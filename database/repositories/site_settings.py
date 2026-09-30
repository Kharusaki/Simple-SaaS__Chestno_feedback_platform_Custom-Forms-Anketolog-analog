"""SQL-доступ к единственной строке `site_settings`. Только SQL, без логики."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import SiteSettings

SINGLETON_ID = 1


def get(session: Session) -> SiteSettings | None:
    return session.scalar(select(SiteSettings).where(SiteSettings.id == SINGLETON_ID))


def get_or_create(session: Session) -> SiteSettings:
    row = get(session)
    if row is not None:
        return row
    row = SiteSettings(id=SINGLETON_ID)
    session.add(row)
    session.flush()
    return row


def set_background(session: Session, image_path: str | None) -> SiteSettings:
    row = get_or_create(session)
    row.background_image = image_path
    return row


def set_logo(session: Session, image_path: str | None) -> SiteSettings:
    row = get_or_create(session)
    row.logo_image = image_path
    return row
