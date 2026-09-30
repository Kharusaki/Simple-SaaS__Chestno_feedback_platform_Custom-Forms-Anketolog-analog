"""SQL-доступ к таблице `survey_keys`. Только SQL, без бизнес-логики и HTTP."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import SurveyKey


def get_by_token(session: Session, token: str) -> SurveyKey | None:
    if not token:
        return None
    return session.scalar(select(SurveyKey).where(SurveyKey.token == token))


def get_by_id(session: Session, key_id: int) -> SurveyKey | None:
    return session.get(SurveyKey, key_id)


def list_for_survey(session: Session, survey_id: int) -> list[SurveyKey]:
    return list(
        session.scalars(
            select(SurveyKey)
            .where(SurveyKey.survey_id == survey_id)
            .order_by(SurveyKey.created_at, SurveyKey.id)
        )
    )


def find_by_label(session: Session, survey_id: int, label: str) -> SurveyKey | None:
    return session.scalar(
        select(SurveyKey).where(SurveyKey.survey_id == survey_id, SurveyKey.label == label)
    )


def add(session: Session, key: SurveyKey) -> SurveyKey:
    session.add(key)
    session.flush()
    return key


def delete(session: Session, key: SurveyKey) -> None:
    session.delete(key)
    session.flush()
