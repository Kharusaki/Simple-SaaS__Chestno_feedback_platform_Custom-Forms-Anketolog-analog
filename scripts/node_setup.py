"""Установка Node.js для туннеля LocalTunnel.

Зачем это здесь, а не отдельным install_node.ps1: PowerShell 5.1 читает .ps1
в системной кодировке, поэтому в нём можно только ASCII — а человеку перед
загрузкой нужны нормальные слова. В Python это просто работает, и ту же
логику видно в тестах.

Установка добрая: официальный zip с nodejs.org, без прав администратора,
без изменения PATH. Node ищется сначала в PATH, потом в папке установки —
ровно как уже сделано для cloudflared.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DIST = "https://nodejs.org/dist"
INDEX_URL = f"{DIST}/index.json"

# Подставная версия на случай, если список релизов недоступен. Актуальная
# берётся из index.json: зашитая в коде дата устареет молча и незаметно.
FALLBACK_VERSION = "v24.21.0"

# Настоящий архив весит около 38 МБ. Меньше — почти наверняка не он:
# прокси или мобильный оператор отдаёт вместо архива HTML-заглушку.
MIN_ARCHIVE_BYTES = 20 * 1024 * 1024

DOWNLOAD_TIMEOUT = 300


class NodeSetupError(RuntimeError):
    """Node.js поставить не удалось."""


def install_dir() -> Path:
    """Куда ставится Node.js, если его нет в системе."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / "Programs" / "NodeTunnel"


def arch_suffix() -> str:
    """Имя архива для текущей архитектуры: arm64 или amd64."""
    if platform.machine().lower() in {"arm64", "aarch64"}:
        return "arm64"
    return "x64"


def archive_name(version: str) -> str:
    return f"node-{version}-win-{arch_suffix()}.zip"


def _npx_candidates() -> list[Path]:
    target = install_dir()
    if os.name == "nt":
        return [target / "npx.cmd", target / "npx"]
    return [target / "npx"]


def npx_in_install() -> Path | None:
    """Путь к npx в нашей папке установки, если он там есть."""
    for candidate in _npx_candidates():
        if candidate.is_file():
            return candidate
    return None


def find_npx() -> Path | None:
    """Ищет npx: сначала в PATH, потом в папке установки проекта."""
    found = shutil.which("npx")
    if found:
        return Path(found)
    return npx_in_install()


def npx_works(executable: Path) -> bool:
    """Проверяет, что npx действительно запускается."""
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def is_ready() -> bool:
    """Готова ли среда для туннеля: npx найден и запускается."""
    executable = find_npx()
    return executable is not None and npx_works(executable)


def _fetch(url: str) -> bytes:
    context = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": "mini-surveys-setup"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT, context=context) as response:
        return response.read()


def latest_lts_version() -> str | None:
    """Самая свежая LTS-версия с архивом под нашу архитектуру."""
    try:
        data = json.loads(_fetch(INDEX_URL))
    except (urllib.error.URLError, TimeoutError, ValueError, ssl.SSLError, OSError):
        return None
    if not isinstance(data, list):
        return None
    wanted = f"win-{arch_suffix()}-zip"
    for release in data:
        version = release.get("version")
        files = release.get("files") or []
        if version and release.get("lts") and wanted in files:
            return str(version)
    return None


def expected_sha256(version: str, name: str) -> str | None:
    """Хеш архива из официального списка либо None, если список недоступен."""
    try:
        listing = _fetch(f"{DIST}/{version}/SHASUMS256.txt").decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError):
        return None
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def _extract(archive: bytes, destination: Path) -> Path:
    """Распаковывает архив и возвращает папку с самим Node.js.

    Внутри zip лежит папка `node-vX-win-x64`, поэтому она поднимается на
    уровень выше: иначе путь к npx зависел бы от номера версии.
    """
    staging = destination.with_name(destination.name + "-new")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            bundle.extractall(staging)
        roots = [item for item in staging.iterdir() if item.is_dir()]
        if len(roots) != 1:
            raise NodeSetupError("внутри архива не одна папка, структура неожиданная")
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        roots[0].replace(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


def install_node(notify=lambda line: None) -> Path:
    """Ставит Node.js, если его нет, и возвращает путь к npx.

    `notify` получает человеческие строки для окна запуска: сам модуль о
    пользователе ничего не знает.
    """
    existing = find_npx()
    if existing is not None and npx_works(existing):
        return existing

    version = latest_lts_version() or FALLBACK_VERSION
    name = archive_name(version)
    target = install_dir()

    notify(f"Качаю Node.js {version} с официального сайта, около 38 МБ...")
    try:
        payload = _fetch(f"{DIST}/{version}/{name}")
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as error:
        raise NodeSetupError(f"не удалось скачать Node.js: {error}") from error

    if len(payload) < MIN_ARCHIVE_BYTES:
        got = len(payload)
        size = f"{got} байт" if got < 1024 else f"{round(got / 1024)} КБ"
        raise NodeSetupError(
            f"скачалось всего {size} — это не архив Node.js. "
            "Похоже, соединение подменяет файл. Попробуйте другую сеть."
        )

    digest = hashlib.sha256(payload).hexdigest()
    wanted = expected_sha256(version, name)
    if wanted is None:
        notify("Не удалось сверить архив с официальным списком, ставлю как есть.")
    elif digest != wanted:
        raise NodeSetupError(
            "не совпала контрольная сумма архива. Файл не был установлен."
        )

    notify("Распаковываю Node.js...")
    try:
        _extract(payload, target)
    except (OSError, ValueError, zipfile.BadZipFile, NodeSetupError) as error:
        raise NodeSetupError(f"не удалось распаковать Node.js: {error}") from error

    installed = npx_in_install()
    if installed is None or not npx_works(installed):
        raise NodeSetupError("Node.js распаковался, но npx не запускается")

    notify("Node.js установлен.")
    return installed
