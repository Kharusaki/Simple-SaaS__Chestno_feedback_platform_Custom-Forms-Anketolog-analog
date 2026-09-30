"""Правила аккаунтов: проверка формы, регистрация, вход, пароль.

HTTP-слой сюда не заглядывает, а репозиторий ничего не знает про пароли.
Логины сравниваются без учёта регистра: «Admin» и «admin» — один и тот
же человек, иначе опечатка в регистре выглядела бы как чужой аккаунт.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from analytics import cache as stats_cache
from auth import passwords
from core.config import settings
from core.images import delete_image
from core.models import ROLE_ADMIN, Question, Response, Survey, User
from core.utils import utcnow
from database.repositories import surveys as surveys_repo
from database.repositories import users as users_repo

logger = logging.getLogger(__name__)

MIN_USERNAME_LENGTH = 3
MAX_USERNAME_LENGTH = 64

USERNAME_HINT = (
    "Логин: от 3 до 64 символов, латиница, цифры, «_», «-», точка."
)
USERNAME_FORBIDDEN = ("admin",)


def normalize_username(raw: str) -> str:
    return raw.strip().casefold()


def _username_error(username: str) -> str | None:
    if len(username) < MIN_USERNAME_LENGTH:
        return f"Логин слишком короткий, минимум {MIN_USERNAME_LENGTH} символа."
    if len(username) > MAX_USERNAME_LENGTH:
        return f"Логин длиннее {MAX_USERNAME_LENGTH} символов."
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_.-")
    if set(username) - allowed:
        return "В логине допустимы латиница, цифры, «_», «-» и точка."
    if username in USERNAME_FORBIDDEN:
        return "Этот логин зарезервирован под администратора."
    return None


def validate_registration(
    session: Session, *, username: str, password: str, password_repeat: str
) -> dict[str, str]:
    """Ошибки формы регистрации. Пустой словарь — всё в порядке."""
    errors: dict[str, str] = {}
    name = normalize_username(username)

    problem = _username_error(name)
    if problem:
        errors["username"] = problem
    elif users_repo.username_exists(session, name):
        errors["username"] = "Такой логин уже занят."

    password_problem = passwords.check_password_length(password)
    if password_problem:
        errors["password"] = password_problem
    if password and password != password_repeat:
        errors["password_repeat"] = "Пароли не совпадают."

    return errors


def register_creator(session: Session, username: str, password: str) -> User:
    user = users_repo.create_creator(
        session, normalize_username(username), passwords.hash_password(password)
    )
    session.commit()
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = users_repo.get_by_username(session, normalize_username(username))
    if user is None:
        # Хеш всё равно считаем: иначе по времени ответа видно, существует
        # ли такой логин.
        passwords.verify_password(password, _DUMMY_HASH)
        return None
    if not passwords.verify_password(password, user.password_hash):
        return None
    return user


_DUMMY_HASH = passwords.hash_password("dummy-password-for-timing")


def validate_password_change(
    user: User, *, current_password: str, password: str, password_repeat: str
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not passwords.verify_password(current_password, user.password_hash):
        errors["current_password"] = "Текущий пароль не совпадает."

    problem = passwords.check_password_length(password)
    if problem:
        errors["password"] = problem
    if password and password != password_repeat:
        errors["password_repeat"] = "Пароли не совпадают."
    if password and password == current_password:
        errors["password"] = "Новый пароль совпадает с текущим."
    return errors


def apply_new_password(session: Session, user: User, password: str) -> None:
    user.password_hash = passwords.hash_password(password)
    user.must_change_password = False
    # Отмечаем момент смены: cookie, подписанные раньше, перестают пускать.
    user.password_changed_at = utcnow()
    session.commit()


def dismiss_password_warning(session: Session, user: User) -> None:
    user.must_change_password = False
    session.commit()


def ensure_admin_account(session: Session) -> User | None:
    """Создаёт девелоперскую учётку, если администратора ещё нет.

    Логин и пароль берутся из настроек и по умолчанию равны `admin`/`admin`.
    Пароль известен всем, поэтому учётка помечается флагом
    `must_change_password`: при входе показывается предупреждение со
    ссылкой на смену пароля, но продолжить можно и без неё.

    Существующего администратора не трогаем: если пароль уже сменили,
    повторный запуск не должен его затирать.
    """
    existing = users_repo.get_admin(session)
    if existing is not None:
        return None

    try:
        password_hash = passwords.hash_password(
            settings.admin_password, enforce_length=False
        )
    except ValueError:
        logger.error("ADMIN_PASSWORD пустой, учётка не создана.")
        return None

    user = User(
        username=normalize_username(settings.admin_username),
        password_hash=password_hash,
        role=ROLE_ADMIN,
        must_change_password=True,
    )
    session.add(user)
    session.commit()
    logger.warning(
        "Создана девелоперская учётка «%s» с паролем по умолчанию. "
        "Смените пароль в разделе /account/password.",
        user.username,
    )
    return user


def _collect_survey_images(session: Session, survey: Survey) -> list[str]:
    """Имена файлов оформления анкеты: обложка и картинки вопросов.

    Собираются до удаления записей: после `commit` полей уже нет, а
    `ON DELETE CASCADE` про файлы в `media/` не знает.
    """
    files = [survey.cover_image]
    files.extend(
        question.image
        for question in session.scalars(
            select(Question).where(Question.survey_id == survey.id)
        )
    )
    return [name for name in files if name]


def delete_all_surveys(session: Session, user: User) -> int:
    """Удаляет все анкеты пользователя вместе с ответами и файлами.

    Возвращает число удалённых анкет. Файлы оформления снимаются после
    `commit`, когда записей уже нет, но имена собраны заранее.
    """
    surveys = surveys_repo.list_by_owner(session, user.id)
    if not surveys:
        return 0

    files: list[str] = []
    for survey in surveys:
        files.extend(_collect_survey_images(session, survey))
        stats_cache.invalidate(survey.slug)

    for survey in surveys:
        session.delete(survey)
    session.commit()

    for filename in files:
        delete_image(filename)

    logger.info("Удалены все анкеты пользователя username=%s count=%d", user.username, len(surveys))
    return len(surveys)


def revoke_all_responses(session: Session, user: User) -> int:
    """Удаляет все ответы на анкеты пользователя, оставляя сами анкеты.

    Анкеты, вопросы, варианты и ключи доступа остаются: отзыв ответов
    обнуляет статистику, но не ломает собранные анкеты.
    """
    surveys = surveys_repo.list_by_owner(session, user.id)
    if not surveys:
        return 0

    survey_ids = [survey.id for survey in surveys]
    responses = list(
        session.scalars(select(Response).where(Response.survey_id.in_(survey_ids)))
    )
    if not responses:
        return 0

    for response in responses:
        session.delete(response)
    session.commit()

    for survey in surveys:
        stats_cache.invalidate(survey.slug)

    logger.info(
        "Отозваны все ответы пользователя username=%s surveys=%d responses=%d",
        user.username,
        len(surveys),
        len(responses),
    )
    return len(responses)


def delete_account(session: Session, user: User) -> None:
    """Удаляет аккаунт и все его анкеты вместе с ответами и файлами.

    Каскад в базе убирает вопросы, варианты, ответы и ключи, но файлы
    оформления в `media/` остаются — их снимаем вручную до удаления.
    """
    username = user.username
    surveys = surveys_repo.list_by_owner(session, user.id)

    files: list[str] = []
    for survey in surveys:
        files.extend(_collect_survey_images(session, survey))
        stats_cache.invalidate(survey.slug)

    session.delete(user)
    session.commit()

    for filename in files:
        delete_image(filename)

    logger.info("Удалён аккаунт username=%s surveys=%d", username, len(surveys))
