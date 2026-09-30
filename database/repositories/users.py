"""SQL-доступ к таблице `users`. Только SQL, без бизнес-логики и HTTP.

Хеширование паролей сюда не попадает: репозиторий не знает, что такое
пароль, и не должен решать, что с ним делать.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import ROLE_ADMIN, ROLE_CREATOR, User


def get_by_id(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def get_by_username(session: Session, username: str) -> User | None:
    if not username:
        return None
    return session.scalar(select(User).where(User.username == username))


def username_exists(session: Session, username: str) -> bool:
    return (
        session.scalar(select(User.id).where(User.username == username).limit(1))
        is not None
    )


def create_creator(session: Session, username: str, password_hash: str) -> User:
    user = User(
        username=username, password_hash=password_hash, role=ROLE_CREATOR
    )
    session.add(user)
    session.flush()
    return user


def get_admin(session: Session) -> User | None:
    return session.scalar(select(User).where(User.role == ROLE_ADMIN).limit(1))


def count_admins(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.role == ROLE_ADMIN)) or 0
