from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_TEST_DB = PROJECT_ROOT / "test_survey.db"

# Под pytest-xdist воркеры идут параллельно и бьют по общему файлу базы:
# Windows сообщает об этом блокировкой, и падает не код, а его проверка.
# Поэтому у каждого воркера своя база — имя берётся из переменной, которую
# xdist экспортирует в окружение каждого процесса. Без xdist переменной нет,
# и путь остаётся прежним.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
if _WORKER:
    _TEST_DB = PROJECT_ROOT / f"test_survey{_WORKER}.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DEBUG"] = "false"
os.environ["REDIS_URL"] = ""
os.environ["COOKIE_SECURE"] = "false"
# Лимиты частоты в тестах выключены: счётчики живут в памяти процесса и
# копились бы между тестами. Свои лимиты проверяет tests/test_ratelimit.py.
os.environ["RATE_LIMIT_CREATE_PER_HOUR"] = "0"
os.environ["RATE_LIMIT_SUBMIT_PER_MINUTE"] = "0"
os.environ["RATE_LIMIT_PAGE_PER_MINUTE"] = "0"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    """Кэш статистики не должен протекать между тестами."""
    from analytics import cache as stats_cache

    stats_cache.reset_backend()
    yield
    stats_cache.reset_backend()


@pytest.fixture(scope="session", autouse=True)
def _prepare_database() -> Iterator[None]:
    from database.session import create_all, engine

    if _TEST_DB.exists():
        _TEST_DB.unlink()
    create_all()
    yield
    engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(_TEST_DB) + suffix)
        if candidate.exists():
            candidate.unlink()


@pytest.fixture
def client() -> Iterator:
    from fastapi.testclient import TestClient

    from core.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db_session() -> Iterator:
    from database.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def clean_tables(db_session) -> Iterator:
    from core.models import Answer, Choice, Question, Response, SiteSettings, Survey, SurveyKey, User

    for model in (Answer, Choice, Question, Response, SurveyKey, SiteSettings, Survey, User):
        db_session.query(model).delete()
    db_session.commit()
    yield
    db_session.rollback()


@pytest.fixture(autouse=True)
def media_dir(tmp_path, monkeypatch) -> Iterator:
    """Загрузки в тестах не должны попадать в рабочий `media/`.

    Фикстура автоприменяемая: картинку сохраняет любой тест, который
    вызывает загрузку, и перечислять её вручную в каждом таком тесте
    забывали — из-за чего после прогонов в `media/` оставались десятки
    заглушек. Теперь перенаправление действует на все тесты сразу.

    `media_dir` — свойство Settings, поэтому подменяем его на уровне класса.
    """
    from core.config import settings

    target = tmp_path / "media"
    target.mkdir()
    monkeypatch.setattr(type(settings), "media_dir", property(lambda self: target))
    yield target


@pytest.fixture
def creator(db_session, clean_tables) -> str:
    """Зарегистрированный создатель анкет. Возвращает логин."""
    from auth.service import register_creator

    register_creator(db_session, "anna", "secret1")
    return "anna"


@pytest.fixture
def admin(db_session, clean_tables) -> str:
    """Администратор с паролем по умолчанию, как его заводит приложение."""
    from auth.service import ensure_admin_account

    ensure_admin_account(db_session)
    return "admin"


def login(client, username: str, password: str) -> None:
    """Вход в аккаунт через настоящую форму, а не мимо неё."""
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, "вход не удался"


@pytest.fixture
def as_creator(client, creator) -> Iterator:
    """Клиент, вошедший создателем анкет."""
    login(client, creator, "secret1")
    yield client


@pytest.fixture
def as_admin(client, admin) -> Iterator:
    """Клиент, вошедший администратором."""
    login(client, admin, "admin")
    yield client
