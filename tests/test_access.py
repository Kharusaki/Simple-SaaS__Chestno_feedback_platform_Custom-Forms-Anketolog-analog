from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from auth import keys as keys_auth
from core.main import app
from core.models import User
from tests import factories


def owner(db_session) -> User:
    return factories.unique_owner(db_session, "keys")


@pytest.fixture
def survey_with_key(db_session, clean_tables):
    """Опрос чужого владельца с выданным основным ключом."""
    stranger = owner(db_session)
    survey, key = factories.make_survey(db_session, stranger)
    return survey, key


class TestFindKey:
    def test_finds_own_key(self, db_session, survey_with_key):
        survey, key = survey_with_key

        found = keys_auth.find_key(db_session, survey, key.token)

        assert found is not None
        assert found.id == key.id

    def test_empty_token_is_never_valid(self, db_session, survey_with_key):
        survey, _ = survey_with_key

        assert keys_auth.find_key(db_session, survey, None) is None
        assert keys_auth.find_key(db_session, survey, "") is None

    def test_foreign_token_rejected(self, db_session, survey_with_key):
        survey, _ = survey_with_key

        assert keys_auth.find_key(db_session, survey, "x" * 48) is None

    def test_key_of_another_survey_rejected(self, db_session, clean_tables):
        stranger = owner(db_session)
        first, first_key = factories.make_survey(db_session, stranger)
        second, _ = factories.make_survey(db_session, stranger)

        assert keys_auth.find_key(db_session, second, first_key.token) is None

    def test_token_of_same_length_but_different_is_rejected(self, db_session, survey_with_key):
        survey, key = survey_with_key
        almost = key.token[:-1] + ("a" if key.token[-1] != "a" else "b")

        assert keys_auth.find_key(db_session, survey, almost) is None


class TestCheckAccess:
    def test_owner_by_cookie(self, db_session, survey_with_key):
        survey, _ = survey_with_key
        its_owner = db_session.get(User, survey.owner_id)

        assert keys_auth.check_access(db_session, survey, its_owner, None) is True

    def test_owner_does_not_need_key(self, db_session, survey_with_key):
        survey, _ = survey_with_key
        its_owner = db_session.get(User, survey.owner_id)

        assert keys_auth.check_access(db_session, survey, its_owner, "wrong-token") is True

    def test_stranger_without_key_denied(self, db_session, survey_with_key):
        survey, _ = survey_with_key
        stranger = owner(db_session)

        assert keys_auth.check_access(db_session, survey, stranger, None) is False

    def test_anonymous_with_valid_key_allowed(self, db_session, survey_with_key):
        survey, key = survey_with_key

        assert keys_auth.check_access(db_session, survey, None, key.token) is True

    def test_stranger_with_valid_key_allowed(self, db_session, survey_with_key):
        survey, key = survey_with_key
        stranger = owner(db_session)

        assert keys_auth.check_access(db_session, survey, stranger, key.token) is True

    def test_stranger_with_foreign_key_denied(self, db_session, survey_with_key):
        survey, _ = survey_with_key
        stranger = owner(db_session)
        _, other_key = factories.make_survey(db_session, stranger)

        assert keys_auth.check_access(db_session, survey, stranger, other_key.token) is False


class TestStatsAccessByKey:
    def test_key_in_query_opens_stats(self, client, db_session, survey_with_key):
        survey, key = survey_with_key

        response = client.get(f"/s/{survey.slug}/stats?key={key.token}")

        assert response.status_code == 200
        assert "Статистика ответов" in response.text

    def test_key_in_query_opens_csv(self, client, db_session, survey_with_key):
        survey, key = survey_with_key

        response = client.get(f"/s/{survey.slug}/stats.csv?key={key.token}")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]

    def test_wrong_key_still_forbidden(self, client, db_session, survey_with_key):
        survey, _ = survey_with_key

        response = client.get(f"/s/{survey.slug}/stats?key={'z' * 48}")

        assert response.status_code == 403
        assert "Доступ закрыт" in response.text

    def test_empty_key_is_not_access(self, client, db_session, survey_with_key):
        survey, _ = survey_with_key

        assert client.get(f"/s/{survey.slug}/stats?key=").status_code == 403

    def test_wrong_key_on_csv_forbidden(self, client, db_session, survey_with_key):
        survey, _ = survey_with_key

        assert client.get(f"/s/{survey.slug}/stats.csv?key={'z' * 48}").status_code == 403

    def test_viewing_stats_via_key_creates_no_user(self, client, db_session, survey_with_key):
        """Анонимный просмотр по ключу не должен плодить строки в `users`."""
        survey, key = survey_with_key
        before = db_session.query(User).count()

        client.get(f"/s/{survey.slug}/stats?key={key.token}")

        assert db_session.query(User).count() == before

    def test_denied_view_creates_no_user(self, client, db_session, survey_with_key):
        survey, _ = survey_with_key
        before = db_session.query(User).count()

        client.get(f"/s/{survey.slug}/stats")

        assert db_session.query(User).count() == before

    def test_owner_does_not_see_share_link_on_stats(self, client, db_session, clean_tables):
        """Ключи и ссылки живут в настройках анкеты. На странице статистики
        их не дублируем: владелец смотрит её из своего кабинета."""
        mine = factories.login_as(client, db_session)
        survey, key = factories.make_survey(db_session, mine)

        response = client.get(f"/s/{survey.slug}/stats")

        assert response.status_code == 200
        assert "Ссылка для просмотра статистики" not in response.text
        assert key.token not in response.text

    def test_owner_sees_share_link_in_settings(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey, key = factories.make_survey(db_session, mine)

        response = client.get(f"/s/{survey.slug}/settings")

        assert response.status_code == 200
        assert key.token in response.text
        assert f"/s/{survey.slug}/stats?key={key.token}" in response.text

    def test_key_visitor_does_not_see_share_link(self, client, db_session, survey_with_key):
        survey, key = survey_with_key

        response = client.get(f"/s/{survey.slug}/stats?key={key.token}")

        assert response.status_code == 200
        assert "Ссылка для просмотра статистики" not in response.text
        assert key.token not in response.text

    def test_share_link_from_page_works_in_clean_browser(self, client, db_session, clean_tables):
        """Ссылка, скопированная владельцем, открывает статистику без его cookie."""
        owner_client = client
        mine = factories.login_as(owner_client, db_session)
        survey, key = factories.make_survey(db_session, mine)
        link = f"/s/{survey.slug}/stats?key={key.token}"

        anonymous = TestClient(app)

        try:
            response = anonymous.get(link)
        finally:
            anonymous.close()

        assert response.status_code == 200
        assert "Статистика ответов" in response.text
