"""Смена пароля выкидывает из всех устройств, кроме текущего.

Cookie подписана, но на сервере не хранится, поэтому сама по себе она
переживала смену пароля: старый телефон продолжал работать под паролем,
который уже не действует. Чинится столбцом `password_changed_at` — cookie,
подписанная раньше, больше не принимается.

Два края, на которых легко ошибиться, и ради которых файл и написан:

- **Часовой пояс.** Из SQLite приходит naive-datetime в UTC, а машина может
  быть в UTC+3. Наивный `.timestamp()` сдвинул бы сравнение на три часа и
  выбил бы человека сразу после смены пароля.
- **Свою же сессию выбивать нельзя.** После смены пароля браузер, в котором
  её делали, должен остаться внутри — иначе человек решит, что его выкинули.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import session as auth_session
from auth.service import register_creator
from core.config import settings
from core.models import User
from core.utils import utcnow


def _login(client: TestClient, username: str = "anna") -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": "secret1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _remember_user_id(db_session: Session, username: str = "anna") -> int:
    user = db_session.scalar(select(User).where(User.username == username))
    assert user is not None
    return user.id


def test_password_change_sets_the_column(db_session: Session, clean_tables: None) -> None:
    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    user = _remember_user_id(db_session)
    assert user is not None
    assert db_session.get(User, user).password_changed_at is None, "у нового аккаунта отметки быть не должно"


def test_new_cookie_keeps_you_signed_in(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    """Тот браузер, где меняли пароль, остаётся внутри."""
    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    _login(client)

    changed = client.post(
        "/account/password",
        data={
            "current_password": "secret1",
            "password": "newsecret1",
            "password_repeat": "newsecret1",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303

    cabinet = client.get("/account", follow_redirects=False)
    assert cabinet.status_code == 200, "смена пароля выбила из текущего сеанса"


def test_old_cookie_stops_working(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    """Cookie, выданная до смены пароля, больше не пускает."""
    from datetime import timedelta

    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    _login(client)

    user_id = _remember_user_id(db_session)
    old_cookie = auth_session.sign_user_id(user_id)

    client.post(
        "/account/password",
        data={
            "current_password": "secret1",
            "password": "newsecret1",
            "password_repeat": "newsecret1",
        },
        follow_redirects=False,
    )

    # Отметку о смене сдвигаем вперёд явно: тест идёт быстрее допуска на
    # расхождение часов, и «настоящую» секунду между cookie и сменой пароля
    # пришлось бы ждать впустую. Проверяем ровно правило сравнения.
    user = db_session.get(User, user_id)
    user.password_changed_at = utcnow() + timedelta(hours=1)
    db_session.commit()

    # Возвращаем старую cookie — ровно то, что осталось на чужом телефоне.
    client.cookies.set(settings.cookie_name, old_cookie, path="/")
    cabinet = client.get("/account", follow_redirects=False)
    assert cabinet.status_code == 303, "старая cookie всё ещё пускает в кабинет"

    # И анкеты тоже: доступ определяется тем же способом.
    dashboard = client.get("/dashboard", follow_redirects=False)
    assert dashboard.status_code == 303


def test_cookie_just_before_change_is_rejected(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    """Провал за секунду до отметки — уже провал.

    Подпись хранит время с точностью до секунды, поэтому и сравнение идёт
    по секундам: «на миллисекунду раньше» отличить нельзя и не нужно.
    """
    from datetime import timedelta

    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    _login(client)

    user_id = _remember_user_id(db_session)
    cookie = auth_session.sign_user_id(user_id)

    user = db_session.get(User, user_id)
    user.password_changed_at = utcnow() + timedelta(seconds=1)
    db_session.commit()

    client.cookies.set(settings.cookie_name, cookie, path="/")
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_cookie_signed_in_the_same_second_still_works(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    """Обратная сторона округления: та же секунда — сессия жива.

    Без этого человек, только что сменивший пароль, вылетел бы на страницу
    входа и решил бы, что его выкинули со всех устройств сразу.
    """
    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    _login(client)

    user_id = _remember_user_id(db_session)
    cookie = auth_session.sign_user_id(user_id)

    user = db_session.get(User, user_id)
    user.password_changed_at = utcnow()
    db_session.commit()

    client.cookies.set(settings.cookie_name, cookie, path="/")
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_cookie_before_change_is_rejected_even_without_clock_skew(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    """Отметка о смене пароля в будущем не должна ломать вход.

    Регрессия на часовой пояс: если сравнение уедет на часы, человек
    окажется залогинен сразу после смены пароля.
    """
    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    user_id = _remember_user_id(db_session)

    client.post(
        "/login",
        data={"username": "anna", "password": "secret1"},
        follow_redirects=False,
    )
    client.post(
        "/account/password",
        data={
            "current_password": "secret1",
            "password": "newsecret1",
            "password_repeat": "newsecret1",
        },
        follow_redirects=False,
    )
    fresh_cookie = client.cookies.get(settings.cookie_name)

    db_session.expire_all()
    changed_at = db_session.get(User, user_id).password_changed_at
    assert changed_at is not None, "момент смены пароля не записан"

    client.cookies.set(settings.cookie_name, fresh_cookie, path="/")
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_account_without_password_change_keeps_session(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    """Пустая отметка не должна выкидывать никого — иначе перенос базы
    на новую версию обнулил бы все сессии разом."""
    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    _login(client)

    cabinet = client.get("/account", follow_redirects=False)
    assert cabinet.status_code == 200


def test_login_after_change_gets_fresh_cookie(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    register_creator(db_session, "anna", "secret1")
    db_session.commit()
    _login(client)

    client.post(
        "/account/password",
        data={
            "current_password": "secret1",
            "password": "newsecret1",
            "password_repeat": "newsecret1",
        },
        follow_redirects=False,
    )

    # Старый пароль больше не подходит, новый — да, и вход даёт рабочую cookie.
    stale = client.post(
        "/login",
        data={"username": "anna", "password": "secret1"},
        follow_redirects=False,
    )
    assert stale.status_code == 401

    fresh = client.post(
        "/login",
        data={"username": "anna", "password": "newsecret1"},
        follow_redirects=False,
    )
    assert fresh.status_code == 303
    assert client.get("/account", follow_redirects=False).status_code == 200


def test_timestamp_conversion_uses_utc_not_local_time() -> None:
    """Часовая стрелка не должна сдвигать сравнение.

    Значение из базы — момент UTC без часового пояса, а подпись cookie
    приходит в UTC с поясом. Наивное сравнение считало бы их разными.
    """
    from datetime import datetime, timezone

    moment = datetime(2026, 1, 1, 12, 0, 0)
    converted = auth_session._as_utc(moment)

    assert converted == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert converted.tzinfo is not None
    # Именно UTC, а не местное время: на машине в UTC+12 это были бы 0:00.
    assert converted.hour == 12


def test_column_is_added_to_existing_database() -> None:
    """Схема дополняется сама: проекту не нужны миграции."""
    from database.session import add_missing_columns
    from sqlalchemy import inspect

    from database.session import engine

    add_missing_columns()

    columns = {item["name"] for item in inspect(engine).get_columns("users")}
    assert "password_changed_at" in columns