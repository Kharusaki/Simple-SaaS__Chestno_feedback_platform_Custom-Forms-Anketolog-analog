"""SQL-доступ к `responses` и `answers`. Только SQL, без логики и HTTP."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from core.models import Answer, Response


def add_response(session: Session, response: Response) -> Response:
    session.add(response)
    session.flush()
    return response


def add_answers(session: Session, answers: list[Answer]) -> None:
    session.add_all(answers)
    session.flush()


def submitted_exists_for_hash(session: Session, survey_id: int, respondent_hash: str) -> bool:
    """Завершал ли уже этот респондент опрос (защита от повторной отправки)."""
    return (
        session.scalar(
            select(Response.id)
            .where(
                Response.survey_id == survey_id,
                Response.respondent_hash == respondent_hash,
                Response.submitted_at.isnot(None),
            )
            .limit(1)
        )
        is not None
    )


def submitted_exists_for_respondent(
    session: Session,
    survey_id: int,
    respondent_token: str | None,
    respondent_hash: str | None,
) -> bool:
    """Отправлял ли респондент ответ — по токену либо по хэшу.

    Обе проверки нужны: токен стабильнее хэша, но у старых ответов его нет,
    и у людей с отключёнными cookie он тоже не появится.
    """
    for column, value in (
        (Response.respondent_token, respondent_token),
        (Response.respondent_hash, respondent_hash),
    ):
        if not value:
            continue
        found = session.scalar(
            select(Response.id)
            .where(
                Response.survey_id == survey_id,
                column == value,
                Response.submitted_at.isnot(None),
            )
            .limit(1)
        )
        if found is not None:
            return True
    return False


def find_submitted_for_respondent(
    session: Session,
    survey_id: int,
    respondent_token: str | None,
    respondent_hash: str | None,
) -> Response | None:
    """Ответ конкретного респондента по опросу.

    Сначала ищем по токену из cookie: он переживает смену IP и User-Agent.
    Хэш — запасной путь для тех, у кого cookie заблокирована или кто
    заполнял анкету до появления токена.
    """
    for column, value in (
        (Response.respondent_token, respondent_token),
        (Response.respondent_hash, respondent_hash),
    ):
        if not value:
            continue
        found = session.scalar(
            select(Response)
            .where(
                Response.survey_id == survey_id,
                column == value,
                Response.submitted_at.isnot(None),
            )
            .order_by(Response.submitted_at.desc(), Response.id.desc())
            .limit(1)
        )
        if found is not None:
            return found
    return None


def list_submitted_for_respondent(
    session: Session,
    respondent_token: str | None,
    respondent_hash: str | None = None,
) -> list[Response]:
    """Все завершённые ответы респондента, свежие сверху. Для «Моих ответов»."""
    clauses = [
        Response.respondent_token == respondent_token,
        Response.respondent_hash == respondent_hash,
    ]
    query = (
        select(Response)
        .where(Response.submitted_at.isnot(None), or_(*clauses))
        .order_by(Response.submitted_at.desc(), Response.id.desc())
    )
    return list(session.scalars(query))


def count_submitted(session: Session, survey_id: int) -> int:
    return (
        session.scalar(
            select(func.count(Response.id))
            .where(Response.survey_id == survey_id, Response.submitted_at.isnot(None))
        )
        or 0
    )


def latest_submitted(session: Session, survey_id: int, limit: int = 10) -> list[Response]:
    return list(
        session.scalars(
            select(Response)
            .where(Response.survey_id == survey_id, Response.submitted_at.isnot(None))
            .order_by(Response.submitted_at.desc())
            .limit(limit)
        )
    )


def list_all_submitted(session: Session, survey_id: int) -> list[Response]:
    return list(
        session.scalars(
            select(Response)
            .where(Response.survey_id == survey_id, Response.submitted_at.isnot(None))
            .order_by(Response.submitted_at, Response.id)
        )
    )


def answers_for_questions(
    session: Session, question_ids: list[int]
) -> dict[int, list[Answer]]:
    """Все ответы по набору вопросов, сгруппированные по question_id.

    Это сводка по всем респондентам сразу. Для строки конкретного
    респондента нужен `answers_for_responses`.
    """
    if not question_ids:
        return {}
    rows = session.scalars(
        select(Answer).where(Answer.question_id.in_(question_ids))
    )
    grouped: dict[int, list[Answer]] = {question_id: [] for question_id in question_ids}
    for answer in rows:
        grouped[answer.question_id].append(answer)
    return grouped


def answers_for_responses(
    session: Session, response_ids: list[int]
) -> dict[int, list[Answer]]:
    """Все ответы по набору ответов респондентов, сгруппированные по response_id."""
    if not response_ids:
        return {}
    rows = session.scalars(
        select(Answer)
        .where(Answer.response_id.in_(response_ids))
        .order_by(Answer.response_id, Answer.question_id, Answer.id)
    )
    grouped: dict[int, list[Answer]] = {response_id: [] for response_id in response_ids}
    for answer in rows:
        grouped[answer.response_id].append(answer)
    return grouped


def answers_for_response(session: Session, response_id: int) -> list[Answer]:
    return list(
        session.scalars(
            select(Answer)
            .where(Answer.response_id == response_id)
            .order_by(Answer.question_id, Answer.id)
        )
    )
