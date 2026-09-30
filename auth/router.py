"""HTTP-слой аккаунтов: регистрация, вход, выход, смена пароля.

Плюс вход по ключу анкеты, который остался с прошлых фаз: он открывает
статистику или настройки конкретной анкеты и учётной записи не требует.

Правила проверки входа и регистрации живут в `auth/service.py`, здесь
только разбор формы, выбор шаблона и редиректы.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from auth import keys as keys_auth
from auth import passwords
from auth import service as auth_service
from auth.access import current_user, current_user_dep
from auth.session import clear_session_cookie, set_session_cookie
from core.config import settings
from database.repositories import answers as answers_repo
from database.repositories import surveys as surveys_repo
from database.session import get_db
from core.deps import render
from core.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

DASHBOARD = "/dashboard"
PASSWORD_PAGE = "/account/password"


def _safe_next(raw: str | None) -> str:
    """Куда вернуть после входа. Только свой сайт: чужой адрес в `next` —
    это открытый редирект.

    Отсекаем не только `http://evil`, но и `//evil` и `\\/evil`: браузер
    считает такие адреса абсолютными и уводит человека с нашего домена.
    """
    if not raw:
        return DASHBOARD
    candidate = raw.strip()
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return DASHBOARD
    return candidate


def _password_page(
    request: Request,
    user: User | None,
    errors: dict[str, str],
    status_code: int,
) -> Response:
    if user is None:
        return RedirectResponse(url=f"/login?next={PASSWORD_PAGE}", status_code=303)
    return render(
        request,
        "password_change.html",
        {
            "title": "Смена пароля",
            "errors": errors,
            "warning": user.must_change_password,
        },
        status_code=status_code,
    )


@router.get("/register")
def register_form(
    request: Request,
    session: Session = Depends(get_db),
    next_url: str | None = Query(None, alias="next"),
) -> Response:
    if current_user(request, session) is not None:
        return RedirectResponse(url=DASHBOARD, status_code=303)
    if not settings.allow_registration:
        return render(
            request,
            "error.html",
            {
                "title": "Регистрация закрыта",
                "code": 403,
                "message": "Регистрация на этом сервере выключена. "
                "Попросите администратора завести аккаунт.",
            },
            status_code=403,
        )
    return render(
        request,
        "register.html",
        {
            "title": "Регистрация",
            "errors": {},
            "values": {},
            "next": _safe_next(next_url),
        },
    )


@router.post("/register")
async def register(
    request: Request, session: Session = Depends(get_db)
) -> Response:
    if not settings.allow_registration:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    repeat = str(form.get("password_repeat", ""))
    next_url = _safe_next(str(form.get("next", "")) or None)

    errors = auth_service.validate_registration(
        session, username=username, password=password, password_repeat=repeat
    )
    if errors:
        return render(
            request,
            "register.html",
            {
                "title": "Регистрация",
                "errors": errors,
                "values": {"username": username},
                "next": next_url,
            },
            status_code=400,
        )

    user = auth_service.register_creator(session, username, password)
    logger.info(
        "Зарегистрирован создатель анкет username=%s id=%s", user.username, user.id
    )

    response = RedirectResponse(url=next_url, status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.get("/login")
def login_form(
    request: Request,
    session: Session = Depends(get_db),
    next_url: str | None = Query(None, alias="next"),
) -> Response:
    if current_user(request, session) is not None:
        return RedirectResponse(url=_safe_next(next_url), status_code=303)
    return render(
        request,
        "login.html",
        {
            "title": "Вход",
            "next": _safe_next(next_url),
            "values": {},
            "errors": {},
        },
    )


@router.post("/login")
async def login(request: Request, session: Session = Depends(get_db)) -> Response:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_url = _safe_next(str(form.get("next", "")) or None)

    user = auth_service.authenticate(session, username, password)
    if user is None:
        logger.info("Неудачный вход username=%s", username)
        return render(
            request,
            "login.html",
            {
                "title": "Вход",
                "next": next_url,
                "values": {"username": username},
                "errors": {"form": "Неверный логин или пароль."},
            },
            status_code=401,
        )

    logger.info("Вход username=%s role=%s", user.username, user.role)
    response = RedirectResponse(url=next_url, status_code=303)
    set_session_cookie(response, user.id)
    return response


@router.post("/logout")
def logout() -> Response:
    """Выход из аккаунта на этом компьютере. Анкеты остаются на месте."""
    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    clear_password_notice_cookie(response)
    return response


@router.get("/account/password")
def password_form(
    request: Request, user: User | None = Depends(current_user_dep)
) -> Response:
    return _password_page(request, user, errors={}, status_code=200)


@router.post("/account/password")
async def change_password(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return _password_page(request, None, errors={}, status_code=303)

    form = await request.form()
    current = str(form.get("current_password", ""))
    password = str(form.get("password", ""))
    repeat = str(form.get("password_repeat", ""))

    errors = auth_service.validate_password_change(
        user, current_password=current, password=password, password_repeat=repeat
    )
    if errors:
        return _password_page(request, user, errors=errors, status_code=400)

    auth_service.apply_new_password(session, user, password)
    logger.info("Пароль изменён username=%s", user.username)
    response = RedirectResponse(url=DASHBOARD, status_code=303)
    # Смена пароля обесценивает все ранее выданные cookie, включая текущую.
    # Перевыдаём её в этом же ответе: иначе сменивший пароль вылетел бы на
    # страницу входа, а вместе с ним — из всех других устройств.
    set_session_cookie(response, user.id)
    clear_password_notice_cookie(response)
    return response


@router.post("/account/password/dismiss")
def dismiss_password_warning(
    request: Request,
    dont_remind: str = Form(""),
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    """Закрывает окно «пароль стандартный».

    Два разных намерения, и их важно не смешивать:
    - без галочки пользователь просто продолжает работу, предупреждение
      остаётся в базе, а cookie прячет его в этом браузере на несколько
      часов;
    - с галочкой «не напоминать больше» флаг снимается навсегда.

    Возвращаем на ту страницу, от которой пришёл запрос, но только если это
    наш же адрес: `Referer` приходит от клиента и ему верить нельзя.
    """
    response = RedirectResponse(
        url=_safe_next(request.headers.get("referer")), status_code=303
    )
    if user is None or not user.must_change_password:
        return response

    if dont_remind:
        auth_service.dismiss_password_warning(session, user)
        logger.info("Пользователь отказался от напоминания о пароле username=%s", user.username)
        clear_password_notice_cookie(response)
        return response

    logger.info("Пользователь отложил смену пароля username=%s", user.username)
    response.set_cookie(
        key=settings.password_notice_cookie_name,
        value="1",
        max_age=settings.password_notice_max_age,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


def clear_password_notice_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.password_notice_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.get("/account")
def account_page(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    """Личный кабинет: информация об аккаунте и управление данными."""
    if user is None:
        return RedirectResponse(url="/login?next=/account", status_code=303)

    surveys = surveys_repo.list_by_owner(session, user.id)
    total_responses = sum(
        answers_repo.count_submitted(session, survey.id) for survey in surveys
    )
    return render(
        request,
        "account.html",
        {
            "title": "Личный кабинет",
            "user": user,
            "surveys_count": len(surveys),
            "responses_count": total_responses,
        },
    )


@router.get("/account/delete")
def delete_account_form(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return RedirectResponse(url="/login?next=/account/delete", status_code=303)
    surveys = surveys_repo.list_by_owner(session, user.id)
    total_responses = sum(
        answers_repo.count_submitted(session, survey.id) for survey in surveys
    )
    return render(
        request,
        "account_delete.html",
        {
            "title": "Удаление аккаунта",
            "user": user,
            "surveys_count": len(surveys),
            "responses_count": total_responses,
            "errors": {},
        },
    )


@router.post("/account/delete")
async def delete_account(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return RedirectResponse(url="/login?next=/account/delete", status_code=303)

    form = await request.form()
    password = str(form.get("password", ""))

    if not passwords.verify_password(password, user.password_hash):
        surveys = surveys_repo.list_by_owner(session, user.id)
        total_responses = sum(
            answers_repo.count_submitted(session, survey.id) for survey in surveys
        )
        return render(
            request,
            "account_delete.html",
            {
                "title": "Удаление аккаунта",
                "user": user,
                "surveys_count": len(surveys),
                "responses_count": total_responses,
                "errors": {"password": "Неверный пароль."},
            },
            status_code=400,
        )

    # Логин читаем до удаления: после `session.delete` и `commit` обращение
    # к полям того же объекта заставит ORM перезагрузить стёртую строку.
    username = user.username
    auth_service.delete_account(session, user)
    logger.info("Аккаунт удалён пользователем username=%s", username)
    response = RedirectResponse(url="/", status_code=303)
    clear_session_cookie(response)
    return response


@router.get("/account/revoke-responses")
def revoke_responses_form(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return RedirectResponse(url="/login?next=/account/revoke-responses", status_code=303)
    surveys = surveys_repo.list_by_owner(session, user.id)
    total_responses = sum(
        answers_repo.count_submitted(session, survey.id) for survey in surveys
    )
    return render(
        request,
        "account_revoke.html",
        {
            "title": "Отзыв ответов",
            "user": user,
            "surveys_count": len(surveys),
            "responses_count": total_responses,
        },
    )


@router.post("/account/revoke-responses")
def revoke_responses(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return RedirectResponse(url="/login?next=/account/revoke-responses", status_code=303)

    count = auth_service.revoke_all_responses(session, user)
    logger.info("Отозваны ответы username=%s count=%d", user.username, count)
    return RedirectResponse(url="/account?revoked=1", status_code=303)


@router.get("/account/delete-surveys")
def delete_surveys_form(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return RedirectResponse(url="/login?next=/account/delete-surveys", status_code=303)
    surveys = surveys_repo.list_by_owner(session, user.id)
    total_responses = sum(
        answers_repo.count_submitted(session, survey.id) for survey in surveys
    )
    return render(
        request,
        "account_delete_surveys.html",
        {
            "title": "Удаление всех анкет",
            "user": user,
            "surveys_count": len(surveys),
            "responses_count": total_responses,
        },
    )


@router.post("/account/delete-surveys")
def delete_surveys(
    request: Request,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    if user is None:
        return RedirectResponse(url="/login?next=/account/delete-surveys", status_code=303)

    count = auth_service.delete_all_surveys(session, user)
    logger.info("Удалены все анкеты username=%s count=%d", user.username, count)
    return RedirectResponse(url="/account?deleted=1", status_code=303)


@router.get("/k/{token}")
def enter_by_key(
    request: Request,
    token: str,
    user: User | None = Depends(current_user_dep),
    session: Session = Depends(get_db),
) -> Response:
    """Вход по ключу: ключ с правом правки ведёт в настройки, обычный — в статистику."""
    key = keys_auth.find_key_anywhere(session, token)
    if key is None:
        logger.info("Вход по несуществующему ключу")
        return render(
            request,
            "error.html",
            {
                "title": "Ключ не найден",
                "code": 404,
                "message": "Такого ключа нет. Возможно, его отозвали или в ссылке опечатка.",
            },
            status_code=404,
        )

    survey = key.survey
    if not keys_auth.is_owner(survey, user) and not key.can_edit:
        logger.info("Вход по ключу только для чтения id=%s", key.id)
        return RedirectResponse(
            url=f"/s/{survey.slug}/stats?key={key.token}", status_code=303
        )

    logger.info("Вход по ключу id=%s survey=%s", key.id, survey.id)
    return RedirectResponse(
        url=f"/s/{survey.slug}/settings?key={key.token}", status_code=303
    )
