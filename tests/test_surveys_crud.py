from __future__ import annotations

import pytest
from sqlalchemy import select

from core.config import settings
from database.repositories import questions as questions_repo
from core.models import Question, Survey, User
from surveys import crud
from tests import factories


def form_payload(**overrides) -> dict:
    payload = {
        "title": ["Анкета после покупки"],
        "description": ["Помогите нам стать лучше"],
        "q1_type": ["single"],
        "q1_text": ["Как вам сервис?"],
        "q1_choices": ["Отлично\nХорошо\nПлохо"],
        "q1_is_required": ["1"],
        "q2_type": ["scale"],
        "q2_text": ["Оценка от 1 до 10"],
        "q2_scale": ["1-10"],
    }
    payload.update(overrides)
    return payload


class TestCreateSurvey:
    """Создание доступно только вошедшему, поэтому вход здесь обязателен."""

    @pytest.fixture(autouse=True)
    def _signed_in(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)

    def test_creates_survey_with_slug_and_keys(self, client, db_session, clean_tables):
        response = client.post("/surveys/new", data=form_payload(), follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"].startswith("/s/")
        assert "/edit?created=1" in response.headers["location"]

        survey = db_session.scalar(select(Survey))
        assert survey is not None
        assert len(survey.slug) == settings.slug_length
        assert survey.title == "Анкета после покупки"
        assert survey.is_open is True

        key = factories.primary_key_of(db_session, survey)
        assert key is not None
        assert key.can_edit is True
        assert len(key.token) == 48

    def test_persists_questions_and_choices(self, client, db_session, clean_tables):
        client.post("/surveys/new", data=form_payload())

        survey = db_session.scalar(select(Survey))
        questions = factories.questions_of(db_session, survey)

        assert [q.text for q in questions] == ["Как вам сервис?", "Оценка от 1 до 10"]
        assert [q.position for q in questions] == [0, 1]
        assert questions[0].type == "single"
        assert factories.choices_of(db_session, questions[0]) == ["Отлично", "Хорошо", "Плохо"]
        assert questions[1].scale_min == 1
        assert questions[1].scale_max == 10

    def test_survey_belongs_to_signed_in_owner(self, client, db_session, clean_tables):
        client.post("/surveys/new", data=form_payload())

        survey = db_session.scalar(select(Survey))
        owner = db_session.get(User, survey.owner_id)
        assert owner is not None
        assert owner.username == "anna"
        assert owner.password_hash
        assert owner.role == "creator"

    def test_slug_is_unique_per_survey(self, client, db_session, clean_tables):
        slugs = {client.post("/surveys/new", data=form_payload()) for _ in range(3)}
        assert len(slugs) == 3

        stored = [row[0] for row in db_session.execute(select(Survey.slug))]
        assert len(set(stored)) == 3

    def test_invalid_form_returns_400_and_keeps_input(
        self, client, db_session, clean_tables
    ):
        response = client.post("/surveys/new", data=form_payload(title=["   "]))

        assert response.status_code == 400
        assert "Введите название" in response.text
        assert db_session.scalar(select(Survey)) is None

    def test_invalid_form_preserves_typed_questions(self, client, clean_tables):
        response = client.post("/surveys/new", data=form_payload(title=[""]))

        assert "Как вам сервис?" in response.text
        assert "Отлично" in response.text

    def test_no_questions_returns_400(self, client, clean_tables):
        response = client.post(
            "/surveys/new", data={"title": ["Опрос"], "description": [""]}
        )

        assert response.status_code == 400
        assert "Добавьте хотя бы один вопрос" in response.text

    def test_choice_question_without_variants_returns_400(self, client, db_session, clean_tables):
        response = client.post(
            "/surveys/new",
            data={
                "title": ["Опрос"],
                "q1_type": ["single"],
                "q1_text": ["Вопрос"],
                "q1_choices": [""],
            },
        )

        assert response.status_code == 400
        assert db_session.scalar(select(Survey)) is None


class TestOwnerAccess:
    """Раньше владелец появлялся сам при первом заходе на `/dashboard`.
    Теперь для этого нужен вход, и проверяется именно он."""

    def test_dashboard_closed_for_guest(self, client, clean_tables):
        response = client.get("/dashboard", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")

    def test_guest_visit_creates_no_user(self, client, db_session, clean_tables):
        client.get("/dashboard", follow_redirects=False)
        client.get("/surveys/new", follow_redirects=False)

        assert db_session.query(User).count() == 0

    def test_session_cookie_is_protected(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)
        response = client.post("/logout", follow_redirects=False)
        response = client.post(
            "/login",
            data={"username": "anna", "password": "secret1"},
            follow_redirects=False,
        )

        assert settings.cookie_name in response.cookies
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"].replace(
            "SameSite=Lax", "SameSite=lax"
        )

    def test_create_survey_closed_for_guest(self, client, clean_tables):
        response = client.post(
            "/surveys/new", data=form_payload(), follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")

    def test_surveys_are_scoped_to_account(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)
        client.post("/surveys/new", data=form_payload())
        assert "Анкета после покупки" in client.get("/dashboard").text

        client.cookies.clear()
        response = client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 303
        assert "Анкета после покупки" not in client.get("/login").text
        assert "Пока нет ни одной анкеты" not in client.get("/login").text


class TestDashboard:
    def test_empty_state(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)

        response = client.get("/dashboard")

        assert response.status_code == 200
        assert "Пока нет ни одной анкеты" in response.text

    def test_shows_survey_with_counts(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)
        client.post("/surveys/new", data=form_payload())

        response = client.get("/dashboard")

        assert "Анкета после покупки" in response.text
        assert "2 вопроса" in response.text
        assert "0 ответов" in response.text

    def test_links_to_stats_page(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey = factories.make_survey(db_session, mine)[0]

        response = client.get("/dashboard")

        assert f'/s/{survey.slug}/stats' in response.text

    def test_new_survey_form_renders(self, client, clean_tables):
        response = client.get("/surveys/new")

        assert response.status_code == 200
        assert "Новая анкета" in response.text
        assert "Возможные ответы" not in response.text
        assert 'name="q0_type"' in response.text

    def test_create_survey_rolls_back_on_failure(self, db_session, clean_tables, monkeypatch):
        owner = factories.unique_owner(db_session, "atomic")

        original = questions_repo.add
        calls = {"n": 0}

        def failing_add(session, question):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("сбой БД при создании вопроса")
            return original(session, question)

        monkeypatch.setattr(questions_repo, "add", failing_add)

        with pytest.raises(RuntimeError):
            factories.run_async(crud.create_survey(db_session, owner, factories.make_payload()))
        db_session.rollback()

        assert calls["n"] == 2
        assert db_session.scalar(select(Survey)) is None
        assert db_session.scalar(select(Question)) is None

    def test_successful_create_persists_everything(self, db_session, clean_tables):
        owner = factories.unique_owner(db_session, "success")

        survey, key = factories.run_async(
            crud.create_survey(db_session, owner, factories.make_payload())
        )

        assert db_session.scalar(select(Survey)) is not None
        assert len(factories.questions_of(db_session, survey)) == 5
        assert key.can_edit is True


def test_question_cascade_on_survey_delete(db_session, clean_tables):
    owner = factories.unique_owner(db_session, "cascade")
    survey, _ = factories.make_survey(db_session, owner)
    survey_id = survey.id

    db_session.delete(survey)
    db_session.commit()

    assert db_session.scalar(select(Question).where(Question.survey_id == survey_id)) is None
