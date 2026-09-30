"""Кэш агрегатов статистики: Redis с обязательным фолбэком в память.

Redis в проекте необязателен. Если `REDIS_URL` пуст, сервер не отвечает или
отвечает ошибкой, приложение продолжает работать на in-memory словаре: любая
ошибка Redis приводит к отказу на локальный кэш, а не к падению запроса.

Кэш хранит JSON, поэтому агрегаты сначала сводятся в словарь
(`stats_to_payload`), а на попадании собираются обратно (`stats_from_payload`)
уже без запросов к базе.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from analytics.service import SurveyStats, stats_from_payload, stats_to_payload
from core.config import settings

logger = logging.getLogger(__name__)

STATS_PREFIX = "stats"

_lock = threading.Lock()
_backend: "Backend | None" = None


class Backend:
    """Минимальный контракт хранилища: get/set/delete/increment/ping."""

    name = "none"

    def get(self, key: str) -> str | None:
        raise NotImplementedError

    def set(self, key: str, value: str, ttl: int) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def increment(self, key: str, ttl: int) -> int:
        raise NotImplementedError

    def ping(self) -> bool:
        raise NotImplementedError


class MemoryBackend(Backend):
    """Фолбэк без внешних зависимостей. Живёт в процессе и сбрасывается
    при перезапуске — этого достаточно, чтобы приложение не падало."""

    name = "memory"

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._values.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._values[key] = (time.monotonic() + ttl, value)

    def delete(self, key: str) -> None:
        self._values.pop(key, None)

    def increment(self, key: str, ttl: int) -> int:
        raw = self.get(key)
        counter = (int(raw) if raw else 0) + 1
        self.set(key, str(counter), ttl)
        return counter

    def ping(self) -> bool:
        return True

    def clear(self) -> None:
        self._values.clear()


class RedisBackend(Backend):
    name = "redis"

    def __init__(self, client) -> None:
        self._client = client

    def get(self, key: str) -> str | None:
        value = self._client.get(key)
        return value.decode("utf-8") if isinstance(value, bytes) else value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._client.set(key, value, ex=ttl)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def increment(self, key: str, ttl: int) -> int:
        counter = int(self._client.incr(key))
        if counter == 1:
            self._client.expire(key, ttl)
        return counter

    def ping(self) -> bool:
        return bool(self._client.ping())


def _build_redis_backend() -> RedisBackend | None:
    """Пробует поднять Redis. Любая ошибка — не повод ломать приложение."""
    try:
        import redis
    except ImportError:
        logger.warning("Пакет redis не установлен, используется кэш в памяти")
        return None

    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
    except Exception as exc:  # noqa: BLE001 — Redis здесь необязателен
        logger.warning("Redis недоступен (%s), используется кэш в памяти", exc)
        return None

    logger.info("Кэш статистики работает через Redis")
    return RedisBackend(client)


def get_backend() -> Backend:
    global _backend
    with _lock:
        if _backend is None:
            _backend = (
                _build_redis_backend() if settings.redis_enabled else MemoryBackend()
            )
        return _backend


def reset_backend() -> None:
    """Сбрасывает выбранный бэкенд. Нужен тестам и переключению конфигурации."""
    global _backend
    with _lock:
        _backend = None


def stats_key(slug: str) -> str:
    return f"{STATS_PREFIX}:{slug}"


def load_stats(slug: str) -> SurveyStats | None:
    backend = get_backend()
    try:
        raw = backend.get(stats_key(slug))
    except Exception as exc:  # noqa: BLE001 — кэш не должен ронять страницу
        logger.warning("Кэш недоступен на чтении (%s), считаем статистику заново", exc)
        return None
    if raw is None:
        return None
    try:
        return stats_from_payload(json.loads(raw))
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("Повреждённая запись кэша для %s (%s), игнорируем", slug, exc)
        try:
            backend.delete(stats_key(slug))
        except Exception as delete_exc:  # noqa: BLE001 — мусор в кэше не важен
            logger.warning("Не удалось удалить битую запись кэша %s: %s", slug, delete_exc)
        return None


def store_stats(slug: str, stats: SurveyStats) -> None:
    backend = get_backend()
    try:
        backend.set(
            stats_key(slug),
            json.dumps(stats_to_payload(stats), ensure_ascii=False),
            settings.redis_ttl_seconds,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Статистику %s не удалось положить в кэш: %s", slug, exc)
    except Exception as exc:  # noqa: BLE001 — кэш не должен ронять страницу
        logger.warning("Кэш недоступен на записи (%s)", exc)


def invalidate(slug: str) -> None:
    """Сбрасывает агрегаты после появления нового ответа или правки анкеты."""
    backend = get_backend()
    try:
        backend.delete(stats_key(slug))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось сбросить кэш для %s: %s", slug, exc)


def status() -> dict[str, Any]:
    """Состояние кэша для `/health/redis`."""
    backend = get_backend()
    try:
        available = backend.ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ping кэша не прошёл: %s", exc)
        available = False
    return {
        "configured": settings.redis_enabled,
        "backend": backend.name,
        "available": available,
        "ttl_seconds": settings.redis_ttl_seconds,
    }
