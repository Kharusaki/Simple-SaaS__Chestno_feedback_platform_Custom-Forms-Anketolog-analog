"""Выгрузка ответов в CSV. Значение, формулы и разделители экранируются."""

from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session

from database.repositories import answers as answers_repo
from database.repositories import questions as questions_repo
from core.models import ANSWER_SKIPPED, Question, Survey

CSV_PREFIX = "\ufeff"


def _cell_text(answers: list) -> str:
    values = [
        row.value_text if row.value_text is not None else str(row.value_int)
        for row in answers
        if row.kind != ANSWER_SKIPPED
    ]
    return ", ".join(values)


def _by_response_and_question(
    session: Session, responses: list, questions: list[Question]
) -> dict:
    """Ключ — (response_id, question_id). Без этого строки разных
    респондентов смешиваются: вопрос один, а ответы чужие."""
    question_ids = [question.id for question in questions]
    if not responses or not question_ids:
        return {}
    grouped = answers_repo.answers_for_responses(
        session, [response.id for response in responses]
    )
    cells: dict[tuple[int, int], list] = {}
    for response_id, rows in grouped.items():
        for row in rows:
            if row.question_id in question_ids:
                cells.setdefault((response_id, row.question_id), []).append(row)
    return cells


def build_csv(session: Session, survey: Survey) -> str:
    """Одна строка на респондента, один столбец на вопрос."""
    questions = questions_repo.list_for_survey(session, survey.id)
    responses = answers_repo.list_all_submitted(session, survey.id)

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow(["Ответ №", "Дата"] + [question.text for question in questions])

    cells = _by_response_and_question(session, responses, questions)
    for response in responses:
        submitted = response.submitted_at.strftime("%d.%m.%Y %H:%M") if response.submitted_at else ""
        row = [str(response.id), submitted]
        for question in questions:
            row.append(_cell_text(cells.get((response.id, question.id), [])))
        writer.writerow(row)

    return CSV_PREFIX + buffer.getvalue()
