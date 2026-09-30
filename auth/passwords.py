"""Хеширование и проверка паролей.

Используется PBKDF2-HMAC-SHA256 из стандартной библиотеки: отдельная
зависимость ради этого не нужна, а параметры хранятся вместе с хешем, так
что число итераций можно поднять позже без ломки старых паролей.

Формат: `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`.

Пароль никогда не пишется в лог и не попадает в исключения: наружу
отдаётся только результат проверки.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 6

_PASSWORD_ERROR = "Пароль должен быть не короче 6 символов."


class PasswordTooShort(ValueError):
    """Пароль короче допустимого минимума."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=32
    )


def hash_password(password: str, *, enforce_length: bool = True) -> str:
    """Хеш для хранения.

    `enforce_length=False` — только для девелоперской учётки `admin/admin`.
    Её пароль короче общего минимума, но требование именно такое, и о
    смене пароля предупреждает плашка при входе. Для паролей, которые
    выбирает человек, проверка длины обязательна.
    """
    if not password:
        raise ValueError("Пароль не может быть пустым.")
    if enforce_length and len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShort(_PASSWORD_ERROR)
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _derive(password, salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Сверяет пароль с хешем за постоянное время.

    Любой разбор неверного формата считается неуспешной проверкой, а не
    ошибкой: иначе битая запись в базе роняла бы вход вместо отказа.
    """
    if not password or not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != ALGORITHM:
        return False
    try:
        iterations = int(parts[1])
        salt = _unb64(parts[2])
        expected = _unb64(parts[3])
    except (ValueError, TypeError):
        return False
    if iterations <= 0:
        return False
    candidate = _derive(password, salt, iterations)
    return hmac.compare_digest(candidate, expected)


def check_password_length(password: str) -> str | None:
    """Текст ошибки для формы регистрации, либо None если пароль годится."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return _PASSWORD_ERROR
    return None
