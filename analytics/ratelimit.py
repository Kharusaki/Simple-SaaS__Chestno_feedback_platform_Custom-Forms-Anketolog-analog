"""Ограничение частоты запросов. Redis с тем же фолбэком в память.

Счётчик сделан по окну: у каждого ключа есть TTL, а счётчик хранится как
целое число под ключом с суффиксом окна. Это ровно то, что умеет `INCR` с
`EXPIRE` в Redis и обычный словарь в памяти, — никакой сортировки и окон
наполовину-в-памяти.
"""

from __future__ import annotations

import logging
import time

from analytics import cache

logger = logging.getLogger(__name__)

RATE_PREFIX = "rate"


def _bucket_key(name: str, identity: str, window: int) -> str:
    return f"{RATE_PREFIX}:{name}:{identity}:{window}"


def _current_window(period: int) -> int:
    return int(time.time()) // period


def allow(name: str, identity: str, limit: int, period: int) -> tuple[bool, int]:
    """Проверяет лимит и увеличивает счётчик окна.

    Возвращает `(можно, сколько секунд ждать)`. Ошибка кэша не блокирует
    запрос: при недоступном хранилище лимит просто не применяется.
    """
    if limit <= 0:
        return True, 0

    window = _current_window(period)
    key = _bucket_key(name, identity, window)
    backend = cache.get_backend()
    ttl = max(1, period)

    try:
        counter = backend.increment(key, ttl)
    except Exception as exc:  # noqa: BLE001 — лимит не должен ронять запрос
        logger.warning("Лимит %s не проверен (%s), пропускаем запрос", name, exc)
        return True, 0

    if counter > limit:
        return False, ttl
    return True, 0
