"""Сервис оформления сайта: фон и логотип.

Хранит и отдаёт только то, что относится ко всему сайту. Обложка анкеты —
не его часть: у неё другой владелец, и меняет её хозяин анкеты в редакторе.
Файл лежит в том же `media/`, но запись настроек сайта и `surveys.cover_image`
смешивать нельзя.

Бизнес-логика без знания про HTTP.
"""

from __future__ import annotations

import logging

from fastapi import UploadFile
from sqlalchemy.orm import Session

from database.repositories import site_settings as settings_repo
from core.images import ImageView, replace_image, to_view
from core.models import SiteSettings

logger = logging.getLogger(__name__)

__all__ = [
    "ImageView",
    "get_settings_row",
    "set_background",
    "set_logo",
    "to_view",
]


def get_settings_row(session: Session) -> SiteSettings | None:
    return settings_repo.get(session)


async def set_background(session: Session, upload: UploadFile | None) -> str | None:
    """Меняет фон сайта. `upload is None` — очистить фон."""
    old = settings_repo.get(session)
    stored = await replace_image(upload, old.background_image if old else None)
    settings_repo.set_background(session, stored)
    session.commit()
    return stored


async def set_logo(session: Session, upload: UploadFile | None) -> str | None:
    """Меняет логотип. `upload is None` — убрать логотип."""
    old = settings_repo.get(session)
    stored = await replace_image(upload, old.logo_image if old else None)
    settings_repo.set_logo(session, stored)
    session.commit()
    return stored
