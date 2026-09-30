"""HTTP-слой админ-зоны: оформление сайта, инструкция, API.

Раздел закрыт ролью `admin`, а не отдельным токеном: раньше доступ
давался строкой в `.env`, и по умолчанию она была одинаковой у всех.
Теперь на входе логин и пароль, а неадминистратор получает 404 — чтобы
не подтверждать постороннему, что такая страница вообще существует.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from admin import service
from auth.access import require_admin
from database.session import get_db
from core.deps import render
from core.images import UploadError, drop_empty
from core.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"], prefix="/admin", dependencies=[Depends(require_admin)])


def _appearance_context(session: Session, error: str | None = None) -> dict[str, object]:
    row = service.get_settings_row(session)
    return {
        "title": "Оформление сайта",
        "background": service.to_view(row.background_image if row else None),
        "logo": service.to_view(row.logo_image if row else None),
        "error": error,
    }


@router.get("")
def admin_home(request: Request) -> Response:
    """Админ-зона: что можно настроить и куда идти дальше."""
    return render(
        request,
        "admin.html",
        {
            "title": "Администратор",
            "sections": [
                {
                    "path": "/admin/appearance",
                    "title": "Оформление сайта",
                    "text": "Фон, логотип и обложки анкет.",
                },
                {
                    "path": "/admin/instructions",
                    "title": "Инструкция по управлению",
                    "text": "Как вести сайт: аккаунты, темы, анкеты, ключи.",
                },
            ],
        },
    )


@router.get("/instructions")
def instructions(request: Request) -> Response:
    return render(request, "admin_instructions.html", {"title": "Инструкция"})


@router.get("/openapi.json")
def openapi_schema(request: Request) -> Response:
    """Сама схема API. Страницы-обёртки для неё нет: ссылка на этот адрес
    описана в README, а интерфейс администратора не должен заниматься
    второстепенными разделами."""
    import json

    from core.main import app

    return Response(
        content=json.dumps(app.openapi(), ensure_ascii=False),
        media_type="application/json",
    )


@router.get("/appearance")
def appearance_page(
    request: Request, session: Session = Depends(get_db)
) -> Response:
    return render(request, "admin_appearance.html", _appearance_context(session))


@router.post("/appearance")
async def save_appearance(
    request: Request,
    background: UploadFile | None = File(None),
    logo: UploadFile | None = File(None),
    clear_background: str = Form(""),
    clear_logo: str = Form(""),
    session: Session = Depends(get_db),
) -> Response:
    error: str | None = None
    try:
        new_background = drop_empty(background)
        new_logo = drop_empty(logo)
        if clear_background.strip():
            await service.set_background(session, None)
        elif new_background is not None:
            await service.set_background(session, new_background)
        if clear_logo.strip():
            await service.set_logo(session, None)
        elif new_logo is not None:
            await service.set_logo(session, new_logo)
    except UploadError as exc:
        error = str(exc)
        logger.info("Файл оформления отклонён: %s", exc)

    return render(
        request,
        "admin_appearance.html",
        _appearance_context(session, error),
        status_code=400 if error else 200,
    )


@router.post("/appearance/back")
def back_to_admin() -> RedirectResponse:
    return RedirectResponse(url="/admin", status_code=303)
