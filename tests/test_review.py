"""Проверка подсчёта балла: `surveys/review.py`."""

from __future__ import annotations

import pytest

from core.models import (
    ANSWER_SKIPPED,
    REVIEW_EXPLAIN,
    REVIEW_NONE,
    REVIEW_SCORE,
    Answer,
    Choice,
    Question,
    Survey,
)
from surveys import review


def make_survey(mode: str = REVIEW_NONE) -> Survey:
    return Survey(slug="quizslug", title="Тест", owner_id=1, review_mode=mode)


_next_id = iter(range(1, 10_000))


def make_question(
    survey: Survey,
    qtype: str,
    *,
    position: int = 0,
    explanation: str = "",
    correct_text: str | None = None,
    correct_int: int | None = None,
    choices: list[tuple[str, bool]] | None = None,
) -> Question:
    """Вопрос с настоящим `id`.

    Объекты не попадают в базу, поэтому `id` остался бы `None` у всех
    вопросов подряд, и ответы в `given` сливались бы под одним ключом.
    """
    question = Question(
        id=next(_next_id),
        survey_id=survey.id,
        position=position,
        text=f"Вопрос {position}",
        type=qtype,
        is_required=True,
        explanation=explanation or None,
        correct_value_text=correct_text,
        correct_value_int=correct_int,
    )
    for index, (text, is_correct) in enumerate(choices or []):
        question.choices.append(
            Choice(position=index, text=text, is_correct=is_correct)
        )
    return question


def answers_for(question: Question, *rows: tuple[str, str | int | None]) -> list[Answer]:
    """Собирает Answer так же, как это делает `taking.submit_response`."""
    out: list[Answer] = []
    for kind, value in rows:
        out.append(
            Answer(
                response_id=1,
                question_id=question.id,
                kind=kind,
                value_text=value if isinstance(value, str) or value is None else None,
                value_int=value if isinstance(value, int) else None,
            )
        )
    return out


def build(questions: list[Question], given: dict[int, list[Answer]], mode: str):
    return review.build_review(questions, given, mode)


def test_mode_none_returns_empty_result():
    survey = make_survey(REVIEW_NONE)
    question = make_question(
        survey, "single", choices=[("Верно", True), ("Нет", False)]
    )
    result = build([question], {question.id: answers_for(question, ("single", "Верно"))}, REVIEW_NONE)
    assert result.is_active is False
    assert result.total == 0
    assert result.questions == []


def test_single_correct_and_wrong():
    survey = make_survey(REVIEW_SCORE)
    right = make_question(survey, "single", choices=[("Да", True), ("Нет", False)])
    wrong = make_question(
        survey, "single", position=1, choices=[("Да", True), ("Нет", False)]
    )
    given = {
        right.id: answers_for(right, ("single", "Да")),
        wrong.id: answers_for(wrong, ("single", "Нет")),
    }
    result = build([right, wrong], given, REVIEW_SCORE)
    assert (result.total, result.correct) == (2, 1)
    assert result.percent == 50


def test_multiple_requires_exact_set():
    survey = make_survey(REVIEW_SCORE)
    question = make_question(
        survey,
        "multiple",
        choices=[("Раз", True), ("Два", True), ("Три", False)],
    )
    exact = build(
        [question],
        {question.id: answers_for(question, ("multiple", "Раз"), ("multiple", "Два"))},
        REVIEW_SCORE,
    )
    partial = build(
        [question], {question.id: answers_for(question, ("multiple", "Раз"))}, REVIEW_SCORE
    )
    extra = build(
        [question],
        {
            question.id: answers_for(
                question, ("multiple", "Раз"), ("multiple", "Два"), ("multiple", "Три")
            )
        },
        REVIEW_SCORE,
    )
    assert (exact.correct, partial.correct, extra.correct) == (1, 0, 0)
    assert exact.total == partial.total == extra.total == 1


def test_yes_no_and_scale():
    survey = make_survey(REVIEW_SCORE)
    yes_no = make_question(survey, "yes_no", correct_text="Да")
    scale = make_question(survey, "scale", position=1, correct_int=7)
    given = {
        yes_no.id: answers_for(yes_no, ("yes_no", "Да")),
        scale.id: answers_for(scale, ("scale", 7)),
    }
    result = build([yes_no, scale], given, REVIEW_SCORE)
    assert (result.total, result.correct) == (2, 2)

    wrong = build(
        [yes_no, scale],
        {
            yes_no.id: answers_for(yes_no, ("yes_no", "Нет")),
            scale.id: answers_for(scale, ("scale", 3)),
        },
        REVIEW_SCORE,
    )
    assert wrong.correct == 0


def test_text_question_is_not_graded():
    survey = make_survey(REVIEW_SCORE)
    text = make_question(survey, "text", explanation="Пояснение есть")
    result = build([text], {text.id: answers_for(text, ("text", "любой текст"))}, REVIEW_SCORE)
    assert result.total == 0
    assert result.correct == 0
    assert result.percent == 0


def test_question_without_correct_answer_is_not_graded():
    survey = make_survey(REVIEW_SCORE)
    unmarked = make_question(survey, "single", choices=[("Да", False), ("Нет", False)])
    result = build(
        [unmarked], {unmarked.id: answers_for(unmarked, ("single", "Да"))}, REVIEW_SCORE
    )
    assert result.total == 0
    assert result.questions == []


def test_skipped_answer_counts_as_wrong_but_not_as_given():
    survey = make_survey(REVIEW_SCORE)
    question = make_question(survey, "single", choices=[("Да", True), ("Нет", False)])
    result = build(
        [question], {question.id: answers_for(question, ("skipped", None))}, REVIEW_SCORE
    )
    assert result.total == 1
    assert result.correct == 0


def test_score_mode_hides_per_question_details():
    survey = make_survey(REVIEW_SCORE)
    question = make_question(
        survey, "single", explanation="Верно, потому что так", choices=[("Да", True), ("Нет", False)]
    )
    result = build(
        [question], {question.id: answers_for(question, ("single", "Нет"))}, REVIEW_SCORE
    )
    assert result.shows_answers is False
    assert result.questions == []


def test_explain_mode_shows_correct_answer_and_explanation():
    survey = make_survey(REVIEW_EXPLAIN)
    question = make_question(
        survey,
        "single",
        explanation="Столица Франции — Париж",
        choices=[("Париж", True), ("Лондон", False)],
    )
    result = build(
        [question], {question.id: answers_for(question, ("single", "Лондон"))}, REVIEW_EXPLAIN
    )
    assert result.shows_answers is True
    assert result.questions, "в режиме explain разбор обязателен"
    item = result.questions[0]
    assert item.correct_text == "Париж"
    assert item.given_text == "Лондон"
    assert item.explanation == "Столица Франции — Париж"
    assert item.is_correct is False


def test_explain_mode_also_lists_ungraded_questions_for_context():
    survey = make_survey(REVIEW_EXPLAIN)
    graded = make_question(survey, "single", choices=[("Да", True), ("Нет", False)])
    plain = make_question(survey, "text", position=1, explanation="Открытый вопрос")
    result = build(
        [graded, plain],
        {
            graded.id: answers_for(graded, ("single", "Да")),
            plain.id: answers_for(plain, ("text", "мой ответ")),
        },
        REVIEW_EXPLAIN,
    )
    assert result.total == 1
    assert [item.position for item in result.questions] == [0, 1]


def test_unknown_mode_falls_back_to_none():
    survey = make_survey("какая-то дичь")
    question = make_question(survey, "single", choices=[("Да", True), ("Нет", False)])
    result = build(
        [question], {question.id: answers_for(question, ("single", "Да"))}, "какая-то дичь"
    )
    assert result.mode == REVIEW_NONE
    assert result.is_active is False


def test_grade_label_text():
    survey = make_survey(REVIEW_SCORE)
    a = make_question(survey, "single", choices=[("Да", True), ("Нет", False)])
    b = make_question(
        survey, "single", position=1, choices=[("Да", True), ("Нет", False)]
    )
    result = build(
        [a, b],
        {a.id: answers_for(a, ("single", "Да")), b.id: answers_for(b, ("single", "Нет"))},
        REVIEW_SCORE,
    )
    assert review.grade_label(result) == "1 из 2 · 50%"


def test_mode_labels_cover_all_modes():
    from core.models import REVIEW_MODES

    assert set(review.REVIEW_MODE_LABELS) == set(REVIEW_MODES)
