"""HTTP-слой опросов: только разбор запроса, вызов сервиса, выбор шаблона."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from auth import keys as keys_auth
from auth.access import current_user_dep, require_user
from database.repositories import answers as answers_repo
from database.session import get_db
from core.deps import render
from core.images import ImageView, UploadedImage, UploadError, drop_empty, to_view as image_view
from core.models import YES_NO_CHOICES, Answer, Question, Survey, User
from surveys import crud, presets, respondent, review, taking
from surveys.builder import FIELD_ERRORS, MAX_CHOICES, MAX_QUESTIONS, build_survey_payload
from core.themes import (
    DEFAULT_FONT,
    DEFAULT_THEME,
    appearance_context,
    google_fonts_link,
    normalize_hex,
    theme_css,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["surveys"])

# Префиксы полей с картинками в конструкторе. Номер блока выводится из
# имени поля, поэтому разбирать приходится здесь, а не в билдере: тот
# работает только с текстом и не должен знать про файлы.
QUESTION_IMAGE_FIELD = "_image"


def _text_form(form) -> dict[str, list[str]]:
    """Только текстовые значения формы.

    С `multipart` в словаре лежат и `UploadFile`, а билдер ждёт строки:
    пробросить файл в нормализацию значило бы отрендерить его в текст
    вопроса при ошибке валидации.

    Значения берутся через `getlist`, а не `multi_items`: тот отдаёт
    пары «имя — значение» по одной, и список вариантов ответа рассыпался
    бы на отдельные символы.
    """
    out: dict[str, list[str]] = {}
    for name in form:
        values = [value for value in form.getlist(name) if isinstance(value, str)]
        if values:
            out[name] = values
    return out


def _question_images(form) -> dict[int, UploadedImage]:
    """Загруженные файлы вопросов: номер блока → файл.

    Ключ — именно номер блока из формы, а не позиция среди непустых
    вопросов: иначе картинка уедет на соседний вопрос, если один из
    блоков окажется пустым.
    """
    images: dict[int, UploadedImage] = {}
    for name, value in form.multi_items():
        if not isinstance(value, UploadedImage) or not name.endswith(QUESTION_IMAGE_FIELD):
            continue
        index_part = name[1: -len(QUESTION_IMAGE_FIELD)]
        if not index_part.isdigit():
            continue
        images[int(index_part)] = drop_empty(value)
    return {index: upload for index, upload in images.items() if upload is not None}


def _kept_images(form) -> dict[int, str]:
    """Картинки, которые остаются на месте: номер блока → имя файла.

    Имя приходит из скрытого поля, поэтому его всё равно приходится
    сверять с тем, что лежит в базе: иначе в форму можно было бы подставить
    чужой путь и сохранить его как картинку вопроса. Блок с отмеченной
    галочкой «убрать» исключается — явное удаление должно побеждать.
    """
    cleared = {
        name[1: -len("_image_clear")]
        for name in form
        if name.endswith("_image_clear") and form.get(name)
    }
    kept: dict[int, str] = {}
    for name, value in form.multi_items():
        if not name.endswith("_image_keep") or not isinstance(value, str):
            continue
        index_part = name[1: -len("_image_keep")]
        if index_part.isdigit() and index_part not in cleared and value.strip():
            kept[int(index_part)] = value.strip()
    return kept


def _upload_errors(exc: UploadError) -> dict[str, str]:
    """Куда в форме поставить текст отказа.

    Ошибка картинки вопроса должна стоять у этого вопроса, иначе создатель
    ищет причину у обложки, которую он и не менял. `question_index` приходит
    из `crud`: он знает, какой блок обрабатывал.
    """
    if exc.question_index is None:
        return {"cover": str(exc)}
    return {f"q{exc.question_index}": str(exc)}


def _not_found(slug: str) -> HTTPException:
    logger.info("Запрошен несуществующий опрос slug=%s", slug)
    return HTTPException(status_code=404)

QUESTION_TYPE_LABELS = (
    ("single", "Один вариант"),
    ("multiple", "Несколько вариантов"),
    ("yes_no", "Да / Нет"),
    ("scale", "Шкала"),
    ("text", "Свободный текст"),
)

SCALE_LABELS = (("1-5", "от 1 до 5"), ("1-10", "от 1 до 10"))

# Словарь нужен там, где тип выводится текстом, а не списком: в заблокированном
# списке вопросов и позже в отчёте по проверке. Сырые ключи вроде `yes_no`
# читались бы как служебные слова.
QUESTION_TYPE_NAMES = dict(QUESTION_TYPE_LABELS)

REVIEW_MODE_LABELS = tuple(review.REVIEW_MODE_LABELS.items())


def _payload_graded(payload: BuildResult) -> int:
    """Сколько вопросов из формы пометят баллом.

    Считается по `BuiltQuestion`, а не по готовым моделям: проверка нужна
    до записи в базу, иначе автор получил бы анкету с режимом проверки,
    где оценивать нечего.
    """
    return sum(
        1
        for question in payload.questions
        if question.type != "text"
        and (
            question.correct_choices
            or question.correct_value_text
            or question.correct_value_int is not None
        )
    )


def _render_form(
    request: Request,
    *,
    status_code: int = 200,
    title: str = "",
    description: str = "",
    review_mode: str = "",
    questions: list[dict[str, str]] | None = None,
    errors: dict[str, str] | None = None,
    cover: ImageView | None = None,
    theme: str | None = None,
    font: str | None = None,
    bg_color: str | None = None,
    accent_color: str | None = None,
    ink_color: str | None = None,
    title_for_preview: str | None = None,
    description_for_preview: str | None = None,
) -> Response:
    """Форма создания анкеты.

    Контекст оформления собирается в `_appearance_context`: та же функция
    отдаёт его редактору, поэтому меню кастомизации не может разъехаться
    между двумя страницами.
    """
    return render(
        request,
        "survey_form.html",
        {
            "title": "Новая анкета",
            "form": {
                "title": title,
                "description": description,
                "review_mode": review_mode,
                "questions": questions or [],
                "theme": theme or DEFAULT_THEME,
                "font": font or DEFAULT_FONT,
            },
            "errors": errors or {},
            "types": QUESTION_TYPE_LABELS,
            "type_names": QUESTION_TYPE_NAMES,
            "scales": SCALE_LABELS,
            "yes_no_labels": YES_NO_CHOICES,
            "max_questions": MAX_QUESTIONS,
            "max_choices": MAX_CHOICES,
            "review_modes": REVIEW_MODE_LABELS,
            "presets": presets.PRESETS,
            "cover": cover,
            "preview_title": title_for_preview or title,
            "preview_description": description_for_preview or description,
            **appearance_context(theme, font, bg_color, accent_color, ink_color),
        },
        status_code=status_code,
    )


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    """Кабинет создателя: только его анкеты.

    Ничего общего у него с респондентами, поэтому и пустой анкетки «на
    всякий случай» здесь не заводится: раньше визит респондента в кабинет
    оставлял в `users` пустую запись.
    """
    rows = crud.list_owner_surveys(session, user)
    surveys = [
        {"survey": survey, "answers_count": answers_count, "questions_count": questions_count}
        for survey, answers_count, questions_count in rows
    ]
    return render(
        request,
        "dashboard.html",
        {
            "title": "Мои анкеты",
            "surveys": surveys,
            "total_surveys": len(surveys),
            "total_answers": sum(item["answers_count"] for item in surveys),
        },
    )


@router.get("/surveys/new")
def new_survey_form(request: Request) -> Response:
    """Пустая форма или форма, заполненная шаблоном.

    Неизвестный ключ игнорируется, а не роняет страницу: ссылка с
    `?preset=` может остаться в истории браузера или в закладках.
    """
    preset = presets.get_preset(request.query_params.get("preset", ""))
    if preset is None:
        return _render_form(request, questions=[{}])
    return _render_form(request, **preset.as_form())


@router.post("/surveys/new")
async def create_survey(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    payload = build_survey_payload(_text_form(form))

    if payload.is_valid and payload.review_mode != "none" and not _payload_graded(payload):
        payload.errors["review_mode"] = review.NOTHING_TO_GRADE

    if not payload.is_valid:
        logger.info("Отказ в создании опроса: %s", list(payload.errors))
        return _render_form(
            request,
            status_code=400,
            title=payload.title,
            description=payload.description,
            review_mode=payload.review_mode,
            questions=payload.raw_blocks or [{}],
            errors=payload.errors,
            theme=form.get("theme"),
            font=form.get("font"),
            **_custom_colors(form),
        )

    try:
        survey, _primary_key = await crud.create_survey(
            session,
            user,
            payload,
            cover=drop_empty(form.get("cover")),
            images=_question_images(form),
            theme=form.get("theme"),
            font=form.get("font"),
            **_custom_colors(form),
        )
    except UploadError as exc:
        logger.info("Картинка анкеты отклонена: %s", exc)
        return _render_form(
            request,
            status_code=400,
            title=payload.title,
            description=payload.description,
            review_mode=payload.review_mode,
            questions=payload.raw_blocks,
            errors=_upload_errors(exc),
            theme=form.get("theme"),
            font=form.get("font"),
            **_custom_colors(form),
        )
    return RedirectResponse(
        url=f"/s/{survey.slug}/edit?created=1", status_code=303
    )
def _custom_colors(form) -> dict[str, str | None]:
    """Свои цвета из формы.

    Отдельная мелочь вместо трёх `form.get` в каждом месте: их легко
    забыть в одном из отказов, и тогда анкета потеряла бы цвет молча —
    человек увидел бы настройку в форме и ничего в готовой анкете.
    """
    return {
        "bg_color": form.get("bg_color"),
        "accent_color": form.get("accent_color"),
        "ink_color": form.get("ink_color"),
    }


def _owner_survey_or_404(
    session: Session,
    slug: str,
    owner: User
) -> Survey:
    survey = crud.get_for_owner(session, slug, owner)
    if survey is None:
        raise _not_found(slug)
    return survey


def _editor_survey_or_404(
    session: Session, slug: str, user: User | None, key: str | None
) -> Survey:
    """Анкета, к которой есть доступ на правку: владелец по cookie или ключ с
    правом редактирования. Всем остальным — 404, чтобы существование slug
    не подтверждалось постороннему."""
    survey = crud.get_by_slug(session, slug)
    if survey is None:
        raise _not_found(slug)
    if not keys_auth.can_edit_survey(session, survey, user, key):
        raise _not_found(slug)
    return survey


def _question_to_dict(question: Question) -> dict[str, object]:
    scale = ""
    if question.scale_min is not None and question.scale_max is not None:
        scale = "%d-%d" % (question.scale_min, question.scale_max)
    return {
        "type": question.type,
        "text": question.text,
        "is_required": question.is_required,
        "choices": "\n".join(choice.text for choice in question.choices),
        "scale": scale or "1-10",
        "image": question.image,
        "correct_choices": [choice.text for choice in question.correct_choices],
        "correct_value_text": question.correct_value_text,
        "correct_value_int": question.correct_value_int,
        "explanation": question.explanation or "",
    }


def _survey_appearance(survey: Survey) -> dict[str, object]:
    """Контекст меню кастомизации по сохранённой анкете."""
    return appearance_context(
        survey.theme,
        survey.font,
        survey.bg_color,
        survey.accent_color,
        survey.ink_color,
    )


def _edit_page(
    request: Request,
    session: Session,
    survey: Survey,
    user: User | None,
    key: str | None,
    *,
    form: dict | None = None,
    errors: dict[str, str] | None = None,
    appearance_error: str | None = None,
    saved: str | None = None,
    status_code: int = 200,
) -> Response:
    """Страница редактора анкеты.

    Один сборщик контекста на все места: обычный вход в редактор, отказ при
    сохранении и отказ при загрузке картинки. Иначе список вопросов и правила
    разбора расходились бы при правке шаблона.
    """
    locked = crud.has_responses(session, survey)
    if form is None:
        form = {
            "title": survey.title,
            "description": survey.description or "",
            "review_mode": survey.review_mode,
            "questions": [
                _question_to_dict(question)
                for question in crud.get_questions(session, survey)
            ],
        }
    if saved is None and request.query_params.get("saved"):
        saved = "Изменения сохранены."
    created = request.query_params.get("created") == "1"
    return render(
        request,
        "survey_edit.html",
        {
            "title": "Редактирование анкеты",
            "survey": survey,
            "locked": locked,
            "is_owner": keys_auth.is_owner(survey, user),
            "key": key,
            "cover": image_view(survey.cover_image),
            "form": form,
            "errors": errors or {},
            "saved": saved,
            "created": created,
            "appearance_error": appearance_error,
            "types": QUESTION_TYPE_LABELS,
            "type_names": QUESTION_TYPE_NAMES,
            "scales": SCALE_LABELS,
            "yes_no_labels": YES_NO_CHOICES,
            "max_questions": MAX_QUESTIONS,
            "max_choices": MAX_CHOICES,
            "review_modes": REVIEW_MODE_LABELS,
            # Название и описание берутся из формы, а не из базы: после
            # неудачного сохранения превью обязано показывать то, что человек
            # уже напечатал, иначе правка выглядит потерянной.
            "preview_title": form.get("title") or survey.title,
            "preview_description": form.get("description") or "",
            **_survey_appearance(survey),
        },
        status_code=status_code,
    )


@router.get("/s/{slug}/edit")
def edit_survey_form(
    request: Request,
    slug: str,
    key: str | None = None,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    survey = _editor_survey_or_404(session, slug, user, key)
    return _edit_page(request, session, survey, user, key)


@router.post("/s/{slug}/edit")
async def update_survey(
    request: Request,
    slug: str,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    form = await request.form()
    key = form.get("key") or None
    survey = _editor_survey_or_404(session, slug, user, key)
    payload = build_survey_payload(_text_form(form))
    locked = crud.has_responses(session, survey)
    is_owner = keys_auth.is_owner(survey, user)

    errors: dict[str, str] = {}
    if not payload.title:
        errors["title"] = FIELD_ERRORS["title"]
    if not locked:
        errors.update(payload.errors)
        graded = _payload_graded(payload)
    else:
        # Вопросы заморожены ответами, поэтому оцениваемость считается по
        # тому, что уже лежит в базе, а не по пришедшей форме.
        graded = review.gradable_count(crud.get_questions(session, survey))

    if payload.review_mode != "none" and not graded:
        errors["review_mode"] = review.NOTHING_TO_GRADE

    if errors:
        return _edit_page(
            request,
            session,
            survey,
            user,
            key,
            form={
                "title": payload.title or survey.title,
                "description": payload.description or survey.description or "",
                "review_mode": payload.review_mode,
                "questions": payload.raw_blocks
                or [_question_to_dict(question) for question in crud.get_questions(session, survey)],
            },
            errors=errors,
            status_code=400,
        )

    try:
        await crud.update_survey(
            session,
            survey,
            title=payload.title,
            description=payload.description,
            # Статус приёма меняет только владелец: ключ с правом правки
            # структуру и тексты правит, но остановить анкету не может.
            is_open=bool(form.get("is_open")) if is_owner else survey.is_open,
            review_mode=payload.review_mode,
            payload=None if locked else payload,
            images=_question_images(form),
            keep_images=_kept_images(form),
        )
    except UploadError as exc:
        logger.info("Картинка вопроса отклонена: %s", exc)
        return _edit_page(
            request,
            session,
            survey,
            user,
            key,
            form={
                "title": payload.title or survey.title,
                "description": payload.description or survey.description or "",
                "questions": payload.raw_blocks,
            },
            errors=_upload_errors(exc),
            status_code=400,
        )
    if is_owner:
        return RedirectResponse(url=f"/dashboard?edited={survey.slug}", status_code=303)
    # Ключ с правом правки не пускает в дашборд, поэтому возвращаем в
    # редактор: оформление анкеты теперь тоже живёт там.
    return RedirectResponse(
        url=f"/s/{survey.slug}/edit?key={key}&saved=1",
        status_code=303,
    )


@router.get("/s/{slug}/settings")
def survey_settings(
    request: Request,
    slug: str,
    key: str | None = None,
    saved: str | None = None,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    survey = _editor_survey_or_404(session, slug, user, key)
    owner = keys_auth.is_owner(survey, user)
    return render(
        request,
        "survey_settings.html",
        {
            "title": "Ключи и ссылки",
            "survey": survey,
            "key": key,
            "is_owner": owner,
            "saved": saved,
            "keys": crud.list_keys(session, survey) if owner else [],
            "primary_key": crud.get_primary_key(session, survey),
            "key_limit_reached": crud.keys_limit_reached(session, survey),
            "max_keys": crud.MAX_KEYS_PER_SURVEY,
            "cover": image_view(survey.cover_image),
            **_survey_appearance(survey),
        },
    )


@router.post("/s/{slug}/cover")
async def upload_cover(
    request: Request,
    slug: str,
    key: str | None = None,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    """Обложка анкеты. Загрузить может владелец или ключ с правом правки:
    оформление — часть содержания анкеты, а не сайта."""
    survey = _editor_survey_or_404(session, slug, user, key)
    form = await request.form()
    error: str | None = None
    try:
        if str(form.get("clear", "")).strip():
            await crud.set_cover(session, survey, None)
        else:
            await crud.set_cover(session, survey, drop_empty(form.get("cover")))
    except UploadError as exc:
        error = str(exc)
        logger.info("Обложка анкеты отклонена: %s", exc)

    return _appearance_result(
        request,
        session,
        survey,
        key,
        user,
        error=error,
        saved="Обложка обновлена." if error is None else None,
    )


@router.post("/s/{slug}/appearance")
async def update_appearance(
    request: Request,
    slug: str,
    key: str | None = None,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    """Тема, шрифт и свои цвета анкеты. Ключи проверяются в `themes.py`,
    поэтому в базу не попадёт мусор из формы."""
    survey = _editor_survey_or_404(session, slug, user, key)
    form = await request.form()
    crud.set_appearance(
        session,
        survey,
        theme=form.get("theme"),
        font=form.get("font"),
        bg_color=form.get("bg_color"),
        accent_color=form.get("accent_color"),
        ink_color=form.get("ink_color"),
    )
    return _appearance_result(
        request, session, survey, key, user, error=None, saved="Оформление обновлено."
    )


def _appearance_result(
    request: Request,
    session: Session,
    survey: Survey,
    key: str | None,
    user: User | None,
    error: str | None,
    saved: str | None = None,
) -> Response:
    """Общий ответ для POST оформления: страница редактора.

    Отдельный редирект здесь означал бы, что ошибка загрузки потеряется
    вместе с введёнными данными. Успех при этом тоже рисуется на месте:
    форма оформления применяется сразу, и страница остаётся редактором.
    """
    return _edit_page(
        request,
        session,
        survey,
        user,
        key,
        appearance_error=error or None,
        saved=saved,
        status_code=400 if error else 200,
    )


@router.post("/s/{slug}/keys")
async def issue_key(
    request: Request,
    slug: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    survey = _owner_survey_or_404(session, slug, user)
    form = await request.form()
    label = crud.normalize_key_label(str(form.get("label", "")))
    can_edit = bool(form.get("can_edit"))
    error = ""

    if not label:
        error = "Придумайте название ключа — по нему его можно отозвать."
    elif crud.is_reserved_label(label):
        error = f"Название «{crud.PRIMARY_KEY_LABEL}» занято основным ключом."
    elif crud.key_label_taken(session, survey, label):
        error = "Ключ с таким названием уже есть. Придумайте другое."
    elif crud.keys_limit_reached(session, survey):
        error = f"Больше {crud.MAX_KEYS_PER_SURVEY} ключей на одну анкету завести нельзя."

    if error:
        return render(
            request,
            "survey_settings.html",
            {
                "title": "Настройки анкеты",
                "survey": survey,
                "key": None,
                "is_owner": True,
                "saved": None,
                "key_error": error,
                "key_label": label,
                "answers_count": answers_repo.count_submitted(session, survey.id),
                "keys": crud.list_keys(session, survey),
                "primary_key": crud.get_primary_key(session, survey),
                "key_limit_reached": crud.keys_limit_reached(session, survey),
                "max_keys": crud.MAX_KEYS_PER_SURVEY,
            },
            status_code=400,
        )

    crud.create_key(session, survey, label, can_edit=can_edit)
    return RedirectResponse(
        url=f"/s/{survey.slug}/settings?key_added=1", status_code=303
    )


@router.post("/s/{slug}/keys/{key_id}/revoke")
def revoke_key(
    slug: str,
    key_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    survey = _owner_survey_or_404(session, slug, user)
    if not crud.revoke_key(session, survey, key_id):
        raise _not_found(slug)
    return RedirectResponse(
        url=f"/s/{survey.slug}/settings?key_revoked=1", status_code=303
    )


@router.post("/s/{slug}/open")
def toggle_open(
    request: Request,
    slug: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    survey = _owner_survey_or_404(session, slug, user)
    crud.set_open(session, survey, not survey.is_open)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/s/{slug}/delete")
def delete_survey_form(
    request: Request,
    slug: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    survey = _owner_survey_or_404(session, slug, user)
    return render(
        request,
        "survey_delete.html",
        {
            "title": "Удаление анкеты",
            "survey": survey,
            "answers_count": answers_repo.count_submitted(session, survey.id),
        },
    )


@router.post("/s/{slug}/delete")
def delete_survey(
    request: Request,
    slug: str,
    user: User = Depends(require_user),
    session: Session = Depends(get_db),
) -> Response:
    survey = _owner_survey_or_404(session, slug, user)
    crud.delete_survey(session, survey)
    return RedirectResponse(url="/dashboard?deleted=1", status_code=303)


@router.get("/s/{slug}")
def take_survey(
    request: Request,
    slug: str,
    session: Session = Depends(get_db),
) -> Response:
    survey = taking.get_public_survey(session, slug)
    if survey is None:
        raise _not_found(slug)
    if not survey.is_open:
        return render(
            request,
            "survey_closed.html",
            {"title": "Анкета закрыта", "survey": survey},
            status_code=403,
        )

    person = respondent.resolve(request)
    own_response = respondent.find_response(session, survey, person)
    own_answers: dict[int, list[Answer]] = {}
    if own_response is not None:
        own_answers = answers_repo.answers_for_responses(session, [own_response.id])

    return render(
        request,
        "survey_public.html",
        {
            "title": survey.title,
            "survey": survey,
            "questions": crud.get_questions(session, survey),
            "cover_image": image_view(survey.cover_image),
            "theme_css": theme_css(
                survey.theme,
                survey.font,
                bg_color=survey.bg_color,
                accent_color=survey.accent_color,
                ink_color=survey.ink_color,
            ),
            "google_fonts_link": google_fonts_link(survey.font),
            "already_answered": own_response is not None,
            "own_response": own_response,
            "own_answers": own_answers.get(own_response.id, []) if own_response else [],
        },
    )


@router.get("/my")
def my_answers(
    request: Request,
    session: Session = Depends(get_db),
) -> Response:
    """Ответы текущего респондента. Без регистрации и без чужих данных."""
    person = respondent.resolve(request)
    rows = respondent.list_answers(session, person)
    items = [
        {"response": row, "answers": answer_rows, "survey": row.survey}
        for row, answer_rows in rows
    ]
    return render(
        request,
        "my_answers.html",
        {"title": "Мои ответы", "items": items, "total": len(items)},
    )


@router.post("/s/{slug}")
async def submit_survey(
    request: Request,
    slug: str,
    session: Session = Depends(get_db),
) -> Response:
    survey = taking.get_public_survey(session, slug)
    if survey is None:
        raise _not_found(slug)
    if not survey.is_open:
        return render(
            request,
            "survey_closed.html",
            {"title": "Анкета закрыта", "survey": survey},
            status_code=403,
        )

    form = await request.form()
    person = respondent.resolve(request)

    try:
        taking.submit_response(
            session,
            survey,
            {key: list(form.getlist(key)) for key in form},
            person.hash,
            person.token,
        )
    except taking.AlreadyRespondedError:
        own_response = respondent.find_response(session, survey, person)
        return render(
            request,
            "survey_already_answered.html",
            {
                "title": "Ответ уже отправлен",
                "survey": survey,
                "own_response": own_response,
            },
            status_code=409,
        )

    return RedirectResponse(url=f"/s/{survey.slug}/thanks", status_code=303)


@router.get("/s/{slug}/thanks")
def thanks_page(
    request: Request,
    slug: str,
    session: Session = Depends(get_db),
) -> Response:
    survey = taking.get_public_survey(session, slug)
    if survey is None:
        raise _not_found(slug)

    context: dict[str, object] = {"title": "Спасибо", "survey": survey}

    # Результат показываем только тому, кто реально отвечал, и только если
    # анкета в режиме проверки. Считаем из сохранённых строк answers, а не
    # из формы: иначе респондент подделал бы себе балл прямо в POST-запросе.
    if survey.reviews_answers:
        person = respondent.resolve(request)
        own_response = respondent.find_response(session, survey, person)
        if own_response is not None:
            answers = answers_repo.answers_for_responses(session, [own_response.id]).get(
                own_response.id, []
            )
            result = review.build_review_for_response(
                crud.get_questions(session, survey), answers, survey.review_mode
            )
            context["review"] = result
            context["review_label"] = review.grade_label(result)

    return render(request, "thanks.html", context)
