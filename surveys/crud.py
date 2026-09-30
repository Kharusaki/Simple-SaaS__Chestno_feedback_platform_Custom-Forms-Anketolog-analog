"""Бизнес-логика опросов. Не знает про HTTP: только сессия БД и данные."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from analytics import cache as stats_cache
from database.repositories import answers as answers_repo
from database.repositories import keys as keys_repo
from database.repositories import questions as questions_repo
from database.repositories import surveys as surveys_repo
from core.images import UploadedImage, UploadError, delete_image, replace_image, save_image
from core.models import Question, Survey, SurveyKey, User
from surveys.builder import BuildResult, BuiltQuestion
from core.themes import (
    DEFAULT_FONT,
    DEFAULT_THEME,
    is_valid_font,
    is_valid_theme,
    normalize_hex,
)
from core.utils import MAX_SLUG_ATTEMPTS, generate_key_token, generate_slug

logger = logging.getLogger(__name__)

PRIMARY_KEY_LABEL = "основной доступ"
MAX_KEYS_PER_SURVEY = 20
MAX_KEY_LABEL_LENGTH = 60


class SlugUnavailableError(RuntimeError):
    """Не удалось подобрать уникальный slug за MAX_SLUG_ATTEMPTS попыток."""


def _unique_slug(session: Session) -> str:
    for _ in range(MAX_SLUG_ATTEMPTS):
        candidate = generate_slug()
        if not surveys_repo.slug_exists(session, candidate):
            return candidate
    raise SlugUnavailableError(
        "Не удалось сгенерировать уникальную ссылку за %d попыток" % MAX_SLUG_ATTEMPTS
    )


def _create_primary_key(session: Session, survey: Survey) -> SurveyKey:
    return keys_repo.add(
        session,
        SurveyKey(
            survey_id=survey.id,
            token=generate_key_token(48),
            label=PRIMARY_KEY_LABEL,
            can_edit=True,
        ),
    )


async def create_survey(
    session: Session,
    owner: User,
    payload: BuildResult,
    *,
    cover: UploadedImage | None = None,
    images: dict[int, UploadedImage] | None = None,
    theme: str | None = None,
    font: str | None = None,
    bg_color: str | None = None,
    ink_color: str | None = None,
    accent_color: str | None = None,
) -> tuple[Survey, SurveyKey]:
    """Создаёт опрос, его вопросы, варианты и основной ключ доступа — атомарно.

    `images` — загруженные картинки вопросов по номеру блока из формы.
    Файлы кладутся на диск до `commit`: если что-то отклонено, анкеты
    вообще не будет, а лишние файлы убирает откат ниже. Обложка в этом
    списке тоже участвует — она сохраняется первой, и падение на
    втором вопросе не должно оставлять её на диске.
    """
    survey = Survey(
        slug=_unique_slug(session),
        owner_id=owner.id,
        title=payload.title,
        description=payload.description or None,
        review_mode=payload.review_mode,
    )
    # Оформление выбирается уже в форме создания, поэтому ключи проверяются
    # теми же словарями, что и при правке готовой анкеты: мусор из формы в
    # базу не попадает.
    if is_valid_theme(theme):
        survey.theme = str(theme)
    if is_valid_font(font):
        survey.font = str(font)
    survey.bg_color = normalize_hex(bg_color)
    survey.ink_color = normalize_hex(ink_color)
    survey.accent_color = normalize_hex(accent_color)
    surveys_repo.add(session, survey)

    stored_images: list[str] = []
    try:
        survey.cover_image = await replace_image(cover, None)
        if survey.cover_image:
            stored_images.append(survey.cover_image)
        for block_index, built in enumerate(payload.questions):
            await _add_question(
                session,
                survey,
                built,
                (images or {}).get(block_index),
                stored_images,
                block_index=block_index,
            )
    except UploadError:
        for filename in stored_images:
            delete_image(filename)
        session.rollback()
        raise

    primary_key = _create_primary_key(session, survey)
    session.commit()

    logger.info(
        "Создан опрос slug=%s вопросов=%d owner=%s",
        survey.slug,
        len(payload.questions),
        owner.id,
    )
    return survey, primary_key


async def _add_question(
    session: Session,
    survey: Survey,
    built: BuiltQuestion,
    upload: UploadedImage | None = None,
    stored: list[str] | None = None,
    block_index: int | None = None,
) -> Question:
    question = questions_repo.add(
        session,
        Question(
            survey_id=survey.id,
            position=built.position,
            text=built.text,
            type=built.type,
            is_required=built.is_required,
            scale_min=built.scale_min,
            scale_max=built.scale_max,
            explanation=built.explanation or None,
            correct_value_text=built.correct_value_text,
            correct_value_int=built.correct_value_int,
        ),
    )
    if upload is not None and (upload.filename or "").strip():
        try:
            saved = await save_image(upload)
        except UploadError as exc:
            # Номер блока нужен форме, чтобы показать ошибку у того вопроса,
            # к которому относится картинка, а не у обложки.
            raise UploadError(
                str(exc), question_index=block_index if block_index is not None else built.position
            ) from exc
        question.image = saved.filename
        if stored is not None:
            stored.append(saved.filename)
    if built.choices:
        questions_repo.add_choices(
            session, question, built.choices, set(built.correct_choices)
        )
    return question


def get_for_owner(session: Session, slug: str, owner: User) -> Survey | None:
    survey = surveys_repo.get_by_slug(session, slug)
    if survey is None or survey.owner_id != owner.id:
        return None
    return survey


def get_by_slug(session: Session, slug: str) -> Survey | None:
    return surveys_repo.get_by_slug(session, slug)


def list_owner_surveys(
    session: Session, owner: User
) -> list[tuple[Survey, int, int]]:
    """Мои опросы вместе с числом ответов и вопросов — для дашборда."""
    surveys = surveys_repo.list_by_owner(session, owner.id)
    counts = surveys_repo.response_counts(session, [survey.id for survey in surveys])
    rows = [
        (survey, counts.get(survey.id, 0), questions_repo.count_for_survey(session, survey.id))
        for survey in surveys
    ]
    return rows


def get_questions(session: Session, survey: Survey) -> list[Question]:
    return questions_repo.list_for_survey(session, survey.id)


def get_primary_key(session: Session, survey: Survey) -> SurveyKey | None:
    """Основной ключ опознаётся по зарезервированной метке, а не по `can_edit`:
    дополнительный ключ с правом правки отзывается так же, как обычный."""
    return keys_repo.find_by_label(session, survey.id, PRIMARY_KEY_LABEL)


def is_primary_key(key: SurveyKey | None) -> bool:
    return key is not None and key.label == PRIMARY_KEY_LABEL


def list_keys(session: Session, survey: Survey) -> list[SurveyKey]:
    return keys_repo.list_for_survey(session, survey.id)


def normalize_key_label(raw: str) -> str:
    return " ".join(raw.split())[:MAX_KEY_LABEL_LENGTH].strip()


def is_reserved_label(label: str) -> bool:
    return label.casefold() == PRIMARY_KEY_LABEL.casefold()


def key_label_taken(session: Session, survey: Survey, label: str) -> bool:
    return any(key.label == label for key in keys_repo.list_for_survey(session, survey.id))


def keys_limit_reached(session: Session, survey: Survey) -> bool:
    return len(keys_repo.list_for_survey(session, survey.id)) >= MAX_KEYS_PER_SURVEY


def create_key(
    session: Session, survey: Survey, label: str, *, can_edit: bool = False
) -> SurveyKey:
    """Выдаёт дополнительный ключ. `can_edit=True` открывает ещё и правку анкеты.

    Дополнительный ключ с правом правки полностью равноправен основному по
    возможностям, но остаётся отзываемым."""
    if is_reserved_label(label):
        raise ValueError(f"Метка {PRIMARY_KEY_LABEL!r} зарезервирована под основной ключ")
    key = keys_repo.add(
        session,
        SurveyKey(
            survey_id=survey.id,
            token=generate_key_token(48),
            label=label,
            can_edit=can_edit,
        ),
    )
    session.commit()
    logger.info("Выдан ключ id=%s slug=%s can_edit=%s", key.id, survey.slug, can_edit)
    return key


def revoke_key(session: Session, survey: Survey, key_id: int) -> bool:
    """Отзывает дополнительный ключ. Основной ключ трогать нельзя: по нему
    владелец возвращается к анкете, даже если потерял cookie."""
    key = keys_repo.get_by_id(session, key_id)
    if key is None or key.survey_id != survey.id or is_primary_key(key):
        return False
    keys_repo.delete(session, key)
    session.commit()
    logger.info("Отозван ключ id=%s slug=%s", key_id, survey.slug)
    return True


def has_responses(session: Session, survey: Survey) -> bool:
    """Есть ли ответы. После первого ответа структуру вопросов не трогаем:
    иначе накопленная статистика перестанет соответствовать вопросам."""
    return bool(answers_repo.count_submitted(session, survey.id))


def set_open(session: Session, survey: Survey, is_open: bool) -> Survey:
    survey.is_open = is_open
    session.commit()
    logger.info("Приём ответов %s slug=%s", "открыт" if is_open else "остановлен", survey.slug)
    return survey


async def update_survey(
    session: Session,
    survey: Survey,
    *,
    title: str,
    description: str,
    is_open: bool,
    review_mode: str | None = None,
    payload: BuildResult | None = None,
    images: dict[int, UploadedImage] | None = None,
    keep_images: dict[int, str] | None = None,
) -> Survey:
    """Обновляет анкету. Вопросы пересоздаются, только если `payload` задан
    и ответов ещё не было.

    Картинки вопросов при пересборке надо забрать у старых вопросов: иначе
    после удаления их записей файлы осиротеют в `media/` навсегда. Файлы,
    которые остались в форме без новой загрузки, переносим по номеру блока
    через `keep_images`, иначе правка анкеты стирала бы картинки молча.

    `review_mode` меняется всегда, даже когда вопросы заморожены ответами:
    режим относится к анкете, а не к отдельному вопросу.
    """
    survey.title = title
    survey.description = description or None
    survey.is_open = is_open
    if review_mode is not None:
        survey.review_mode = review_mode

    if payload is not None and not has_responses(session, survey):
        previous = get_questions(session, survey)
        old_images = [question.image for question in previous if question.image]
        old_files = set(old_images)
        questions_repo.delete_for_survey(session, survey.id)
        carried = keep_images or {}
        stored: list[str] = []
        try:
            for block_index, built in enumerate(payload.questions):
                await _add_question(
                    session,
                    survey,
                    built,
                    (images or {}).get(block_index),
                    stored,
                    block_index=block_index,
                )
        except UploadError:
            for filename in stored:
                delete_image(filename)
            # Вопросы уже удалены в этой транзакции, поэтому откат
            # обязателен: иначе анкета осталась бы без вопросов.
            session.rollback()
            raise

        # Картинка без новой загрузки переезжает из старого блока с тем же
        # номером, иначе правка анкеты молча стирала бы оформление вопроса.
        # Имя сверяется со списком файлов этой анкеты: скрытое поле формы
        # приходит от клиента и содержению базы доверия нет.
        new_questions = get_questions(session, survey)
        for block_index, question in enumerate(new_questions):
            inherited = carried.get(block_index)
            if question.image is None and inherited in old_files:
                question.image = inherited

        survivors = {question.image for question in new_questions if question.image}
        for filename in old_images:
            if filename not in survivors:
                delete_image(filename)

    session.commit()
    stats_cache.invalidate(survey.slug)
    logger.info(
        "Анкета обновлена slug=%s вопросы=%s",
        survey.slug,
        "заменены" if payload is not None else "не тронуты",
    )
    return survey


async def set_cover(
    session: Session, survey: Survey, upload: UploadedImage | None
) -> Survey:
    """Меняет обложку анкеты. `upload is None` — убрать обложку.

    Отдельный метод, а не поле в `update_survey`: картинка приходит
    отдельным `multipart` и не должна ломать сохранение текстов, если
    файл не прошёл проверку.
    """
    survey.cover_image = await replace_image(upload, survey.cover_image)
    session.commit()
    logger.info("Обложка анкеты обновлена slug=%s", survey.slug)
    return survey


def set_appearance(
    session: Session,
    survey: Survey,
    *,
    theme: str | None,
    font: str | None,
    bg_color: str | None = None,
    accent_color: str | None = None,
    ink_color: str | None = None,
) -> Survey:
    """Меняет оформление анкеты: тему, шрифт и свои цвета.

    Ключи тем и шрифтов проверяются по словарю из `themes.py`: в базу
    попадает только то, что действительно есть, иначе чужой или стёртый
    ключ сделал бы страницу нечитаемой.

    Цвета нормализуются мягче: пустое значение снимает переопределение и
    возвращает цвет темы, а неразборчивый мусор — тоже. Иначе человек,
    вписавший в поле «цвет» слово, получил бы навсегда нечитаемую анкету
    без объяснения, что именно он сделал не так.
    """
    if is_valid_theme(theme):
        survey.theme = str(theme)
    if is_valid_font(font):
        survey.font = str(font)
    for field, value in (
        ("bg_color", bg_color),
        ("accent_color", accent_color),
        ("ink_color", ink_color),
    ):
        setattr(survey, field, normalize_hex(value))
    session.commit()
    logger.info("Оформление анкеты изменено slug=%s", survey.slug)
    return survey


def delete_survey(session: Session, survey: Survey) -> None:
    """Удаляет анкету целиком: вопросы, варианты, ответы и ключи.

    Файлы оформления снимаем до удаления записи: после `commit` полей уже
    нет, а `ON DELETE CASCADE` про файлы в `media/` не знает, и картинки
    остались бы навсегда. Обложка и картинки вопросов убираются вместе.
    """
    slug = survey.slug
    files = [survey.cover_image]
    files.extend(question.image for question in get_questions(session, survey))
    surveys_repo.delete(session, survey)
    session.commit()
    for filename in files:
        delete_image(filename)
    stats_cache.invalidate(slug)
    logger.info("Анкета удалена slug=%s", slug)
