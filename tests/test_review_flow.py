"""Проверка ответов как пользовательский сценарий, от формы до страницы итога.

Чистый подсчёт балла разбирает `test_review.py`. Здесь проверяется то, что
ломается между слоями: форма не прислала правильные ответы, режим потерялся
при перерисовке после ошибки, а разбор не показался респонденту.

Отдельно закрыт неприятный сценарий: автор включает проверку в анкете, где
отмечать нечего. Без проверки такая анкета выглядела бы рабочей, а
респондент получал бы пустую страницу итога.
"""

from __future__ import annotations

import pytest

from core.models import Survey
from surveys import crud
from tests import factories


def quiz_payload(**overrides) -> dict:
    """Форма теста: один вопрос с одним верным вариантом и шкала."""
    payload = {
        "title": ["Тест знаний"],
        "review_mode": ["explain"],
        "q0_type": ["single"],
        "q0_text": ["Столица Франции?"],
        "q0_choices": ["Париж\nЛондон"],
        "q0_correct": ["Париж"],
        "q0_explanation": ["Париж — столица Франции с 987 года."],
        "q0_is_required": ["1"],
        "q1_type": ["scale"],
        "q1_text": ["Оцените сложность"],
        "q1_scale": ["1-10"],
        "q1_correct_scale": ["7"],
    }
    payload.update(overrides)
    return payload


def post_form(client, url: str, payload: dict, follow_redirects: bool = False):
    data: dict = {}
    files: dict = {}
    for key, value in payload.items():
        if isinstance(value, tuple):
            files[key] = value
        else:
            data[key] = value
    return client.post(url, data=data, files=files, follow_redirects=follow_redirects)


def reload_survey(db_session) -> Survey:
    """Читает анкету заново.

    Приложение коммитит в своей сессии, а `expire_on_commit` в тестах
    выключен: без `populate_existing` вернётся закэшированный объект со
    старыми атрибутами, и тест будет проверять не то, что записано в базу.
    """
    db_session.expire_all()
    return db_session.query(Survey).one()


@pytest.fixture
def creator(client, db_session, clean_tables):
    factories.login_as(client, db_session)
    return db_session.query(Survey).count()


class TestCreateWithReviewMode:
    def test_correct_answer_is_saved(self, client, db_session, clean_tables, creator):
        response = post_form(client, "/surveys/new", quiz_payload())

        assert response.status_code == 303
        survey = db_session.query(Survey).one()
        assert survey.review_mode == "explain"

        questions = factories.questions_of(db_session, survey)
        capital = next(q for q in questions if q.text == "Столица Франции?")
        assert [choice.text for choice in capital.correct_choices] == ["Париж"]
        assert capital.explanation.startswith("Париж — столица")

        scale = next(q for q in questions if q.text == "Оцените сложность")
        assert scale.correct_value_int == 7

    def test_text_question_is_never_graded(self, client, db_session, clean_tables, creator):
        post_form(client, "/surveys/new", quiz_payload(
            q2_type=["text"],
            q2_text=["Почему?"],
            q2_correct=["потому что"],
        ))

        survey = db_session.query(Survey).one()
        free = next(q for q in factories.questions_of(db_session, survey) if q.text == "Почему?")

        assert free.correct_value_text is None

    def test_mode_survives_validation_error(self, client, db_session, clean_tables, creator):
        """Ошибка валидации перерисовывает форму — режим и галочки обязаны
        остаться, иначе автор потеряет всю разметку одним кликом."""
        response = post_form(client, "/surveys/new", quiz_payload(title=[""]))

        assert response.status_code == 400
        assert 'value="explain" selected' in response.text.replace("\n", "").replace("  ", " ")
        assert "Париж" in response.text

    def test_review_without_correct_answers_is_refused(self, client, db_session, clean_tables, creator):
        response = post_form(client, "/surveys/new", {
            "title": ["Тест знаний"],
            "review_mode": ["score"],
            "q0_type": ["single"],
            "q0_text": ["Столица Франции?"],
            "q0_choices": ["Париж\nЛондон"],
        })

        assert response.status_code == 400
        assert db_session.query(Survey).count() == 0

    def test_mode_none_needs_no_correct_answers(self, client, db_session, clean_tables, creator):
        response = post_form(client, "/surveys/new", {
            "title": ["Обычная анкета"],
            "review_mode": ["none"],
            "q0_type": ["single"],
            "q0_text": ["Как вам?"],
            "q0_choices": ["Хорошо\nПлохо"],
        })

        assert response.status_code == 303
        assert reload_survey(db_session).review_mode == "none"


class TestTakingShowsResult:
    def _quiz(self, client, db_session, **overrides):
        post_form(client, "/surveys/new", quiz_payload(**overrides))
        return db_session.query(Survey).one()

    def test_correct_answer_shows_score_and_explanation(self, client, db_session, clean_tables, creator):
        survey = self._quiz(client, db_session)

        client.post(
            f"/s/{survey.slug}",
            data={"q0": ["Париж"], "q1": ["7"]},
            follow_redirects=True,
        )

        page = client.get(f"/s/{survey.slug}/thanks")
        assert page.status_code == 200
        assert "2" in page.text
        assert "Париж — столица Франции" in page.text

    def test_wrong_answer_loses_the_point(self, client, db_session, clean_tables, creator):
        survey = self._quiz(client, db_session)

        client.post(
            f"/s/{survey.slug}",
            data={"q0": ["Лондон"], "q1": ["2"]},
            follow_redirects=True,
        )

        page = client.get(f"/s/{survey.slug}/thanks")
        assert "0" in page.text
        assert "Париж" in page.text

    def test_score_mode_hides_explanation(self, client, db_session, clean_tables, creator):
        survey = self._quiz(client, db_session, review_mode=["score"])

        client.post(
            f"/s/{survey.slug}",
            data={"q0": ["Лондон"], "q1": ["7"]},
            follow_redirects=True,
        )

        page = client.get(f"/s/{survey.slug}/thanks")
        assert "Париж — столица Франции" not in page.text

    def test_mode_none_shows_no_result(self, client, db_session, clean_tables, creator):
        survey = self._quiz(client, db_session, review_mode=["none"])

        client.post(
            f"/s/{survey.slug}",
            data={"q0": ["Париж"], "q1": ["7"]},
            follow_redirects=True,
        )

        page = client.get(f"/s/{survey.slug}/thanks")
        assert "Париж — столица Франции" not in page.text
        assert "Ваш балл" not in page.text

    def test_result_is_not_revealed_before_answers(self, client, db_session, clean_tables, creator):
        survey = self._quiz(client, db_session)

        page = client.get(f"/s/{survey.slug}")

        assert page.status_code == 200
        assert "Париж — столица Франции" not in page.text


class TestEditReviewMode:
    def test_owner_can_turn_check_on_after_publish(self, client, db_session, clean_tables, creator):
        post_form(client, "/surveys/new", {
            "title": ["Анкета"],
            "review_mode": ["none"],
            "q0_type": ["single"],
            "q0_text": ["Столица Франции?"],
            "q0_choices": ["Париж\nЛондон"],
        })
        survey = db_session.query(Survey).one()

        response = post_form(client, f"/s/{survey.slug}/edit", {
            "title": ["Анкета"],
            "review_mode": ["score"],
            "is_open": ["1"],
            "q0_type": ["single"],
            "q0_text": ["Столица Франции?"],
            "q0_choices": ["Париж\nЛондон"],
            "q0_correct": ["Париж"],
        })

        assert response.status_code == 303
        assert reload_survey(db_session).review_mode == "score"

    def test_locked_survey_reports_when_nothing_to_grade(self, client, db_session, clean_tables, creator):
        post_form(client, "/surveys/new", {
            "title": ["Анкета"],
            "review_mode": ["none"],
            "q0_type": ["single"],
            "q0_text": ["Как вам?"],
            "q0_choices": ["Хорошо\nПлохо"],
        })
        survey = db_session.query(Survey).one()
        client.post(f"/s/{survey.slug}", data={"q0": ["Хорошо"]}, follow_redirects=True)

        response = post_form(client, f"/s/{survey.slug}/edit", {
            "title": ["Анкета"],
            "review_mode": ["explain"],
            "is_open": ["1"],
        })

        assert response.status_code == 400
        assert reload_survey(db_session).review_mode == "none"

    def test_editor_shows_current_mode(self, client, db_session, clean_tables, creator):
        post_form(client, "/surveys/new", quiz_payload())
        survey = db_session.query(Survey).one()

        page = client.get(f"/s/{survey.slug}/edit")

        assert page.status_code == 200
        assert "Балл и правильные ответы" in page.text
