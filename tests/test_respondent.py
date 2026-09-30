"""Приватность респондента: баннер, свои ответы, «Мои ответы», изоляция.

Смысл этих тестов — проверить, что человек, которому прислали ссылку,
не видит чужих данных и не может по ссылке зайти в чужие результаты.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.config import settings
from tests import factories


def _submit(client, survey, question, value="Отлично", user_agent="device-a"):
    return client.post(
        f"/s/{survey.slug}",
        data={f"q{question.id}": [value]},
        headers={"user-agent": user_agent},
        follow_redirects=False,
    )


def _survey_by_creator(client, db_session, **kwargs):
    """Создаёт анкету под вошедшим автором и оставляет клиента респондентом.

    Дальше тест работает как обычный человек, которому прислали ссылку.
    Сессия создателя снимается, а cookie респондента ставится отдельным
    заходом на страницу анкеты: раньше её неявно ставил рендер `/dashboard`,
    которого у респондента больше нет.
    """
    owner = factories.login_as(client, db_session)
    survey, _key = factories.make_survey(db_session, owner, **kwargs)
    _become_pure_respondent(client)
    client.get(f"/s/{survey.slug}")
    return survey


def _become_pure_respondent(client) -> None:
    """Оставляет cookie респондента, убирает cookie сессии владельца.

    Именно это состояние у человека, которому прислали ссылку: ответы есть,
    а собственных анкет нет. `client.cookies.clear()` не подходит — он заодно
    сбросил бы токен респондента.
    """
    client.cookies.delete(settings.cookie_name)


class TestAlreadyAnsweredBanner:
    def test_banner_absent_before_first_submit(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        page = client.get(f"/s/{survey.slug}")

        assert page.status_code == 200
        assert "Вы уже заполнили эту анкету" not in page.text
        assert "Отправить ответ" in page.text

    def test_banner_shown_immediately_after_submit(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]

        assert _submit(client, survey, question).status_code == 303

        page = client.get(f"/s/{survey.slug}")

        assert page.status_code == 200
        assert "Вы уже заполнили эту анкету" in page.text
        assert "id=\"already-answered\"" in page.text

    def test_banner_contains_own_answers_only(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        questions = factories.questions_of(db_session, survey)
        first, third = questions[0], questions[2]

        _submit(client, survey, first, value="Отлично")

        page = client.get(f"/s/{survey.slug}")

        assert "Ваши ответы" in page.text
        assert "Как вам сервис?" in page.text
        assert "Отлично" in page.text

    def test_other_respondent_does_not_see_the_banner(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question, value="Отлично", user_agent="device-a")

        with TestClient(client.app) as other:
            page = other.get(f"/s/{survey.slug}")

        assert page.status_code == 200
        assert "Вы уже заполнили эту анкету" not in page.text
        assert "Отправить ответ" in page.text

    def test_second_submit_still_conflicts(self, client, db_session, clean_tables):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question)

        response = _submit(client, survey, question)

        assert response.status_code == 409
        assert "Ответ уже отправлен" in response.text


class TestMyAnswers:
    def test_lists_own_answers(self, client, db_session, clean_tables):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question, value="Отлично")

        page = client.get("/my")

        assert page.status_code == 200
        assert "Мои ответы" in page.text
        assert survey.title in page.text
        assert "Отлично" in page.text

    def test_empty_state_for_new_visitor(self, client, clean_tables):
        page = client.get("/my")

        assert page.status_code == 200
        assert "Вы ещё не заполняли анкеты" in page.text

    def test_does_not_show_other_respondent_answers(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question, value="Отлично", user_agent="device-a")

        with TestClient(client.app) as other:
            other.get(f"/s/{survey.slug}")
            page = other.get("/my")

        assert "Вы ещё не заполняли анкеты" in page.text
        assert "Отлично" not in page.text

    def test_survives_user_agent_change(self, client, db_session, clean_tables):
        """Токен в cookie переживает смену User-Agent — в отличие от IP+UA."""
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question, user_agent="device-a")

        page = client.get(f"/s/{survey.slug}", headers={"user-agent": "device-b"})

        assert "Вы уже заполнили эту анкету" in page.text

    def test_find_answers_by_legacy_hash_without_cookie(
        self, client, db_session, clean_tables
    ):
        """Ответы, записанные до появления токена, всё ещё находятся по хэшу."""
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question, value="Отлично", user_agent="device-a")

        from database.repositories import answers as answers_repo

        response_row = answers_repo.latest_submitted(db_session, survey.id)[0]
        response_row.respondent_token = None
        db_session.commit()
        client.cookies.clear()

        page = client.get(f"/s/{survey.slug}", headers={"user-agent": "device-a"})

        assert "Вы уже заполнили эту анкету" in page.text


class TestRespondentIsolation:
    """Отвечающему не нужен аккаунт, и кабинет ему не выдаётся."""

    def test_dashboard_sends_respondent_to_login(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question)

        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")

    def test_respondent_visit_creates_no_owner_user(
        self, client, db_session, clean_tables
    ):
        from core.models import User
        from sqlalchemy import func, select

        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question)

        client.get("/dashboard", follow_redirects=False)

        count = db_session.scalar(select(func.count(User.id)))
        assert count == 1

    def test_navigation_hides_dashboard_from_respondent(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)

        page = client.get(f"/s/{survey.slug}")

        assert "Мои ответы" in page.text
        assert "Мои анкеты" not in page.text
        assert ">Вход<" in page.text

    def test_navigation_shows_dashboard_to_owner(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)

        page = client.get("/dashboard")

        assert "Мои анкеты" in page.text
        assert "Мои ответы" in page.text

    def test_respondent_cannot_read_survey_stats(
        self, client, db_session, clean_tables
    ):
        survey = _survey_by_creator(client, db_session)
        question = factories.questions_of(db_session, survey)[0]
        _submit(client, survey, question)

        response = client.get(f"/s/{survey.slug}/stats")

        assert response.status_code == 403

    def test_respondent_cannot_read_owner_pages(
        self, client, db_session, clean_tables
    ):
        """Правка и настройки незнакомцу отдают 404: не подтверждаем, что
        анкета вообще существует. Удаление требует входа, поэтому уводит
        на форму входа — тоже без ответа про существование анкеты."""
        survey = _survey_by_creator(client, db_session)

        assert client.get(f"/s/{survey.slug}/settings").status_code == 404
        assert client.get(f"/s/{survey.slug}/edit").status_code == 404

        response = client.get(f"/s/{survey.slug}/delete", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")

    def test_dashboard_closed_for_fresh_visitor(self, client, clean_tables):
        """Гостем кабинет закрыт, а не пуст: без входа его не показать."""
        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/dashboard"
