from __future__ import annotations

import pytest
from sqlalchemy import select

from core.models import ANSWER_SKIPPED, Answer, Response, User
from surveys import crud, taking
from surveys.builder import BuiltQuestion
from tests import factories


def owner(db_session) -> User:
    return factories.unique_owner(db_session, "taking")


def question_map(db_session, survey) -> dict[str, object]:
    return {q.text: q for q in factories.questions_of(db_session, survey)}


def answers_by_kind(db_session, question_id: int) -> list[Answer]:
    return list(
        db_session.scalars(
            select(Answer)
            .where(Answer.question_id == question_id)
            .order_by(Answer.id)
        )
    )


def submit(session, survey, form: dict[str, list[str]], respondent="device-a"):
    return taking.submit_response(session, survey, form, respondent)


class TestPrepareAnswer:
    def test_single_accepts_only_existing_choice(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Как вам сервис?"]

        result = taking.prepare_answer(question, "Отлично")

        assert len(result) == 1
        assert result[0].kind == "single"
        assert result[0].value_text == "Отлично"

    def test_single_rejects_invented_choice(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Как вам сервис?"]

        result = taking.prepare_answer(question, "Придуманный вариант")

        assert result[0].kind == ANSWER_SKIPPED
        assert result[0].value_text is None

    def test_multiple_keeps_several_rows(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Что использовали?"]

        result = taking.prepare_answer(question, "API\nВеб-интерфейс")

        assert [item.value_text for item in result] == ["API", "Веб-интерфейс"]
        assert all(item.kind == "multiple" for item in result)

    def test_multiple_drops_unknown_and_keeps_known(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Что использовали?"]

        result = taking.prepare_answer(question, "API\nЧто-то ещё")

        assert [item.value_text for item in result] == ["API"]

    def test_yes_no_accepts_only_da_net(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Будете рекомендовать?"]

        assert taking.prepare_answer(question, "Да")[0].value_text == "Да"
        assert taking.prepare_answer(question, "Нет")[0].value_text == "Нет"
        assert taking.prepare_answer(question, "Может быть")[0].kind == ANSWER_SKIPPED

    @pytest.mark.parametrize(
        "raw,expected_kind,expected_value",
        [("7", "scale", 7), ("1", "scale", 1), ("10", "scale", 10)],
    )
    def test_scale_inside_range(self, db_session, clean_tables, raw, expected_kind, expected_value):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Оценка от 1 до 10"]

        result = taking.prepare_answer(question, raw)

        assert result[0].kind == expected_kind
        assert result[0].value_int == expected_value

    @pytest.mark.parametrize("raw", ["0", "11", "-3", "abc", "", "  "])
    def test_scale_outside_range_or_garbage(self, db_session, clean_tables, raw):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Оценка от 1 до 10"]

        result = taking.prepare_answer(question, raw)

        assert result[0].kind == ANSWER_SKIPPED
        assert result[0].value_int is None

    def test_text_is_trimmed_and_capped(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Комментарий"]

        result = taking.prepare_answer(question, "  " + "а" * 5000 + "  ")

        assert result[0].kind == "text"
        assert len(result[0].value_text) == 2000

    def test_empty_text_becomes_skipped(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Комментарий"]

        assert taking.prepare_answer(question, "   ")[0].kind == ANSWER_SKIPPED

    def test_single_with_two_values_becomes_skipped(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = question_map(db_session, survey)["Как вам сервис?"]

        result = taking.prepare_answer(question, "Отлично\nХорошо")

        assert len(result) == 1
        assert result[0].kind == ANSWER_SKIPPED


class TestSubmitResponse:
    def test_saves_all_five_types(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = question_map(db_session, survey)

        result = submit(
            db_session,
            survey,
            {
                f"q{questions['Как вам сервис?'].id}": ["Отлично"],
                f"q{questions['Что использовали?'].id}": ["API", "Веб-интерфейс"],
                f"q{questions['Будете рекомендовать?'].id}": ["Да"],
                f"q{questions['Оценка от 1 до 10'].id}": ["9"],
                f"q{questions['Комментарий'].id}": ["Всё отлично"],
            },
        )

        assert result.saved == 6
        assert result.skipped == 0
        assert result.kinds == {
            "single": 1, "multiple": 2, "yes_no": 1, "scale": 1, "text": 1
        }

        assert [
            (a.kind, a.value_text, a.value_int)
            for a in answers_by_kind(db_session, questions["Как вам сервис?"].id)
        ] == [("single", "Отлично", None)]
        assert [
            a.value_text for a in answers_by_kind(db_session, questions["Что использовали?"].id)
        ] == ["API", "Веб-интерфейс"]
        assert answers_by_kind(db_session, questions["Оценка от 1 до 10"].id)[0].value_int == 9

    def test_missing_answers_become_skipped_not_errors(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = question_map(db_session, survey)

        result = submit(
            db_session, survey, {f"q{questions['Как вам сервис?'].id}": ["Плохо"]}
        )

        assert result.saved == 1
        assert result.skipped == 4
        for text, question in questions.items():
            if text == "Как вам сервис?":
                continue
            assert answers_by_kind(db_session, question.id)[0].kind == ANSWER_SKIPPED

    def test_response_row_is_created_once(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        submit(db_session, survey, {})

        responses = list(db_session.scalars(select(Response)))
        assert len(responses) == 1
        assert responses[0].submitted_at is not None
        assert responses[0].respondent_hash == "device-a"

    def test_answers_are_linked_to_questions_of_this_survey_only(
        self, db_session, clean_tables
    ):
        first, _ = factories.make_survey(db_session, owner(db_session))
        second, _ = factories.make_survey(db_session, owner(db_session))

        submit(db_session, first, {})

        second_answers = list(
            db_session.scalars(
                select(Answer)
                .join(Response, Answer.response_id == Response.id)
                .where(Response.survey_id == second.id)
            )
        )
        assert second_answers == []

    def test_closed_survey_is_rejected(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        survey.is_open = False
        db_session.commit()

        with pytest.raises(taking.SurveyClosedError):
            submit(db_session, survey, {})

        assert db_session.scalar(select(Response)) is None

    def test_second_submission_from_same_device_is_rejected(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        submit(db_session, survey, {}, respondent="device-a")

        with pytest.raises(taking.AlreadyRespondedError):
            submit(db_session, survey, {}, respondent="device-a")

        assert len(list(db_session.scalars(select(Response)))) == 1

    def test_other_device_can_still_answer(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        submit(db_session, survey, {}, respondent="device-a")
        submit(db_session, survey, {}, respondent="device-b")

        assert len(list(db_session.scalars(select(Response)))) == 2

    def test_unsubmitted_response_does_not_block_answers(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        db_session.add(Response(survey_id=survey.id, respondent_hash="device-a"))
        db_session.commit()

        result = submit(db_session, survey, {}, respondent="device-a")

        assert result.saved == 0
        assert result.skipped == 5

    def test_answers_cascade_on_response_delete(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        result = submit(db_session, survey, {})

        db_session.delete(result.response)
        db_session.commit()

        assert db_session.scalar(select(Answer)) is None


class TestPublicRoutes:
    def test_public_page_is_open_without_cookie(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))

        response = client.get(f"/s/{survey.slug}")

        assert response.status_code == 200
        assert "Анкета после покупки" in response.text
        assert "Как вам сервис?" in response.text
        assert "Отлично" in response.text
        assert settings_cookie_absent(client)

    def test_public_page_shows_scale_and_yes_no(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))

        response = client.get(f"/s/{survey.slug}")

        assert "Будете рекомендовать?" in response.text
        assert ">7</span>" in response.text or "7" in response.text
        assert "Да" in response.text and "Нет" in response.text

    def test_unknown_slug_returns_404(self, client, clean_tables):
        response = client.get("/s/nosuchslug")

        assert response.status_code == 404
        assert "Страница не найдена" in response.text

    def test_closed_survey_page_returns_403(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        survey.is_open = False
        db_session.commit()

        response = client.get(f"/s/{survey.slug}")

        assert response.status_code == 403
        assert "Анкета закрыта" in response.text

    def test_submission_redirects_to_thanks(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = factories.questions_of(db_session, survey)[0]

        response = client.post(
            f"/s/{survey.slug}",
            data={f"q{question.id}": ["Отлично"]},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == f"/s/{survey.slug}/thanks"

    def test_thanks_page_renders(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))

        response = client.get(f"/s/{survey.slug}/thanks")

        assert response.status_code == 200
        assert "Спасибо за ответ" in response.text

    def test_duplicate_submission_returns_409(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        question = factories.questions_of(db_session, survey)[0]
        data = {f"q{question.id}": ["Отлично"]}

        first = client.post(f"/s/{survey.slug}", data=data, follow_redirects=False)
        second = client.post(f"/s/{survey.slug}", data=data)

        assert first.status_code == 303
        assert second.status_code == 409
        assert "Ответ уже отправлен" in second.text

    def test_submission_to_unknown_slug_returns_404(self, client, clean_tables):
        response = client.post("/s/nosuchslug", data={"q1": ["x"]})

        assert response.status_code == 404

    def test_answers_survive_request(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = question_map(db_session, survey)
        data = {
            f"q{questions['Как вам сервис?'].id}": ["Хорошо"],
            f"q{questions['Что использовали?'].id}": ["API"],
            f"q{questions['Будете рекомендовать?'].id}": ["Нет"],
            f"q{questions['Оценка от 1 до 10'].id}": ["4"],
            f"q{questions['Комментарий'].id}": ["Норм"],
        }

        client.post(f"/s/{survey.slug}", data=data, follow_redirects=False)
        db_session.expire_all()

        stored = list(db_session.scalars(select(Answer)))
        assert len(stored) == 5
        assert {a.kind for a in stored} == {"single", "multiple", "yes_no", "scale", "text"}
        assert db_session.scalar(select(Response).where(Response.submitted_at.isnot(None)))


def settings_cookie_absent(client) -> bool:
    from core.config import settings

    return settings.cookie_name not in client.cookies
