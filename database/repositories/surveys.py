"""SQL-доступ к таблице `surveys`. Только SQL, без бизнес-логики и HTTP."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models import Response, Survey


def get_by_id(session: Session, survey_id: int) -> Survey | None:
    return session.get(Survey, survey_id)


def get_by_slug(session: Session, slug: str) -> Survey | None:
    if not slug:
        return None
    return session.scalar(select(Survey).where(Survey.slug == slug))


def slug_exists(session: Session, slug: str) -> bool:
    return session.scalar(select(Survey.id).where(Survey.slug == slug).limit(1)) is not None


def add(session: Session, survey: Survey) -> Survey:
    session.add(survey)
    session.flush()
    return survey


def delete(session: Session, survey: Survey) -> None:
    session.delete(survey)
    session.flush()


def list_by_owner(session: Session, owner_id: int) -> list[Survey]:
    return list(
        session.scalars(
            select(Survey)
            .where(Survey.owner_id == owner_id)
            .order_by(Survey.created_at.desc())
        )
    )


def list_all(session: Session) -> list[Survey]:
    """Все опросы по дате создания. Для раздела модерации."""
    return list(session.scalars(select(Survey).order_by(Survey.created_at.desc())))


def response_counts(session: Session, survey_ids: list[int]) -> dict[int, int]:
    """Сколько завершённых ответов у каждого опроса — один запрос на весь список."""
    if not survey_ids:
        return {}
    rows = session.execute(
        select(Response.survey_id, func.count(Response.id))
        .where(Response.survey_id.in_(survey_ids), Response.submitted_at.isnot(None))
        .group_by(Response.survey_id)
    )
    return {survey_id: count for survey_id, count in rows}
