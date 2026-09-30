"""Сессия аккаунта: подписанная cookie с id пользователя.

Идентификатор раньше был анонимным токеном, который выдавался каждому
посетителю. Теперь в cookie кладётся `id` аккаунта, а сам аккаунт
появляется только через регистрацию или вход, поэтому заход на любую
страницу больше никого не создаёт.

Cookie подписана `itsdangerous` и помечена `httpOnly`, но пароль здесь
не хранится: подпись защищает от подмены, а не от чтения из браузера.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from core.config import settings
from database.repositories import users as users_repo
from core.models import User

logger = logging.getLogger(__name__)

_serializer = URLSafeTimedSerializer(settings.secret_key, salt="user-session")


def _as_utc(moment: datetime) -> datetime:
    """Момент из базы приводим к UTC с часовым поясом.

    SQLite отдаёт `datetime` без пояса, а `utcnow()` кладёт в базу именно UTC.
    Наивное сравнение считало бы одно и то же число разными моментами:
    подпись приходит в UTC с поясом, а запись из базы — голым UTC, и на
    машине в UTC+3 подпись «поехала» бы на три часа.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _whole_second(moment: datetime) -> datetime:
    """Обрезает момент до целой секунды.

    Подпись cookie хранит время с точностью до секунды: микросекунды в ней
    просто отсутствуют. Если сравнивать «как есть», cookie, выданная сразу
    после смены пароля, окажется на те же миллисекунды «раньше» отметки о
    смене — и человека, который только что сменил пароль, выкинуло бы из
    его же собственной сессии. Поэтому обе стороны сравниваются в одних
    единицах. Плата — привязка к секунде: cookie, подписанная в ту же
    секунду, что и смена пароля, остаётся действительной.
    """
    return moment.replace(microsecond=0)


def sign_user_id(user_id: int) -> str:
    return _serializer.dumps(user_id)


def unsign_session(signed: str | None) -> tuple[int, float] | None:
    """`id` аккаунта и момент подписи cookie. None, если подпись не наша."""
    if not signed:
        return None
    try:
        return _serializer.loads(signed, max_age=settings.cookie_max_age, return_timestamp=True)
    except (BadSignature, SignatureExpired):
        return None


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=sign_user_id(user_id),
        max_age=settings.cookie_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.cookie_name, path="/")


def resolve_current_user(request: Request, session: Session) -> User | None:
    """Аккаунт из cookie или None. Только чтение: ничего не создаётся.

    Cookie, подписанная раньше последней смены пароля, не принимается:
    смена пароля должна выкидывать из всех других устройств, иначе украденный
    телефон продолжил бы работать под чужим паролем.
    """
    parsed = unsign_session(request.cookies.get(settings.cookie_name))
    if parsed is None:
        return None
    user_id, signed_at = parsed

    user = users_repo.get_by_id(session, user_id)
    if user is None:
        return None

    changed_at = user.password_changed_at
    if changed_at is not None and signed_at < _whole_second(_as_utc(changed_at)):
        logger.info("Cookie старше смены пароля, username=%s", user.username)
        return None

    return user
