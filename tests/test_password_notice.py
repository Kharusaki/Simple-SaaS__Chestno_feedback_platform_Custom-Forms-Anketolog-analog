"""Окно «пароль стандартный»: два разных действия и две разные цены.

Проверяем смысл, а не вёрстку:
- окно появляется только у учётки, которой это нужно, и только в этом
  браузере — предупреждение приходит к каждой странице, поэтому «закрыть
  один раз» должно означать «не показывать на этом компьютере»;
- кнопка «продолжить как есть» не трогает флаг в базе: иначе предупреждение
  можно было бы отключить навсегда одним невнимательным кликом, а флаг для
  того и нужен, чтобы о нём помнил администратор;
- галочка «не напоминать» снимает флаг, и это уже навсегда;
- смена пароля и выход убирают cookie, чтобы окно не висело призраком;
- `Referer` клиенту не доверяем: чужой адрес в ответе не появляется.
"""

from __future__ import annotations

from core.config import settings
from tests import factories

NOTICE = "стандартный пароль"
MARKER = 'id="password-modal"'
DISMISS = "/account/password/dismiss"


def mark(client, db_session) -> None:
    """Включает требование смены пароля у уже вошедшего создателя."""
    user = factories.owner_by_username(db_session, "anna")
    user.must_change_password = True
    db_session.commit()


def make_flagged(client, db_session):
    factories.login_as(client, db_session)
    mark(client, db_session)
    return factories.owner_by_username(db_session, "anna")


class TestVisibility:
    def test_window_shown_for_flagged_account(self, client, db_session, clean_tables):
        make_flagged(client, db_session)

        response = client.get("/dashboard")

        assert MARKER in response.text
        assert NOTICE in response.text

    def test_window_hidden_for_normal_account(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)

        response = client.get("/dashboard")

        assert MARKER not in response.text

    def test_window_hidden_for_anonymous_visitor(self, client, db_session, clean_tables):
        response = client.get("/login")

        assert MARKER not in response.text

    def test_continue_button_works_without_javascript(self, client, db_session, clean_tables):
        """Кнопка отправляет форму, а не зовёт `fetch`: без JS окно тоже
        должно закрываться, иначе его нельзя будет закрыть совсем."""
        make_flagged(client, db_session)

        response = client.get("/dashboard")

        assert f'action="{DISMISS}"' in response.text
        assert 'name="dont_remind"' in response.text


class TestTemporaryDismiss:
    def test_continue_hides_window_in_this_browser(self, client, db_session, clean_tables):
        user = make_flagged(client, db_session)

        response = client.post(
            DISMISS, data={}, follow_redirects=False, headers={"referer": "/dashboard"}
        )

        assert response.status_code == 303
        assert settings.password_notice_cookie_name in response.cookies
        assert MARKER not in client.get("/dashboard").text

    def test_continue_keeps_flag_in_database(self, client, db_session, clean_tables):
        user = make_flagged(client, db_session)

        client.post(DISMISS, data={}, follow_redirects=False)

        db_session.refresh(user)
        assert user.must_change_password is True

    def test_continue_returns_to_the_page_it_came_from(self, client, db_session, clean_tables):
        make_flagged(client, db_session)

        response = client.post(
            DISMISS, data={}, follow_redirects=False, headers={"referer": "/settings-x"}
        )

        assert response.headers["location"].endswith("/settings-x")

    def test_foreign_referer_is_not_trusted(self, client, db_session, clean_tables):
        make_flagged(client, db_session)

        response = client.post(
            DISMISS,
            data={},
            follow_redirects=False,
            headers={"referer": "https://example.com/steal"},
        )

        assert "example.com" not in response.headers["location"]


class TestPermanentDismiss:
    def test_dont_remind_clears_flag_in_database(self, client, db_session, clean_tables):
        user = make_flagged(client, db_session)

        response = client.post(
            DISMISS,
            data={"dont_remind": "1"},
            follow_redirects=False,
            headers={"referer": "/dashboard"},
        )

        db_session.refresh(user)
        assert user.must_change_password is False
        # Cookie сброшена: при следующем входе окно не появится и без неё.
        assert settings.password_notice_cookie_name not in response.cookies

    def test_dont_remind_hides_window_for_another_device(self, client, db_session, clean_tables):
        """Главный смысл галочки: предупреждение должно перестать приходить
        вообще, а не только в этом браузере."""
        user = make_flagged(client, db_session)
        client.post(DISMISS, data={"dont_remind": "1"}, follow_redirects=False)

        client.cookies.clear()
        factories.login_as(client, db_session)

        assert MARKER not in client.get("/dashboard").text


class TestWindowReset:
    def test_changing_password_clears_cookie(self, client, db_session, clean_tables):
        make_flagged(client, db_session)
        client.post(DISMISS, data={}, follow_redirects=False)
        assert settings.password_notice_cookie_name in client.cookies

        response = client.post(
            "/account/password",
            data={
                "current_password": "secret1",
                "password": "new-secret-1",
                "password_repeat": "new-secret-1",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert settings.password_notice_cookie_name not in client.cookies

    def test_logout_clears_cookie(self, client, db_session, clean_tables):
        make_flagged(client, db_session)
        client.post(DISMISS, data={}, follow_redirects=False)

        client.post("/logout", follow_redirects=False)

        assert settings.password_notice_cookie_name not in client.cookies
