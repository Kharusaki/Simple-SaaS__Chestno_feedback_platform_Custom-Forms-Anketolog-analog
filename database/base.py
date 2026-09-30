from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый декларативный класс. Единственный источник правды по схеме БД."""

    def __repr__(self) -> str:  # pragma: no cover - удобство отладки
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"
