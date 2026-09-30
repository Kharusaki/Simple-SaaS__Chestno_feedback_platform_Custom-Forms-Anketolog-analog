"""HTTP-слой аналитики: страница статистики и выгрузка CSV."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from analytics import cache as stats_cache
from analytics import charts as charts_mod
from analytics import csv_export
from analytics.service import collect_stats
from auth import keys as keys_auth
from auth.access import current_user_dep
from database.session import get_db
from core.deps import render
from core.models import Survey, User
from surveys import crud

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])

RECENT_LIMIT = 10
FILENAME_UNSAFE = re.compile(r"[^\w\s-]", re.UNICODE)
FILENAME_SPACES = re.compile(r"\s+")


def _not_found(slug: str) -> HTTPException:
    logger.info("Запрошена статистика несуществующего опроса slug=%s", slug)
    return HTTPException(status_code=404)


def _forbidden(slug: str) -> HTTPException:
    logger.info("Отказ в доступе к статистике slug=%s: не владелец", slug)
    return HTTPException(status_code=403)


def _load_viewable(
    session: Session, slug: str, user: User | None, key: str | None
) -> Survey:
    """Опрос для просмотра статистики.

    404 если такого slug нет, 403 если он есть, но доступ не выдан.
    Владелец определяется по сессии, остальные — по ключу из `?key=`.
    """
    survey = crud.get_by_slug(session, slug)
    if survey is None:
        raise _not_found(slug)
    if not keys_auth.check_access(session, survey, user, key):
        raise _forbidden(slug)
    return survey


def _csv_filename(title: str) -> str:
    """Имя файла, которое Excel и браузер принимают без искажений."""
    cleaned = FILENAME_UNSAFE.sub("", title or "анкета").strip()
    cleaned = FILENAME_SPACES.sub("_", cleaned)[:60].strip("_")
    return f"{cleaned or 'анкета'}_otvety.csv"


@router.get("/s/{slug}/stats")
def survey_stats(
    request: Request,
    slug: str,
    key: str | None = Query(default=None, alias=keys_auth.KEY_QUERY_PARAM),
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    survey = _load_viewable(session, slug, user, key)

    survey_stats_data = stats_cache.load_stats(survey.slug)
    if survey_stats_data is None:
        survey_stats_data = collect_stats(session, survey, recent_limit=RECENT_LIMIT)
        stats_cache.store_stats(survey.slug, survey_stats_data)

    is_owner = user is not None and survey.owner_id == user.id
    share_key = crud.get_primary_key(session, survey) if is_owner else None
    return render(
        request,
        "survey_stats.html",
        {
            "title": f"Статистика: {survey.title}",
            "survey": survey,
            "stats": survey_stats_data,
            "charts": charts_mod.build_charts(survey_stats_data),
            "chart_cdn": charts_mod.CHART_CDN,
            "recent_limit": RECENT_LIMIT,
            "stats_key": share_key.token if share_key else None,
        },
    )


@router.get("/s/{slug}/stats.csv", response_class=PlainTextResponse)
def survey_stats_csv(
    slug: str,
    key: str | None = Query(default=None, alias=keys_auth.KEY_QUERY_PARAM),
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    survey = _load_viewable(session, slug, user, key)

    body = csv_export.build_csv(session, survey)
    filename = _csv_filename(survey.title)
    logger.info("Выгрузка CSV slug=%s", slug)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"otvety.csv\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )
