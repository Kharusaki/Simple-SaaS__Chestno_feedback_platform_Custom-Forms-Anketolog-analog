"""Проверки демоданных: `python -m scripts.seed_demo`."""

from __future__ import annotations

import pytest

from scripts import seed_demo


@pytest.fixture
def seeded(clean_tables, capsys) -> list[tuple[str, str, str]]:
    """Прогоняет скрипт и отдаёт список (название, slug, токен)."""
    assert seed_demo.main(["--responses", "25", "--reset"]) == 0
    capsys.readouterr()
    from database.session import SessionLocal

    from core import models as models
    session = SessionLocal()
    try:
        surveys = session.query(models.Survey).order_by(models.Survey.id).all()
        return [(s.title, s.slug, s.keys[0].token) for s in surveys]
    finally:
        session.close()


def test_creates_all_demos(seeded):
    titles = [title for title, _slug, _token in seeded]
    assert titles == [
        "Демо: обратная связь после покупки",
        "Демо: типы вопросов",
        "Демо: рабочая встреча",
    ]


def test_responses_count_is_25_per_survey(seeded, db_session):
    from database.repositories import answers as answers_repo
    from surveys import crud

    session = db_session
    for _title, slug, _token in seeded:
        survey = crud.get_by_slug(session, slug)
        assert answers_repo.count_submitted(session, survey.id) == 25


def test_owner_can_log_in_and_see_demos(seeded, client, db_session):
    from auth.service import authenticate
    from surveys import crud

    assert authenticate(db_session, seed_demo.DEMO_LOGIN, seed_demo.DEMO_PASSWORD) is not None
    client.post("/login", data={"username": seed_demo.DEMO_LOGIN, "password": seed_demo.DEMO_PASSWORD})
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "Демо: типы вопросов" in dashboard.text


def test_stats_open_by_primary_key(seeded, client):
    _title, slug, token = seeded[0]
    page = client.get(f"/s/{slug}/stats?key={token}")
    assert page.status_code == 200
    assert "обратная связь после покупки" in page.text.lower()


def test_word_cloud_has_clear_leader(seeded, db_session):
    """Лидер должен отрываться от второго места, а не делить вершину."""
    from analytics import service as analytics
    from surveys import crud

    _title, slug, _token = seeded[0]
    survey = crud.get_by_slug(db_session, slug)
    stats = analytics.collect_stats(db_session, survey)
    text_stats = [q for q in stats.questions if q.text.words]
    assert text_stats, "в демо-анкете должен быть вопрос со свободным ответом"

    words = text_stats[0].text.words
    assert words[0].text == "скорость"
    assert words[0].count >= words[1].count * 2
    assert words[0].font_size > words[1].font_size
    assert text_stats[0].text.words_total >= 20, "ответы должны быть разнообразными"


def test_some_questions_left_unanswered(seeded, db_session):
    """Пропуски нужны, чтобы в статистике было видно незаполненные вопросы."""
    from analytics import service as analytics
    from surveys import crud

    _title, slug, _token = seeded[0]
    survey = crud.get_by_slug(db_session, slug)
    stats = analytics.collect_stats(db_session, survey)
    assert any(q.skipped > 0 for q in stats.questions)


def test_themes_differ_between_demos(seeded, db_session):
    from surveys import crud

    themes = {crud.get_by_slug(db_session, slug).theme for _t, slug, _k in seeded}
    assert themes == {"light", "mint", "sand"}


def test_reset_is_idempotent(seeded, db_session, capsys):
    """Повторный прогон с `--reset` не копит копии анкет."""
    from surveys import crud

    before = {slug for _t, slug, _k in seeded}
    assert seed_demo.main(["--responses", "25", "--reset"]) == 0
    capsys.readouterr()
    db_session.expire_all()
    after = {survey.slug for survey in db_session.query(crud.Survey).all()}
    assert not (before & after), "старые анкеты должны быть удалены"
    assert len(after) == 3


def test_reset_keeps_foreign_surveys(seeded, db_session, capsys):
    """`--reset` не имеет права удалять ручные анкеты.

    Скрипт запускают на рабочей базе, где лежат анкеты владельца. Удаление
    «всех анкет» выглядело безобидно, пока не оказалось, что ручные анкеты
    исчезают вместе с демо-данными, а восстановить их можно только из
    бэкапа.
    """
    from surveys import crud
    from tests import factories

    mine = factories.make_survey(db_session, factories.unique_owner(db_session, "reset"))[0]
    slug = mine.slug

    assert seed_demo.main(["--responses", "1", "--reset"]) == 0
    capsys.readouterr()
    db_session.expire_all()

    assert crud.get_by_slug(db_session, slug) is not None


def test_reset_keeps_manually_created_demo_owner_survey(seeded, db_session, capsys):
    """Анкета демо-владельца с другим названием тоже остаётся.

    Отбор идёт по названию из `DEMOS`, поэтому случайно совпавшее имя
    единственного настоящего демо-владельца не делает анкету удаляемой.
    """
    from surveys import crud
    from tests import factories

    from core import models as models
    demo = (
        db_session.query(models.User)
        .filter(models.User.username == seed_demo.DEMO_LOGIN)
        .one()
    )
    other = factories.make_survey(db_session, demo, title="Моя рабочая анкета")[0]
    slug = other.slug

    assert seed_demo.main(["--responses", "1", "--reset"]) == 0
    capsys.readouterr()
    db_session.expire_all()

    assert crud.get_by_slug(db_session, slug) is not None
