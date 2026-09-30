from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect, text

from database.session import Base, add_missing_columns as session_add_missing_columns
from database.session import engine
from core.config import settings

import core.models  # noqa: F401  регистрация всех таблиц в метаданных

logger = logging.getLogger(__name__)

TABLES = (
    "users",
    "surveys",
    "questions",
    "choices",
    "responses",
    "answers",
    "survey_keys",
    "site_settings",
)


def _is_sqlite_file(url: str) -> str | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix):]
    return raw or None


def drop_all() -> None:
    logger.warning("Удаляю все таблицы в %s", settings.database_url)
    Base.metadata.drop_all(bind=engine)


def create_all() -> None:
    Base.metadata.create_all(bind=engine)
    logger.info("Схема создана/актуальна: %s", settings.database_url)


def add_missing_columns() -> None:
    session_add_missing_columns()


def _pk_columns(inspector, table: str) -> set[str]:
    return set(inspector.get_pk_constraint(table).get("constrained_columns") or [])


def _fk_targets(inspector, table: str) -> dict[str, str]:
    out = {}
    for fk in inspector.get_foreign_keys(table):
        target = fk.get("referred_table")
        for col in fk.get("constrained_columns") or []:
            out[col] = f"-> {target}.{fk.get('referred_columns')}"
    return out


def report() -> None:
    """Состав базы: сколько строк в каждой таблице."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    print(f"База данных: {settings.database_url}")
    for table in TABLES:
        mark = "OK " if table in existing else "-- "
        count = 0
        if table in existing:
            count = engine.connect().execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
        print(f"  {mark}{table:<14} строк: {count}")


def schema() -> None:
    """Структура базы: колонки, первичные ключи и связи.

    Нужен, чтобы посмотреть схему, не открывая SQLite в сторонней программе.
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    print(f"База данных: {settings.database_url}")
    for table in TABLES:
        if table not in existing:
            print(f"\n-- {table}: таблицы нет")
            continue
        pk = _pk_columns(inspector, table)
        fk = _fk_targets(inspector, table)
        print(f"\n{table}")
        for col in inspector.get_columns(table):
            marks = []
            if col["name"] in pk:
                marks.append("PK")
            if not col.get("nullable", True):
                marks.append("NN")
            if col["name"] in fk:
                marks.append(fk[col["name"]])
            suffix = f"  [{', '.join(marks)}]" if marks else ""
            print(f"  {col['name']:<18} {col['type']}{suffix}")
        for idx in inspector.get_indexes(table):
            print(f"  индекс {idx['name']}: {', '.join(idx['column_names'])}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Создание схемы БД проекта «Опросы»")
    parser.add_argument(
        "--drop", action="store_true", help="удалить все таблицы перед созданием"
    )
    parser.add_argument("--report", action="store_true", help="показать состав и наполнение")
    parser.add_argument(
        "--schema", action="store_true", help="показать колонки, ключи и связи всех таблиц"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    path = _is_sqlite_file(settings.database_url)
    if path and args.drop and Path(path).exists():
        Path(path).unlink()
        logger.info("Файл базы удалён: %s", path)
        args.drop = False

    if args.drop:
        drop_all()

    create_all()
    add_missing_columns()

    if args.schema:
        schema()
    elif args.report:
        report()


if __name__ == "__main__":
    main()
