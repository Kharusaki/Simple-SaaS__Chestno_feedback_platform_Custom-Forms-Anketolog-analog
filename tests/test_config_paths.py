"""Путь к базе по умолчанию привязан к корню проекта, а не к рабочей папке.

Раньше в `core/config.py` стояло `sqlite:///./survey.db`, и SQLite разрешал
этот путь относительно папки запуска. Из соседнего каталога появлялась
вторая пустая база — снаружи это выглядело как «все анкеты пропали», хотя
настоящая база лежала нетронутой. Здесь проверяется, что путь абсолютный и
что явный `DATABASE_URL` по-прежнему главнее.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from core.config import (
    BASE_DIR,
    DB_PATH,
    DEFAULT_SECRET_KEY,
    ENV_PATH,
    Settings,
    default_database_url,
)


def test_default_points_into_project_root() -> None:
    assert default_database_url() == f"sqlite:///{DB_PATH}"
    assert DB_PATH.parent == BASE_DIR, "база обязана лежать в корне проекта"


def test_default_url_is_absolute_not_relative() -> None:
    """Относительный путь и был источником второй пустой базы."""
    database = make_url(default_database_url()).database

    assert database, "SQLAlchemy не разобрал путь к базе"
    assert Path(database).is_absolute(), f"путь относительный: {database!r}"


def test_default_url_is_independent_of_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert default_database_url() == f"sqlite:///{DB_PATH}"
    assert Path(DB_PATH).is_absolute()


def test_explicit_database_url_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Значение из окружения главнее: иначе внешнюю базу было бы не задать."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:////data/survey.db")

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite:////data/survey.db"


def test_default_is_used_when_variable_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == default_database_url()


def test_env_template_warns_about_relative_paths() -> None:
    """Шаблон `.env` не должен снова подсказывать относительный путь."""
    from core.config import ENV_TEMPLATE

    text = ENV_TEMPLATE.format(
        date="2026-01-01", secret="x", db_path=DB_PATH
    )

    active = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith("DATABASE_URL=")
    ]
    assert not active, f"шаблон задаёт базу молча: {active}"
    assert "ПОЛНЫЙ путь" in text, "нет предупреждения про относительный путь"


def test_existing_env_does_not_pin_a_relative_database() -> None:
    """Если `.env` уже есть, он не должен активно задавать путь от cwd.

    Старый шаблон подсказывал `sqlite:///./survey.db`. Пока строка
    закомментирована, она безвредна, но активная вернула бы баг обратно.
    """
    if not ENV_PATH.exists():
        pytest.skip("`.env` ещё не создан")

    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("DATABASE_URL="):
            continue
        value = stripped.split("=", 1)[1]
        if value.startswith("sqlite:///"):
            path = value[len("sqlite:///"):]
            assert Path(path).is_absolute(), f"`.env` задаёт путь от cwd: {value}"


def test_env_example_does_not_hand_out_a_relative_database() -> None:
    """Скопированный пример не должен возвращать баг обратно.

    `.env.example` раньше содержал живую строку
    `DATABASE_URL=sqlite:///./survey.db`. Скопировавший его человек снова
    получал базу, зависящую от папки запуска.
    """
    example = Path(__file__).resolve().parents[1] / ".env.example"

    active = [
        line.strip()
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("DATABASE_URL=")
    ]

    assert not active, f"пример задаёт базу молча: {active}"


class TestKnownSecretRefusedInProduction:
    """Публичный ключ из учебных материалов подписывает все cookie одинаково.

    С ним можно подделать чужую сессию, поэтому боевой запуск с ним должен
    падать, а не тихо подниматься.
    """

    def test_production_with_default_secret_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", DEFAULT_SECRET_KEY)

        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(_env_file=None)

    def test_production_with_own_secret_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "свой-ключ-подлиннее")

        assert Settings(_env_file=None).secret_key == "свой-ключ-подлиннее"

    def test_debug_with_default_secret_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отладка и `start.bat` должны работать из коробки, без настроек."""
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("SECRET_KEY", DEFAULT_SECRET_KEY)

        settings = Settings(_env_file=None)

        assert settings.is_production is False
        assert settings.secret_key == DEFAULT_SECRET_KEY

    def test_tests_are_not_blocked_by_the_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """conftest поднимает тесты с `DEBUG=false`, но со своим ключом."""
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "test-secret-key")

        assert Settings(_env_file=None).is_production is True
