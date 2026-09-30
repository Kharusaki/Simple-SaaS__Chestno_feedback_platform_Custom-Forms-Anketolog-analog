"""Графики статистики: страница не зависит от CDN, а данные сходятся с разметкой.

Два риска, которые не видно на глаз:
- Chart.js приходит с CDN. Если библиотека не загрузилась, на странице не
  должно остаться пустого канваса, поэтому блок графика скрыт в разметке и
  показывается скриптом. Проверяем именно это, а не работу библиотеки.
- `stats_charts.js` ищет канвас по `data-chart-for` из данных Jinja. Если
  вопрос без графика в данных, но с блоком в разметке (или наоборот),
  график просто не нарисуется и никто не заметит. Поэтому сверяем оба
  списка между собой.
"""

from __future__ import annotations

import json
import re

from surveys import taking
from tests import factories

CHART_BLOCK = re.compile(r'data-chart-for="(\d+)"')
CANVAS = re.compile(r'id="chart-q(\d+)"')
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
PAYLOAD = re.compile(
    r'<script id="charts-data" type="application/json">(.*?)</script>', re.S
)


def make_answered(client, db_session):
    mine = factories.login_as(client, db_session)
    survey, _ = factories.make_survey(db_session, mine)
    questions = factories.questions_of(db_session, survey)
    taking.submit_response(
        db_session,
        survey,
        {
            f"q{questions[0].id}": ["Отлично"],
            f"q{questions[1].id}": ["API"],
            f"q{questions[2].id}": ["да"],
            f"q{questions[3].id}": ["9"],
        },
        "device-1",
    )
    return survey


def charts_of(response) -> list[dict]:
    match = PAYLOAD.search(response.text)
    assert match is not None, "на странице нет блока с данными графиков"
    return json.loads(match.group(1))


class TestChartBlocks:
    def test_chart_block_is_hidden_before_script_runs(self, client, db_session, clean_tables):
        """Скрытый блок — это то, что спасает страницу без интернета."""
        survey = make_answered(client, db_session)

        response = client.get(f"/s/{survey.slug}/stats")

        assert 'class="chart-box" data-chart-for=' in response.text
        assert re.search(r'<div class="chart-box"[^>]*\bhidden\b', response.text)

    def test_cdn_script_is_included(self, client, db_session, clean_tables):
        survey = make_answered(client, db_session)

        response = client.get(f"/s/{survey.slug}/stats")

        assert "chart_cdn" not in response.text
        assert re.search(r'<script src="[^"]*chart[^"]*" defer></script>', response.text)

    def test_payload_ids_match_rendered_blocks(self, client, db_session, clean_tables):
        """Главная проверка: скрипт ищет блок по `question_id` из JSON."""
        survey = make_answered(client, db_session)

        response = client.get(f"/s/{survey.slug}/stats")
        payload = {chart["question_id"] for chart in charts_of(response)}

        assert payload == {int(x) for x in CHART_BLOCK.findall(response.text)}

    def test_payload_ids_match_canvas_ids(self, client, db_session, clean_tables):
        survey = make_answered(client, db_session)

        response = client.get(f"/s/{survey.slug}/stats")
        payload = {chart["question_id"] for chart in charts_of(response)}

        assert payload == {int(x) for x in CANVAS.findall(response.text)}

    def test_every_chart_has_labels_values_and_type(self, client, db_session, clean_tables):
        survey = make_answered(client, db_session)

        charts = charts_of(client.get(f"/s/{survey.slug}/stats"))

        assert charts
        for chart in charts:
            assert chart["type"] in {"bar", "doughnut", "pie", "line"}
            assert len(chart["labels"]) == len(chart["values"])
            # Палитра короче шкалы 1-10, Chart.js повторяет цвета сам. Важно
            # лишь, что первый цвет есть: скрипт ставит его в `borderColor`.
            assert chart["colors"]
            assert all(HEX_COLOR.match(color) for color in chart["colors"])

    def test_text_question_gets_no_chart(self, client, db_session, clean_tables):
        survey = make_answered(client, db_session)
        text_question = factories.questions_of(db_session, survey)[4]

        charts = charts_of(client.get(f"/s/{survey.slug}/stats"))

        assert text_question.id not in {chart["question_id"] for chart in charts}

    def test_unanswered_survey_has_no_chart_blocks(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey, _ = factories.make_survey(db_session, mine)

        response = client.get(f"/s/{survey.slug}/stats")

        assert CHART_BLOCK.findall(response.text) == []


class TestNoDuplicateShareLink:
    """Ссылка на прохождение уже есть на странице опроса и в настройках."""

    def test_stats_page_has_no_share_link(self, client, db_session, clean_tables):
        survey = make_answered(client, db_session)

        response = client.get(f"/s/{survey.slug}/stats")

        assert "copy.js" not in response.text
        assert "Скопировать ссылку" not in response.text

    def test_empty_stats_still_links_to_survey(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey, _ = factories.make_survey(db_session, mine)

        response = client.get(f"/s/{survey.slug}/stats")

        assert "Ответов пока нет" in response.text
        assert f"/s/{survey.slug}" in response.text
