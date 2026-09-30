"""Простота запуска: автосоздание `.env`, учётка администратора, понятная главная.

Проверяем решения, принятые после того, как пользователь попробовал начать
работу и не смог: настройки должны появляться сами, а не после правки
файла вручную.

Раньше тут проверялся `MODERATOR_TOKEN` и подсказка с ним на странице входа.
Токена больше нет — вместо него учётка администратора, а её логин и пароль
печатает лаунчер, а не веб-страница.
"""

from __future__ import annotations

import re

from core.config import ensure_env_file


class TestEnvAutocreate:
    def test_creates_file_when_missing(self, tmp_path, monkeypatch):
        from core import config as config
        target = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", target)

        result = ensure_env_file()

        assert result == target
        assert target.exists()

    def test_generated_file_has_random_secret(self, tmp_path, monkeypatch):
        from core import config as config
        target = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", target)

        ensure_env_file()
        body = target.read_text(encoding="utf-8")

        assert "SECRET_KEY=" in body
        assert "dev-secret-key-change-me-in-production" not in body
        assert "MODERATOR_TOKEN" not in body

    def test_does_not_overwrite_existing_file(self, tmp_path, monkeypatch):
        from core import config as config
        target = tmp_path / ".env"
        target.write_text("SECRET_KEY=моё-значение\n", encoding="utf-8")
        monkeypatch.setattr(config, "ENV_PATH", target)

        result = ensure_env_file()

        assert result is None
        assert "моё-значение" in target.read_text(encoding="utf-8")

    def test_two_runs_produce_different_secrets(self, tmp_path, monkeypatch):
        from core import config as config
        first = tmp_path / "a.env"
        monkeypatch.setattr(config, "ENV_PATH", first)
        ensure_env_file()

        second = tmp_path / "b.env"
        monkeypatch.setattr(config, "ENV_PATH", second)
        ensure_env_file()

        assert first.read_text(encoding="utf-8") != second.read_text(encoding="utf-8")

    def test_generated_file_has_no_template_placeholders(self, tmp_path, monkeypatch):
        from core import config as config
        target = tmp_path / ".env"
        monkeypatch.setattr(config, "ENV_PATH", target)

        ensure_env_file()
        body = target.read_text(encoding="utf-8")

        assert "{" not in body and "}" not in body


class TestAdminAccount:
    """Учётка администратора заводится сама, иначе раздел закрыт навсегда."""

    def test_account_is_created_when_missing(self, db_session, clean_tables):
        from auth.service import authenticate, ensure_admin_account

        user = ensure_admin_account(db_session)

        assert user is not None
        assert user.is_admin
        assert authenticate(db_session, "admin", "admin") is not None

    def test_new_admin_must_change_password(self, db_session, clean_tables):
        from auth.service import ensure_admin_account

        assert ensure_admin_account(db_session).must_change_password is True

    def test_existing_password_survives_restart(self, db_session, clean_tables):
        """Повторный запуск не должен затирать уже сменённый пароль."""
        from auth.service import apply_new_password, ensure_admin_account

        user = ensure_admin_account(db_session)
        apply_new_password(db_session, user, "новый-пароль")

        assert ensure_admin_account(db_session) is None
        assert user.password_hash != ""
        assert user.must_change_password is False

    def test_credentials_are_not_shown_on_public_pages(self, client, clean_tables):
        """Пароль по умолчанию печатает лаунчер, на сайте его быть не должно."""
        for path in ("/", "/login", "/register"):
            body = client.get(path).text
            assert "admin/admin" not in body, path

    def test_warning_prompts_password_change(self, client, as_admin, clean_tables):
        home = client.get("/")

        assert "стандартный" in home.text
        assert "/account/password" in home.text

    def test_warning_leaves_after_dismiss(self, client, as_admin, clean_tables):
        response = client.post(
            "/account/password/dismiss", follow_redirects=False
        )

        assert response.status_code == 303
        assert "стандартный" not in client.get("/").text

    def test_creator_has_no_password_warning(self, client, as_creator, clean_tables):
        assert "стандартный" not in client.get("/").text


class TestLandingPage:
    def test_two_role_paths(self, client, clean_tables):
        home = client.get("/")

        assert home.status_code == 200
        assert "Я создаю анкеты" in home.text
        assert "Я отвечаю на анкеты" in home.text

    def test_author_path_leads_to_creation(self, client, clean_tables):
        home = client.get("/")

        assert '"/surveys/new"' in home.text
        assert '"/dashboard"' in home.text

    def test_respondent_path_leads_to_my_answers(self, client, clean_tables):
        home = client.get("/")

        assert '"/my"' in home.text

    def test_landing_keeps_short_claims(self, client, clean_tables):
        """Главная не должна превращаться в инструкцию: маркетинг убран вниз."""
        home = client.get("/")

        assert "Как это работает" not in home.text

    def test_registration_link_present(self, client, clean_tables):
        home = client.get("/")

        assert '"/register"' in home.text

    def test_no_admin_links_for_guests(self, client, clean_tables):
        assert "/admin" not in client.get("/").text

    def test_landing_shows_service_name_and_slogan(self, client, clean_tables):
        home = client.get("/")

        assert "ЧЕСТНО" in home.text
        assert "Простые формы для честной обратной связи" in home.text

    def test_slogan_is_the_page_heading(self, client, clean_tables):
        """Слоган — заголовок первого экрана, а не подпись в подвале."""
        home = client.get("/")

        assert re.search(r"<h1>Простые формы для честной обратной связи</h1>", home.text)
