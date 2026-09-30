"""Полный сброс базы: удаляет все таблицы и создаёт их заново.

Запуск:
    python -m scripts.reset_db
    python -m scripts.reset_db --yes   # без вопроса подтверждения
"""

from __future__ import annotations

import logging
import sys

from core.config import settings
from database.init_db import create_all, drop_all, report
from database.session import engine

import core.models  # noqa: F401  регистрация таблиц в метаданных

logger = logging.getLogger(__name__)

CONFIRM_TEXT = "СБРОС"


def _confirm() -> bool:
    if not sys.stdin.isatty():
        return False
    print(f"Будет удалено содержимое: {settings.database_url}")
    answer = input(f"Введите {CONFIRM_TEXT} для подтверждения: ")
    return answer.strip() == CONFIRM_TEXT


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not settings.database_url.startswith("sqlite:///"):
        logger.error(
            "Сброс разрешён только для SQLite. Проверь DATABASE_URL: %s",
            settings.database_url,
        )
        return 1

    if "--yes" not in argv and not _confirm():
        logger.info("Сброс отменён. Для повтора: python -m scripts.reset_db --yes")
        return 1

    logger.warning("Удаляю все таблицы в %s", settings.database_url)
    drop_all()
    create_all()
    engine.dispose()
    logger.info("Схема пересоздана, данных нет.")
    print()
    report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
