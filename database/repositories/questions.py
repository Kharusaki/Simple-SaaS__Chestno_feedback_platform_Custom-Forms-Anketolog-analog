"""SQL-доступ к `questions` и `choices`. Только SQL, без бизнес-логики и HTTP."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from core.models import Choice, Question


def get_by_id(session: Session, question_id: int) -> Question | None:
    return session.get(Question, question_id)


def add(session: Session, question: Question) -> Question:
    session.add(question)
    session.flush()
    return question


def add_choices(
    session: Session,
    question: Question,
    texts: list[str],
    correct: set[str] | None = None,
) -> None:
    """Добавляет варианты ответа, помечая верные.

    `correct` — множество текстов верных вариантов. Пустое множество
    означает обычный опрос без проверки, поэтому флаг остаётся `False`.
    """
    marked = correct or set()
    for position, text in enumerate(texts):
        session.add(
            Choice(
                question_id=question.id,
                position=position,
                text=text,
                is_correct=text in marked,
            )
        )
    session.flush()


def list_for_survey(session: Session, survey_id: int) -> list[Question]:
    return list(
        session.scalars(
            select(Question)
            .where(Question.survey_id == survey_id)
            .order_by(Question.position)
            .options(selectinload(Question.choices))
        )
    )


def count_for_survey(session: Session, survey_id: int) -> int:
    return (
        session.scalar(
            select(func.count(Question.id)).where(Question.survey_id == survey_id)
        )
        or 0
    )


def delete_for_survey(session: Session, survey_id: int) -> None:
    """Удаляет вопросы анкеты вместе с вариантами и ответами (CASCADE)."""
    for question in session.scalars(
        select(Question).where(Question.survey_id == survey_id)
    ):
        session.delete(question)
    session.flush()
