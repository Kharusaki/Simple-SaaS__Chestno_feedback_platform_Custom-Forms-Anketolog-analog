from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from .config import BASE_DIR, settings
from surveys import respondent as respondent_service

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def common_context(request: Request) -> dict[str, Any]:
    """Данные, нужные на каждой странице: адрес, имя приложения, оформление,
    кто вошёл.

    Фон и логотип подтягиваются здесь, чтобы `base.html` не знал про БД.
    Настройки лежат в единственной строке, поэтому это один дешёвый запрос.
    """
    from auth.access import current_user
    from database.repositories import site_settings as settings_repo
    from database.session import SessionLocal
    from .models import ROLE_ADMIN

    background = logo = None
    with SessionLocal() as session:
        row = settings_repo.get(session)
        if row is not None:
            background = row.background_image
            logo = row.logo_image
        user = current_user(request, session)

    return {
        "base_url": str(request.base_url).rstrip("/"),
        "app_name": settings.app_name,
        "background_image": f"/media/{background}" if background else None,
        "logo_image": f"/media/{logo}" if logo else None,
        "current_user": user,
        "is_admin": user is not None and user.role == ROLE_ADMIN,
        "is_logged_in": user is not None,
        "show_password_notice": _password_notice(request, user),
    }


def _password_notice(request: Request, user) -> bool:
    """Показывать ли окно «пароль стандартный».

    Показываем, только если учётка просит смены и пользователь не выбрал
    «продолжить» в этом браузере: без cookie кнопка «продолжить как есть»
    просто возвращала бы то же окно на следующей же странице.
    """
    if user is None or not user.must_change_password:
        return False
    return not request.cookies.get(settings.password_notice_cookie_name)


def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение: plural(2, 'вопрос', 'вопроса', 'вопросов') → '2 вопроса'."""
    tail_100 = count % 100
    if tail_100 < 11 or tail_100 > 14:
        tail_10 = count % 10
        if tail_10 == 1:
            return f"{count} {one}"
        if 2 <= tail_10 <= 4:
            return f"{count} {few}"
    return f"{count} {many}"


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[common_context]
)
templates.env.filters["plural"] = plural
templates.env.globals["max_text_length"] = 2000


def apply_respondent_cookie(request: Request, response: Response) -> Response:
    """Проставляет cookie респондента, если он зашёл впервые.

    Идёт на любую страницу: без респондентской cookie «Мои ответы» не на чем
    показать.
    """
    new_token = getattr(request.state, respondent_service.NEW_COOKIE_ATTR, None)
    if new_token:
        respondent_service.set_cookie(response, new_token)
    return response


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    """Единая точка рендера HTML: шаблон + cookie нового респондента.

    Cookie владельца больше не нужна: вход держится в сессии, которую ставит
    `auth/session.py` при входе и сбрасывает при выходе.
    """
    response = templates.TemplateResponse(
        request, name, context or {}, status_code=status_code
    )
    return apply_respondent_cookie(request, response)
