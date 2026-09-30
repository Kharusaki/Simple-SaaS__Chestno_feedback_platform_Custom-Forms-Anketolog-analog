from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from admin.router import router as admin_router
from analytics import cache as stats_cache
from analytics import ratelimit
from analytics.router import router as analytics_router
from auth.router import router as auth_router
from auth.service import ensure_admin_account
from .config import configure_logging, settings
from database.session import SessionLocal, create_all
from .deps import STATIC_DIR, templates
from .images import UploadError, media_response, resolve_media_path
from surveys.router import router as surveys_router
from .utils import client_fingerprint, client_ip

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    create_all()
    with SessionLocal() as session:
        ensure_admin_account(session)
    logger.info("%s запущен в режиме %s", settings.app_name, "debug" if settings.debug else "prod")
    yield
    logger.info("%s остановлен", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="Мини-SaaS для создания опросов, сбора ответов и аналитики.",
    version="1.0.0",
    lifespan=lifespan,
    # Автодокументация закрыта: схема отдаётся только администратору
    # на /admin/openapi.json.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_router)
app.include_router(surveys_router)
app.include_router(analytics_router)
app.include_router(admin_router)

# Пути, которые не ограничиваем: служебные.
RATE_LIMIT_EXEMPT = frozenset({"/health", "/health/redis", "/"})

# Префиксы без лимита: статика и картинки оформления. Их тянет браузер
# при отрисовке страницы, и одна страница с пятью картинками съедала бы
# пять из лимита на страницу.
RATE_LIMIT_EXEMPT_PREFIXES = ("/static", "/media")


def _rate_limit_for(method: str, path: str) -> tuple[str, int, int] | None:
    """Имя счётчика, лимит и окно в секундах. None — путь не ограничен.

    Считается по фактическому пути запроса, а не по шаблону маршрута:
    middleware работает до того, как FastAPI сопоставил путь с роутом.
    """
    if path in RATE_LIMIT_EXEMPT or path.startswith(RATE_LIMIT_EXEMPT_PREFIXES):
        return None
    if method == "POST" and path == "/surveys/new":
        return ("create", settings.rate_limit_create_per_hour, 3600)
    # Корзина `submit` — только отправка ответов, то есть ровно `/s/{slug}`.
    # Всё остальное под `/s/` (правка, ключи, пауза, удаление) — действия
    # владельца, их нельзя смешивать с лимитом респондентов.
    if method == "POST" and path.startswith("/s/") and path.count("/") == 2:
        return ("submit", settings.rate_limit_submit_per_minute, 60)
    return ("page", settings.rate_limit_page_per_minute, 60)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    rule = _rate_limit_for(request.method, request.url.path)
    if rule is None:
        return await call_next(request)

    name, limit, period = rule
    identity = client_fingerprint(client_ip(request))
    allowed, retry_after = ratelimit.allow(name, identity, limit, period)
    if not allowed:
        logger.info("Лимит %s исчерпан для клиента %s", name, identity[:8])
        return await _error_page(request, 429, retry_after=retry_after)
    return await call_next(request)


@app.get("/health", tags=["service"])
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "app": settings.app_name, "version": "1.0.0"})


@app.get("/health/redis", tags=["service"])
async def health_redis() -> JSONResponse:
    return JSONResponse(stats_cache.status())


@app.get("/media/{filename}", tags=["service"])
async def media(filename: str) -> FileResponse:
    """Картинка оформления. Публичный маршрут: фон, логотип и обложку
    видят и респонденты, поэтому входа здесь не требуется. Имя файла
    генерировалось сервером, здесь оно только проверяется на безопасность."""
    try:
        path = resolve_media_path(filename)
    except UploadError as exc:
        logger.info("Запрошен недоступный файл оформления: %s", exc)
        raise HTTPException(status_code=404) from exc
    return media_response(path)


@app.get("/", tags=["pages"])
async def index(request: Request) -> Response:
    return templates.TemplateResponse(request, "index.html", {"title": settings.app_name})


ERROR_PAGES = {
    403: (
        "Доступ закрыт",
        "Статистика анкеты доступна только её автору. Войдите тем же браузером, "
        "которым создавали анкету, или откройте страницу по ссылке с ключом.",
    ),
    404: (
        "Страница не найдена",
        "Такой страницы нет. Возможно, ссылка устарела или в ней опечатка.",
    ),
    429: (
        "Слишком много запросов",
        "Вы отправили слишком много запросов за короткое время. Подождите немного "
        "и попробуйте снова — обычно это несколько минут.",
    ),
}


async def _error_page(
    request: Request, status_code: int, retry_after: int = 0
) -> Response:
    title, message = ERROR_PAGES[status_code]
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": title, "code": status_code, "message": message},
        status_code=status_code,
        headers=headers,
    )


@app.exception_handler(404)
async def not_found(request: Request, exc: Exception) -> Response:
    return await _error_page(request, 404)


@app.exception_handler(403)
async def forbidden(request: Request, exc: Exception) -> Response:
    return await _error_page(request, 403)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> Response:
    """Формы проверяются сами и показывают ошибки у полей. Сюда попадает
    только мусор в самом запросе — показываем страницу, а не JSON."""
    logger.info("Некорректный запрос %s: %s", request.url.path, exc.errors()[:3])
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "title": "Некорректный запрос",
            "code": 422,
            "message": "Не удалось разобрать запрос. Обновите страницу и попробуйте снова.",
        },
        status_code=422,
    )
