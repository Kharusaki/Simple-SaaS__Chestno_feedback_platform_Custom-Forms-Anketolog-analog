"""Идентичность респондента и его собственные ответы.

Два идентификатора, оба хранятся в `responses`:
- `respondent_token` — случайный токен в httpOnly-cookie. Стабилен при смене
  IP, User-Agent или браузера на том же устройстве, поэтому это основной путь;
- `respondent_hash` — sha256 от IP и User-Agent. Живёт, когда cookie не дошла
  (первый визит, блокировка, другой браузер), и продолжает защищать от
  повторной отправки.

Cookie ставится всегда: без неё «Мои ответы» нечего показать.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from core.config import settings
from database.repositories import answers as answers_repo
from core.models import Answer, Response, Survey
from core.utils import client_ip, respondent_hash

logger = logging.getLogger(__name__)

NEW_COOKIE_ATTR = "new_respondent_token"
TOKEN_LENGTH = 32

ALLOWED_TOKEN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


@dataclass(frozen=True)
class Respondent:
    """Кто сейчас зашёл на публичную страницу."""

    token: str
    hash: str


def _clean_cookie_token(raw: str | None) -> str | None:
    """Cookie приходит от клиента: пропускаем только ожидаемые символы."""
    if not raw or len(raw) > 64:
        return None
    if not set(raw) <= ALLOWED_TOKEN_CHARS:
        return None
    return raw


def resolve(request: Request) -> Respondent:
    """Текущий респондент. Новый токен кладётся в `request.state`,
    cookie выставляет `deps.render` — как и у владельца."""
    token = _clean_cookie_token(request.cookies.get(settings.respondent_cookie_name))
    if token is None:
        token = secrets.token_urlsafe(TOKEN_LENGTH)
        setattr(request.state, NEW_COOKIE_ATTR, token)
        logger.info("Выдан новый токен респондента")
    return Respondent(
        token=token,
        hash=respondent_hash(client_ip(request), request.headers.get("user-agent")),
    )


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        key=settings.respondent_cookie_name,
        value=token,
        max_age=settings.respondent_cookie_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


def find_response(
    session: Session, survey: Survey, respondent: Respondent
) -> Response | None:
    """Ответ этого респондента по опросу, если он уже отправлял."""
    return answers_repo.find_submitted_for_respondent(
        session, survey.id, respondent.token, respondent.hash
    )


def has_response(session: Session, respondent: Respondent) -> bool:
    """Есть ли у респондента хоть один завершённый ответ."""
    return bool(
        answers_repo.list_submitted_for_respondent(session, respondent.token, respondent.hash)
    )


def list_answers(session: Session, respondent: Respondent) -> list[tuple[Response, list[Answer]]]:
    """Все ответы респондента с их вариантами, свежие сверху."""
    responses = answers_repo.list_submitted_for_respondent(
        session, respondent.token, respondent.hash
    )
    if not responses:
        return []
    grouped = answers_repo.answers_for_responses(session, [row.id for row in responses])
    return [(row, grouped.get(row.id, [])) for row in responses]
