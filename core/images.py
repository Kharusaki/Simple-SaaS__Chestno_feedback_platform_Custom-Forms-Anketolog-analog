"""Хранилище картинок: фон, логотип, обложка анкеты, картинка в вопросе.

Правила одинаковы для всех картинок, поэтому модуль один и лежит рядом с
`config.py`: и раздел администратора, и конструктор анкеты грузят через
него, и ни один из них не тянет в себя чужой пакет.

Правила хранения:
- файл кладётся в `media/` под сгенерированным именем, а не под именем
  из формы — иначе в путь попадёт `../` или имя вроде `shell.php`;
- расширение выводится из содержимого (magic bytes), а не из заголовка;
- SVG не принимается: он исполняет JavaScript, а картинки отдаются с
  того же origin, что и страницы.

Ответы респондентов сюда не попадают никогда: это только оформление.
"""

from __future__ import annotations

import logging
import mimetypes
import secrets
from dataclasses import dataclass
from pathlib import Path

from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from .config import settings

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")

_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

ALLOWED_EXTENSIONS = frozenset(_EXTENSIONS.values())


class UploadError(ValueError):
    """Файл не прошёл проверку. Текст сообщения показывается в форме.

    `question_index` — номер блока вопроса, если отклонён именно он, иначе
    `None` для обложки. Без него форма показала бы ошибку у обложки даже
    тогда, когда создатель забыл приложить картинку к третьему вопросу.
    """

    def __init__(self, message: str, question_index: int | None = None) -> None:
        super().__init__(message)
        self.question_index = question_index


# Тип загруженного файла переэкспортируется здесь, а не импортируется в
# `crud` из fastapi напрямую: бизнес-логика принимает файл, но не должна
# знать, откуда он пришёл.
#
# Берётся именно starlette-класс: в `request.form()` лежат его экземпляры,
# а fastapi.UploadFile — лишь подкласс для объявления параметров роутера.
# Проверка `isinstance` по fastapi-классу не нашла бы файл из формы.
UploadedImage = StarletteUploadFile


@dataclass(frozen=True)
class StoredImage:
    filename: str
    content_type: str


@dataclass(frozen=True)
class ImageView:
    url: str
    filename: str


def detect_image_type(head: bytes) -> str | None:
    """Определяет тип по первым байтам. `None` — это не поддерживаемая картинка."""
    for prefix, mime in _MAGIC:
        if head.startswith(prefix):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def to_view(filename: str | None) -> ImageView | None:
    """Картинка для шаблона. Пустое имя — картинки нет."""
    if not filename:
        return None
    return ImageView(url=f"/media/{filename}", filename=filename)


def _target_dir() -> Path:
    media_dir = settings.media_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def delete_image(filename: str | None) -> None:
    """Удаляет файл с диска. Отсутствующий файл — не ошибка."""
    if not filename:
        return
    path = _target_dir() / filename
    if path.is_file():
        path.unlink()
        logger.info("Удалён файл оформления: %s", filename)


def drop_empty(upload: UploadedImage | None) -> UploadedImage | None:
    """Пустой file-input приходит как файл с пустым именем — считаем его «нет»."""
    if upload is not None and not (upload.filename or "").strip():
        return None
    return upload


async def save_image(upload: UploadedImage) -> StoredImage:
    """Проверяет и сохраняет картинку. Возвращает сгенерированное имя файла."""
    data = await upload.read()
    if not data:
        raise UploadError("Файл пустой.")
    if len(data) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes / (1024 * 1024)
        raise UploadError(f"Файл больше {limit_mb:.0f} МБ.")

    content_type = detect_image_type(data)
    if content_type is None:
        raise UploadError(
            "Это не поддерживаемая картинка. Допустимы PNG, JPEG, WebP и GIF."
        )

    filename = f"{secrets.token_hex(12)}{_EXTENSIONS[content_type]}"
    path = _target_dir() / filename
    path.write_bytes(data)
    logger.info("Сохранён файл оформления %s (%s)", filename, content_type)
    return StoredImage(filename=filename, content_type=content_type)


async def replace_image(
    upload: UploadedImage | None, old_filename: str | None
) -> str | None:
    """Кладёт новую картинку и убирает старую. `None` — очистить.

    Старый файл удаляется только после успешной записи нового, иначе
    неудачная загрузка оставила бы поле без картинки.
    """
    if upload is None:
        delete_image(old_filename)
        return None
    stored = await save_image(upload)
    delete_image(old_filename)
    return stored.filename


def resolve_media_path(filename: str) -> Path:
    """Путь к файлу оформления. Имя приходит из URL, поэтому проверяем его
    строго: только имя файла из allowlist-символов, без каталогов."""
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or filename.startswith(".")
    ):
        raise UploadError("Некорректное имя файла.")
    path = settings.media_dir / filename
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise UploadError("Некорректное имя файла.")
    if not path.is_file():
        raise UploadError("Файл не найден.")
    return path


def media_response(path: Path) -> FileResponse:
    """Ответ с картинкой.

    `nosniff` обязателен: без него браузер вправе определить тип по
    содержимому и исполнить файл как скрипт.
    """
    guessed, _ = mimetypes.guess_type(path.name)
    return FileResponse(
        path,
        media_type=guessed or "application/octet-stream",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )
