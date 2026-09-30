from __future__ import annotations

import logging
import secrets
import string
from datetime import datetime, timezone
from hashlib import sha256

from .config import settings

logger = logging.getLogger(__name__)

SLUG_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
TOKEN_ALPHABET = string.ascii_letters + string.digits
MAX_SLUG_ATTEMPTS = 12


def generate_slug(length: int | None = None) -> str:
    """Короткий читаемый идентификатор опроса: буквы без похожих 0/o/1/l."""
    size = length or settings.slug_length
    return "".join(secrets.choice(SLUG_ALPHABET) for _ in range(size))


def generate_token(length: int = 32) -> str:
    return "".join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def generate_key_token(length: int = 48) -> str:
    return generate_token(length)


def constant_time_equals(left: str, right: str) -> bool:
    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def respondent_hash(ip: str | None, user_agent: str | None) -> str:
    """Стабильный псевдоним респондента для защиты от повторной отправки.
    Хранится только необратимый хеш, не IP."""
    raw = f"{ip or '-'}|{(user_agent or '-')[:200]}|{settings.secret_key}"
    return sha256(raw.encode("utf-8")).hexdigest()


def client_fingerprint(ip: str | None) -> str:
    """Короткий необратимый отпечаток клиента для ключей лимитов и логов.

    Нужен, чтобы адрес посетителя не попадал ни в ключи Redis, ни в память,
    ни в записи журнала."""
    raw = f"{ip or '-'}|{settings.secret_key}"
    return sha256(raw.encode("utf-8")).hexdigest()[:32]


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def truncate(value: str, limit: int = 80) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def client_ip(request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
