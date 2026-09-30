from __future__ import annotations

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import Column, Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from core.config import settings
from database.base import Base

logger = logging.getLogger(__name__)

_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
if settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine: Engine = create_engine(settings.database_url, **_engine_kwargs)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI-зависимость: сессия на запрос с откатом при ошибке."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def add_column(table_name: str, column: Column[Any]) -> None:
    """Добавляет одну колонку в существующую таблицу.

    Тип и значение по умолчанию берутся из модели, поэтому синтаксис
    получается одинаковым и для SQLite, и для PostgreSQL: `ALTER TABLE ...
    ADD COLUMN` поддерживают оба.
    """
    if not column.nullable and column.default is None and column.server_default is None:
        # NOT NULL без значения по умолчанию добавить нельзя: на непустой
        # таблице ALTER упадёт. Лучше остановиться сразу, чем после кучи
        # уже применённых изменений.
        raise RuntimeError(
            f"Колонка {table_name}.{column.name} обязательна, но без значения "
            "по умолчанию — её нельзя добавить к существующей таблице"
        )

    dialect = engine.dialect
    quoted_table = dialect.identifier_preparer.quote(table_name)
    type_sql = column.type.compile(dialect=dialect)
    statement = f"ALTER TABLE {quoted_table} ADD COLUMN {column.name} {type_sql}"
    if column.default is not None:
        literal = column.type.literal_processor(dialect=dialect)
        statement += f" DEFAULT {literal(column.default.arg)}"
    if not column.nullable:
        statement += " NOT NULL"

    with engine.begin() as conn:
        conn.execute(text(statement))
    logger.info("Добавлена колонка %s.%s", table_name, column.name)


def add_missing_columns() -> None:
    """Доводит схему до модели, ничего не удаляя и не перезаписывая.

    `create_all` создаёт недостающие таблицы, но в существующей таблице
    новую колонку не добавляет. У проекта нет системы миграций, а рабочая
    база содержит ручные данные, поэтому недостающие колонки дописываются
    обычным `ADD COLUMN`: строки при этом сохраняются, а значения по
    умолчанию проставляет сама база.
    """
    import core.models  # noqa: F401  регистрация метаданных всех моделей

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        present = {item["name"] for item in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in present:
                add_column(table.name, column)


def create_all() -> None:
    import core.models  # noqa: F401  регистрация метаданных всех моделей

    Base.metadata.create_all(bind=engine)
    add_missing_columns()
    logger.info("Схема БД синхронизирована: %s", settings.database_url)
