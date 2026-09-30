"""Управление анкетой: правка, приостановка приёма, удаление.

Меню действий живёт в дашборде, поэтому почти все проверки идут через
owner-сессию TestClient, а не напрямую через crud.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from database.repositories import answers as answers_repo
from core.models import Answer, Choice, Question, Response, Survey, SurveyKey
from surveys import crud
from tests import factories
from tests.test_surveys_crud import form_payload


def _session():
    from database.session import SessionLocal

    return SessionLocal()


@pytest.fixture
def owned(client, db_session, clean_tables):
    """Анкета, принадлежащая пользователю текущего клиента."""
    owner = factories.login_as(client, db_session)
    survey, _key = factories.make_survey(db_session, owner, title="Анкета после покупки")
    return survey


def one_question(title: str = "Анкета", **extra) -> dict:
    """Форма правки с одним вопросом — как её отправляет браузер."""
    data = {
        "title": [title],
        "description": [""],
        "q0_type": ["yes_no"],
        "q0_text": ["Только один вопрос"],
        "q0_is_required": ["1"],
    }
    data.update(extra)
    return data


class TestDashboardMenu:
    def test_menu_shows_all_three_actions(self, client, owned):
        response = client.get("/dashboard")

        assert response.status_code == 200
        assert f"/s/{owned.slug}/edit" in response.text
        assert f"/s/{owned.slug}/open" in response.text
        assert f"/s/{owned.slug}/delete" in response.text

    def test_menu_offers_pause_when_open(self, client, owned):
        assert owned.is_open is True
        response = client.get("/dashboard")

        assert "Остановить приём ответов" in response.text

    def test_menu_offers_resume_when_paused(self, client, db_session, owned):
        crud.set_open(db_session, owned, False)

        response = client.get("/dashboard")

        assert "Возобновить приём ответов" in response.text


class TestPause:
    def test_pause_closes_public_page(self, client, db_session, owned):
        client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        db_session.expire_all()
        assert factories.survey_by_slug(db_session, owned.slug).is_open is False

        page = client.get(f"/s/{owned.slug}")
        assert page.status_code == 403
        assert "Анкета закрыта" in page.text

    def test_pause_blocks_submission(self, client, db_session, owned):
        client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        response = client.post(f"/s/{owned.slug}", data=form_payload(title=["Анкета"]))

        assert response.status_code == 403
        db_session.expire_all()
        assert answers_repo.count_submitted(db_session, owned.id) == 0

    def test_resume_opens_again(self, client, db_session, owned):
        client.post(f"/s/{owned.slug}/open", follow_redirects=False)
        client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        db_session.expire_all()
        assert factories.survey_by_slug(db_session, owned.slug).is_open is True
        assert client.get(f"/s/{owned.slug}").status_code == 200

    def test_redirects_to_dashboard(self, client, owned):
        response = client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard"

    def test_answers_survive_pause(self, client, db_session, owned):
        client.post(
            f"/s/{owned.slug}",
            data=form_payload(title=["Анкета"]),
            headers={"user-agent": "pause-test"},
        )
        client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        db_session.expire_all()
        assert answers_repo.count_submitted(db_session, owned.id) == 1


class TestEdit:
    def test_form_shows_current_values(self, client, owned):
        response = client.get(f"/s/{owned.slug}/edit")

        assert response.status_code == 200
        assert "Анкета после покупки" in response.text
        assert "Как вам сервис?" in response.text

    def test_can_rename_and_pause(self, client, db_session, owned):
        response = client.post(
            f"/s/{owned.slug}/edit",
            data=one_question("Новое название", description=["Новое описание"]),
            follow_redirects=False,
        )

        assert response.status_code == 303
        db_session.expire_all()
        stored = factories.survey_by_slug(db_session, owned.slug)
        assert stored.title == "Новое название"
        assert stored.description == "Новое описание"
        assert stored.is_open is False

    def test_can_resume_from_form(self, client, db_session, owned):
        client.post(
            f"/s/{owned.slug}/edit",
            data=one_question(is_open="1"),
            follow_redirects=False,
        )

        db_session.expire_all()
        assert factories.survey_by_slug(db_session, owned.slug).is_open is True

    def test_questions_are_editable_without_answers(self, client, db_session, owned):
        response = client.post(
            f"/s/{owned.slug}/edit",
            data=one_question(is_open="1"),
            follow_redirects=False,
        )

        assert response.status_code == 303
        db_session.expire_all()
        questions = factories.questions_of(db_session, owned)
        assert len(questions) == 1
        assert questions[0].type == "yes_no"
        assert questions[0].text == "Только один вопрос"

    def test_choices_survive_edit(self, client, db_session, owned):
        client.post(
            f"/s/{owned.slug}/edit",
            data={
                "title": ["Анкета"],
                "is_open": "1",
                "q0_type": ["single"],
                "q0_text": ["Новый вопрос"],
                "q0_is_required": ["1"],
                "q0_choices": ["Раз\nДва\nТри"],
            },
            follow_redirects=False,
        )

        db_session.expire_all()
        questions = factories.questions_of(db_session, owned)
        assert factories.choices_of(db_session, questions[0]) == ["Раз", "Два", "Три"]

    def test_questions_are_frozen_after_answers(self, client, db_session, owned):
        client.post(
            f"/s/{owned.slug}",
            data=form_payload(title=["Анкета"]),
            headers={"user-agent": "freeze-test"},
        )

        page = client.get(f"/s/{owned.slug}/edit")
        assert "Вопросы зафиксированы" in page.text
        assert "questions-list" not in page.text

        response = client.post(
            f"/s/{owned.slug}/edit",
            data={"title": ["Переименованная"], "is_open": "1"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        db_session.expire_all()
        stored = factories.survey_by_slug(db_session, owned.slug)
        assert stored.title == "Переименованная"
        assert len(factories.questions_of(db_session, stored)) == 5

    def test_invalid_title_keeps_page(self, client, db_session, owned):
        response = client.post(
            f"/s/{owned.slug}/edit",
            data=one_question(""),
        )

        assert response.status_code == 400
        assert "Введите название" in response.text
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, owned.slug).title == "Анкета после покупки"

    def test_slug_survives_edit(self, client, db_session, owned):
        slug = owned.slug
        client.post(
            f"/s/{slug}/edit",
            data=one_question("Другое", is_open="1"),
            follow_redirects=False,
        )

        db_session.expire_all()
        assert factories.survey_by_slug(db_session, slug) is not None


class TestDelete:
    def test_confirmation_page_mentions_loss(self, client, owned):
        response = client.get(f"/s/{owned.slug}/delete")

        assert response.status_code == 200
        assert "Удалить анкету?" in response.text
        assert "необратимое" in response.text

    def test_delete_removes_survey(self, client, db_session, owned):
        slug = owned.slug
        response = client.post(f"/s/{slug}/delete", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard?deleted=1"
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, slug) is None

    def test_delete_removes_questions_and_keys(self, client, db_session, owned):
        slug = owned.slug
        client.post(f"/s/{slug}/delete", follow_redirects=False)
        db_session.expire_all()

        for model in (Question, Choice, SurveyKey):
            rows = db_session.scalars(select(model)).all()
            assert list(rows) == [], model.__tablename__

    def test_delete_removes_answers(self, client, db_session, owned):
        slug = owned.slug
        client.post(
            f"/s/{slug}",
            data=form_payload(title=["Анкета"]),
            headers={"user-agent": "delete-test"},
        )
        assert db_session.scalar(select(Answer)) is not None

        client.post(f"/s/{slug}/delete", follow_redirects=False)
        db_session.expire_all()

        assert db_session.scalars(select(Answer)).all() == []
        assert db_session.scalars(select(Response)).all() == []

    def test_public_page_is_gone(self, client, owned):
        slug = owned.slug
        client.post(f"/s/{slug}/delete", follow_redirects=False)

        assert client.get(f"/s/{slug}").status_code == 404


class TestOwnerIsolation:
    """Анкета не видна и неуправляема из чужой учётной записи.

    Раньше «чужой» получался сбросом cookie, и сервер заводил ему нового
    анонимного владельца. Теперь чужим может быть только второй вошедший,
    поэтому вход переключается явно.
    """

    def test_edit_of_stranger_is_404(self, client, db_session, owned):
        factories.login_as(client, db_session, "mallory")

        assert client.get(f"/s/{owned.slug}/edit").status_code == 404

    def test_pause_of_stranger_is_404(self, client, db_session, owned):
        factories.login_as(client, db_session, "mallory")

        response = client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        assert response.status_code == 404
        session = _session()
        try:
            assert factories.survey_by_slug(session, owned.slug).is_open is True
        finally:
            session.close()

    def test_delete_of_stranger_is_404(self, client, db_session, owned):
        factories.login_as(client, db_session, "mallory")

        response = client.post(f"/s/{owned.slug}/delete", follow_redirects=False)

        assert response.status_code == 404
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, owned.slug) is not None

    def test_pause_by_guest_asks_for_login(self, client, owned):
        client.cookies.clear()

        response = client.post(f"/s/{owned.slug}/open", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")
