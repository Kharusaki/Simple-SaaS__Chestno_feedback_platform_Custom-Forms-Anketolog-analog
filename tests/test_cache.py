"""Кэш статистики и rate limit: Redis опционален, фолбэк обязателен.

Главное здесь — не «работает ли Redis», а «не падает ли приложение без него»,
поэтому проверяются оба пути: подставной Redis-бэкенд и настоящий фолбэк.
"""

from __future__ import annotations

import pytest

from analytics import cache as stats_cache
from analytics import ratelimit
from analytics.service import collect_stats, stats_from_payload, stats_to_payload
from surveys import taking
from tests import factories
from tests.test_admin import PNG
from tests.test_analytics import fill
from core.utils import client_fingerprint


def _bucket_keys() -> list[str]:
    """Ключи счётчиков, которые реально записались в бэкенд."""
    backend = stats_cache.get_backend()
    return [key for key in backend._values if key.startswith(ratelimit.RATE_PREFIX)]


class FakeRedis:
    """Минимальный Redis: строки с TTL и INCR."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[int, object]] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key, (0, None))[1]

    def set(self, key, value, ex=None):
        self.store[key] = (0, value)
        if ex is not None:
            self.ttls[key] = ex

    def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)

    def incr(self, key):
        current = int(self.get(key) or 0) + 1
        self.store[key] = (0, current)
        return current

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def ping(self):
        return True


@pytest.fixture
def survey_with_answers(client, db_session, clean_tables):
    """Анкета с ответами, принадлежащая владельцу текущего клиента,
    чтобы страницу статистики можно было открыть без ключа."""
    owner = factories.login_as(client, db_session)
    survey, _key = factories.make_survey(db_session, owner)
    questions = factories.questions_of(db_session, survey)
    fill(
        db_session,
        survey,
        [
            {questions[0].id: ["Отлично"]},
            {questions[0].id: ["Хорошо"]},
            {questions[4].id: ["быстро и удобно"]},
        ],
    )
    db_session.expire_all()
    return survey


class TestPayloadRoundTrip:
    def test_round_trip_keeps_numbers(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)

        restored = stats_from_payload(stats_to_payload(stats))

        assert restored.total_responses == stats.total_responses
        assert len(restored.questions) == len(stats.questions)

    def test_round_trip_keeps_option_counts(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)

        restored = stats_from_payload(stats_to_payload(stats))
        first = restored.questions[0]

        assert [option.text for option in first.options] == [
            "Отлично",
            "Хорошо",
            "Плохо",
        ]
        assert first.options[0].count == 1
        assert first.options[2].count == 0

    def test_round_trip_keeps_word_cloud(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)

        restored = stats_from_payload(stats_to_payload(stats))
        text_block = restored.questions[4].text

        assert text_block.total_answered == 1
        assert {word.text for word in text_block.words} == {"быстро", "удобно"}
        assert text_block.words[0].font_size > 0

    def test_round_trip_keeps_recent_dates(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)

        restored = stats_from_payload(stats_to_payload(stats))

        assert len(restored.recent) == len(stats.recent)
        assert restored.recent[0]["submitted_at"] is not None
        assert restored.recent[0]["cells"][0]["text"]


class TestMemoryBackend:
    def test_default_backend_is_memory_without_redis(self):
        assert stats_cache.get_backend().name == "memory"

    def test_store_and_load(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)
        stats_cache.store_stats(survey_with_answers.slug, stats)

        loaded = stats_cache.load_stats(survey_with_answers.slug)

        assert loaded is not None
        assert loaded.total_responses == stats.total_responses

    def test_miss_returns_none(self):
        assert stats_cache.load_stats("несуществующий") is None

    def test_invalidate_drops_entry(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)
        stats_cache.store_stats(survey_with_answers.slug, stats)

        stats_cache.invalidate(survey_with_answers.slug)

        assert stats_cache.load_stats(survey_with_answers.slug) is None

    def test_expired_entry_is_dropped(self, db_session, survey_with_answers):
        stats = collect_stats(db_session, survey_with_answers)
        stats_cache.store_stats(survey_with_answers.slug, stats)
        backend = stats_cache.get_backend()
        stored_key = stats_cache.stats_key(survey_with_answers.slug)
        _, value = backend._values[stored_key]
        backend._values[stored_key] = (-1.0, value)

        assert stats_cache.load_stats(survey_with_answers.slug) is None

    def test_broken_json_is_ignored(self, db_session, survey_with_answers):
        backend = stats_cache.get_backend()
        backend.set(stats_cache.stats_key(survey_with_answers.slug), "{не json", 30)

        assert stats_cache.load_stats(survey_with_answers.slug) is None
        assert backend.get(stats_cache.stats_key(survey_with_answers.slug)) is None

    def test_broken_shape_is_ignored(self, db_session, survey_with_answers):
        backend = stats_cache.get_backend()
        backend.set(stats_cache.stats_key(survey_with_answers.slug), '{"total": 1}', 30)

        assert stats_cache.load_stats(survey_with_answers.slug) is None


class TestRedisBackend:
    def test_redis_used_when_available(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379/0")
        monkeypatch.setattr(
            stats_cache, "_build_redis_backend", lambda: stats_cache.RedisBackend(fake)
        )
        stats_cache.reset_backend()

        assert stats_cache.get_backend().name == "redis"

    def test_memory_used_when_redis_not_configured(self, monkeypatch):
        monkeypatch.setattr("core.config.settings.redis_url", "")
        stats_cache.reset_backend()

        assert stats_cache.get_backend().name == "memory"

    def test_redis_round_trip(self, db_session, survey_with_answers, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379/0")
        monkeypatch.setattr(
            stats_cache, "_build_redis_backend", lambda: stats_cache.RedisBackend(fake)
        )
        stats_cache.reset_backend()
        stats = collect_stats(db_session, survey_with_answers)

        stats_cache.store_stats(survey_with_answers.slug, stats)
        loaded = stats_cache.load_stats(survey_with_answers.slug)

        assert loaded is not None
        assert loaded.total_responses == stats.total_responses
        assert stats_cache.stats_key(survey_with_answers.slug) in fake.store

    def test_unreachable_redis_falls_back_to_memory(self, monkeypatch):
        """`_build_redis_backend` возвращает None, когда Redis не отвечает."""
        monkeypatch.setattr(stats_cache, "_build_redis_backend", lambda: None)
        stats_cache.reset_backend()

        backend = stats_cache.get_backend()

        assert backend.name == "memory"
        assert backend.ping() is True

    def test_redis_probe_failure_returns_none(self, monkeypatch):
        import redis

        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def ping(self):
                raise ConnectionError("connection refused")

        monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: FakeClient()))
        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379/0")

        assert stats_cache._build_redis_backend() is None

    def test_failing_redis_read_does_not_raise(self, db_session, survey_with_answers, monkeypatch):
        class FailingBackend(stats_cache.Backend):
            name = "broken"

            def get(self, key):
                raise ConnectionError("упало")

            def set(self, key, value, ttl):
                raise ConnectionError("упало")

            def delete(self, key):
                raise ConnectionError("упало")

            def increment(self, key, ttl):
                raise ConnectionError("упало")

            def ping(self):
                raise ConnectionError("упало")

        monkeypatch.setattr("core.config.settings.redis_url", "redis://localhost:6379/0")
        monkeypatch.setattr(stats_cache, "_build_redis_backend", lambda: FailingBackend())
        stats_cache.reset_backend()

        assert stats_cache.load_stats(survey_with_answers.slug) is None
        stats_cache.invalidate(survey_with_answers.slug)
        assert stats_cache.status()["available"] is False


class TestCacheInvalidation:
    def test_new_answer_invalidates_cache(self, db_session, survey_with_answers):
        survey = survey_with_answers
        before = collect_stats(db_session, survey)
        stats_cache.store_stats(survey.slug, before)
        assert stats_cache.load_stats(survey.slug) is not None

        taking.submit_response(
            db_session,
            survey,
            {f"q{factories.questions_of(db_session, survey)[0].id}": ["Плохо"]},
            "device-invalidate-1",
        )

        assert stats_cache.load_stats(survey.slug) is None

    def test_stats_page_is_served_from_cache(self, client, db_session, survey_with_answers):
        first = client.get(f"/s/{survey_with_answers.slug}/stats")
        assert first.status_code == 200

        second = client.get(f"/s/{survey_with_answers.slug}/stats")

        assert second.status_code == 200
        assert stats_cache.load_stats(survey_with_answers.slug) is not None

    def test_cache_reflects_new_answer(self, client, db_session, survey_with_answers):
        survey = survey_with_answers
        client.get(f"/s/{survey.slug}/stats")
        question = factories.questions_of(db_session, survey)[0]

        taking.submit_response(
            db_session, survey, {f"q{question.id}": ["Плохо"]}, "device-invalidate-2"
        )
        page = client.get(f"/s/{survey.slug}/stats")

        assert page.status_code == 200
        cached = stats_cache.load_stats(survey.slug)
        assert cached is not None
        assert cached.total_responses == 4
        assert {option.text: option.count for option in cached.questions[0].options}[
            "Плохо"
        ] == 1


class TestRateLimit:
    def test_disabled_limit_always_allows(self):
        assert ratelimit.allow("page", "1.2.3.4", 0, 60) == (True, 0)

    def test_requests_under_limit_pass(self):
        results = [ratelimit.allow("page", "1.2.3.4", 3, 60) for _ in range(3)]

        assert all(allowed for allowed, _ in results)

    def test_request_over_limit_is_blocked(self):
        for _ in range(3):
            ratelimit.allow("page", "1.2.3.4", 3, 60)

        allowed, retry_after = ratelimit.allow("page", "1.2.3.4", 3, 60)

        assert allowed is False
        assert retry_after > 0

    def test_limit_is_per_identity(self):
        for _ in range(3):
            ratelimit.allow("page", "1.2.3.4", 3, 60)

        assert ratelimit.allow("page", "5.6.7.8", 3, 60)[0] is True

    def test_limit_is_per_bucket(self):
        for _ in range(3):
            ratelimit.allow("page", "1.2.3.4", 3, 60)

        assert ratelimit.allow("submit", "1.2.3.4", 3, 60)[0] is True

    def test_broken_backend_does_not_block(self, monkeypatch):
        class FailingBackend(stats_cache.Backend):
            name = "broken"

            def increment(self, key, ttl):
                raise ConnectionError("упало")

        monkeypatch.setattr(stats_cache, "_build_redis_backend", lambda: FailingBackend())
        stats_cache.reset_backend()

        assert ratelimit.allow("page", "1.2.3.4", 1, 60) == (True, 0)


class TestRateLimitMiddleware:
    def test_health_is_never_limited(self, client, monkeypatch):
        monkeypatch.setattr("core.config.settings.rate_limit_page_per_minute", 1)

        codes = [client.get("/health").status_code for _ in range(5)]

        assert codes == [200] * 5

    def test_static_is_never_limited(self, client):
        codes = [client.get("/static/css/style.css").status_code for _ in range(3)]

        assert codes == [200] * 3

    def test_exhausted_limit_returns_429(self, client, monkeypatch):
        monkeypatch.setattr("core.config.settings.rate_limit_page_per_minute", 2)
        client.get("/dashboard")
        client.get("/dashboard")

        response = client.get("/dashboard")

        assert response.status_code == 429
        assert response.headers["retry-after"]

    def test_owner_actions_do_not_spend_the_submit_budget(
        self, client, db_session, clean_tables, monkeypatch
    ):
        """Лимит ответов не должен съедаться действиями владельца.

        Правка, ключи, пауза и удаление живут под `/s/{slug}/…`, но в корзине
        `submit` им не место: иначе десяток кликов по настройкам блокирует
        отправку ответов с того же адреса.
        """
        owner = factories.login_as(client, db_session)
        survey, primary = factories.make_survey(db_session, owner)
        monkeypatch.setattr("core.config.settings.rate_limit_submit_per_minute", 3)
        monkeypatch.setattr("core.config.settings.rate_limit_create_per_hour", 100)
        monkeypatch.setattr("core.config.settings.rate_limit_page_per_minute", 100)

        codes = []
        for index in range(6):
            codes.append(client.post(f"/s/{survey.slug}/open", follow_redirects=False).status_code)
            codes.append(
                client.post(
                    f"/s/{survey.slug}/keys",
                    data={"label": f"коллега {index}"},
                    follow_redirects=False,
                ).status_code
            )
            codes.append(
                client.post(
                    f"/s/{survey.slug}/edit",
                    data={
                        "title": ["Правка"],
                        "q1_text": ["Вопрос?"],
                        "q1_type": ["text"],
                        "q1_required": ["1"],
                        "is_open": ["1"],
                    },
                    follow_redirects=False,
                ).status_code
            )

        assert set(codes) == {303}, sorted(set(codes))
        assert client.get(f"/s/{survey.slug}/stats?key={primary.token}").status_code == 200

    def test_answer_submission_uses_submit_budget(
        self, client, db_session, clean_tables, monkeypatch
    ):
        owner = factories.login_as(client, db_session)
        survey, _primary = factories.make_survey(db_session, owner)
        question = factories.questions_of(db_session, survey)[0]
        monkeypatch.setattr("core.config.settings.rate_limit_submit_per_minute", 2)

        codes = []
        for index in range(3):
            # Тест про корзину rate limit, а не про повторную отправку.
            # Чистим cookie, чтобы каждая отправка была новым респондентом:
            # IP у них общий, и лимит копится, а защита от дубля — нет.
            client.cookies.clear()
            resp = client.post(
                f"/s/{survey.slug}",
                data={f"q{question.id}": ["Отлично"]},
                headers={"user-agent": f"budget-device-{index}"},
                follow_redirects=False,
            )
            codes.append(resp.status_code)

        assert codes == [303, 303, 429], codes

    def test_429_is_a_page_not_json(self, client, monkeypatch):
        monkeypatch.setattr("core.config.settings.rate_limit_page_per_minute", 1)
        client.get("/dashboard")

        response = client.get("/dashboard")

        assert response.status_code == 429
        assert "text/html" in response.headers["content-type"]
        assert "Слишком много запросов" in response.text

    def test_images_do_not_spend_page_limit(self, client, media_dir, monkeypatch):
        """Браузер тянет картинки при отрисовке страницы. Считать их
        просмотрами страниц нельзя: одна анкета с обложкой съедала бы
        лимит, не показав ни одной страницы."""
        monkeypatch.setattr("core.config.settings.rate_limit_page_per_minute", 1)
        (media_dir / "обложка.png").write_bytes(PNG)

        codes = [
            client.get("/media/обложка.png").status_code for _ in range(3)
        ]

        assert codes == [200, 200, 200]
        assert client.get("/dashboard", follow_redirects=False).status_code == 303

    def test_raw_ip_is_not_used_as_a_key(self, client, monkeypatch):
        """В ключах счётчиков и в логах IP быть не должно."""
        monkeypatch.setattr("core.config.settings.rate_limit_page_per_minute", 5)

        client.get("/dashboard", headers={"x-forwarded-for": "203.0.113.7"})

        backend = stats_cache.get_backend()
        stored = " ".join(backend._values)
        assert "203.0.113.7" not in stored
        assert "203.0.113.7" not in _bucket_keys()

    def test_fingerprint_is_stable_and_opaque(self):
        first = client_fingerprint("203.0.113.7")
        second = client_fingerprint("203.0.113.7")

        assert first == second
        assert first != client_fingerprint("203.0.113.8")
        assert "203.0.113.7" not in first
        assert len(first) == 32

    def test_empty_ip_still_produces_a_key(self):
        assert client_fingerprint(None) == client_fingerprint("") != ""

    def test_redis_health_endpoint(self, client):
        response = client.get("/health/redis")

        assert response.status_code == 200
        body = response.json()
        assert body["backend"] in ("memory", "redis")
        assert body["available"] is True
        assert body["configured"] is False
