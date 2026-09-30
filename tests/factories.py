"""Фабрики данных для тестов: собирают опросы и вопросы через боевой код."""

from __future__ import annotations

import asyncio
import itertools
from typing import Awaitable, TypeVar

from sqlalchemy import select

from auth.service import register_creator
from core.models import Choice, Question, Survey, SurveyKey, User
from surveys import crud
from surveys.builder import BuildResult, BuiltQuestion

T = TypeVar("T")

_owner_counter = itertools.count(1)

ALL_QUESTIONS = [
    BuiltQuestion(text="Как вам сервис?", type="single", is_required=True, position=0,
                  choices=["Отлично", "Хорошо", "Плохо"]),
    BuiltQuestion(text="Что использовали?", type="multiple", is_required=False, position=1,
                  choices=["API", "Веб-интерфейс"]),
    BuiltQuestion(text="Будете рекомендовать?", type="yes_no", is_required=True, position=2),
    BuiltQuestion(text="Оценка от 1 до 10", type="scale", is_required=True, position=3,
                  scale_min=1, scale_max=10),
    BuiltQuestion(text="Комментарий", type="text", is_required=False, position=4),
]


def make_payload(questions=None, title="Анкета после покупки") -> BuildResult:
    return BuildResult(
        title=title,
        description="Помогите нам стать лучше",
        questions=list(questions if questions is not None else ALL_QUESTIONS),
    )


def run_async(coro: Awaitable[T]) -> T:
    """Дожидается корутины CRUD из синхронного теста.

    `create_survey` стал `async` из-за сохранения картинок, а тесты
    синхронные. Отдельный event loop на вызов нужен, потому что у pytest
    свой цикл событий уже занят.
    """
    return asyncio.run(coro)  # type: ignore[arg-type]


def make_survey(session, owner: User, questions=None, title: str = "Анкета после покупки") -> tuple[Survey, SurveyKey]:
    return run_async(crud.create_survey(session, owner, make_payload(questions, title=title)))


def survey_by_slug(session, slug: str) -> Survey | None:
    return session.scalar(select(Survey).where(Survey.slug == slug))


def questions_of(session, survey: Survey) -> list[Question]:
    return list(
        session.scalars(
            select(Question).where(Question.survey_id == survey.id).order_by(Question.position)
        )
    )


def choices_of(session, question: Question) -> list[str]:
    return list(
        session.scalars(
            select(Choice.text)
            .where(Choice.question_id == question.id)
            .order_by(Choice.position)
        )
    )


def primary_key_of(session, survey: Survey) -> SurveyKey | None:
    from surveys.crud import PRIMARY_KEY_LABEL

    return session.scalar(
        select(SurveyKey).where(
            SurveyKey.survey_id == survey.id, SurveyKey.label == PRIMARY_KEY_LABEL
        )
    )


def make_owner(session, username: str = "anna") -> User:
    """Создатель анкет. У каждого вызова своя учётка, чтобы доступ одного
    теста не выглядел доступом другого. Повторный вызов с тем же логином
    возвращает уже существующего — тесты часто заводят двух разных
    владельцев, а не двух одинаковых."""
    existing = owner_by_username(session, username)
    if existing is not None:
        return existing
    user = register_creator(session, username, "secret1")
    session.commit()
    return user


def unique_owner(session, prefix: str = "owner") -> User:
    """Создатель со счётчиком в логине. Нужен там, где в одном тесте
    заводятся несколько разных учёток."""
    return make_owner(session, f"{prefix}{next(_owner_counter)}")


def owner_by_username(session, username: str = "anna") -> User | None:
    return session.scalar(select(User).where(User.username == username))


def login_as(client, session, username: str = "anna") -> User:
    """Заводит создателя анкет и входит под ним через настоящую форму.

    Заменил прежнюю пару «зайти на `/dashboard` и получить анонимного
    владельца»: анонимных владельцев больше нет, а вход проверяется так же,
    как у живого человека.
    """
    user = make_owner(session, username)
    response = client.post(
        "/login",
        data={"username": username, "password": "secret1"},
        follow_redirects=False,
    )
    assert response.status_code == 303, "вход под создателем не сработал"
    return user
