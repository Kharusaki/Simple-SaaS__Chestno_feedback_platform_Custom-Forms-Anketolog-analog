"""Зависимости доступа: кто сейчас перед сайтом и что ему можно.

Разделение намеренное:

- `current_user` — чистая функция «кто вошёл, или никто». Её зовут и как
  зависимость FastAPI, и напрямую из общего контекста шаблонов;
- `require_user` — для всего, что создаёт и правит анкеты: без входа
  отправляем на форму входа, а не отвечаем 401 на HTML-странице;
- `require_admin` — раздел настройки сайта, инструкции и документация API.

Раньше вместо входа был анонимный владелец с cookie: он появлялся у
каждого посетителя и открывал пустую анкету кому угодно. Теперь закрытые
процессы действительно закрыты.

Отказ от доступа поднимается исключением, а не возвращается редиректом:
иначе зависимостью «вернулась» бы не `User`, и любая функция, работающая
с аккаунтом, получила бы вместо пользователя HTTP-ответ.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from auth.session import resolve_current_user
from database.session import get_db
from core.models import User

LOGIN_PATH = "/login"


class LoginRequired(HTTPException):
    """Гостю нужен вход.

    Это не отказ в доступе, а приглашение войти, поэтому 303 на форму входа
    с адресом возврата, а не 401.
    """

    def __init__(self, target: str) -> None:
        location = f"{LOGIN_PATH}?next={quote(target, safe='/?=&')}"
        super().__init__(status_code=303, headers={"Location": location})
        self.target = target


class SectionHidden(HTTPException):
    """Раздела нет — 404, чтобы не подтверждать постороннему его существование."""

    def __init__(self) -> None:
        super().__init__(status_code=404, detail="Страница не найдена")


def return_target(request: Request) -> str:
    """Куда вернуть гостя после входа: тот же путь с тем же вопросом.

    Собирается из самого запроса, а не из параметра: подставить сюда чужую
    ссылку — значит превратить `/login` в открытый редирект.
    """
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return target


def current_user(request: Request, session: Session) -> User | None:
    return resolve_current_user(request, session)


def current_user_dep(
    request: Request, session: Session = Depends(get_db)
) -> User | None:
    """`current_user` как зависимость FastAPI."""
    return current_user(request, session)


def require_user(
    request: Request, session: Session = Depends(get_db)
) -> User:
    """Аккаунт обязателен. Гость уходит на /login с возвратом обратно."""
    user = current_user(request, session)
    if user is None:
        raise LoginRequired(return_target(request))
    return user


def is_admin(user: User | None) -> bool:
    return user is not None and user.is_admin


def require_admin(user: User = Depends(require_user)) -> User:
    """Только администратор. Неадминистратор получает 404.

    Именно 404, а не 403: существование раздела не должно подтверждаться
    постороннему, как и с ключами от анкет.
    """
    if not is_admin(user):
        raise SectionHidden()
    return user
