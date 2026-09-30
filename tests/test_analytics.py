from __future__ import annotations

import csv
import io
import json
import re

import pytest
from sqlalchemy import select

from analytics import charts as charts_mod
from analytics import csv_export
from analytics.service import (
    CLOUD_FONT_MAX,
    CLOUD_FONT_MIN,
    TEXT_PREVIEW_LIMIT,
    build_word_cloud,
    collect_stats,
)
from core.models import Answer, Response, User
from surveys import taking
from tests import factories


def owner(db_session) -> User:
    return factories.unique_owner(db_session, "analytics")


def answer_form(question_id: int, *values: str) -> dict[str, list[str]]:
    return {f"q{question_id}": list(values)}


def fill(session, survey, responses: list[dict[int, list[str]]]) -> None:
    """Каждый элемент — ответы одного респондента: {question_id: [значения]}."""
    questions = {question.id: question for question in factories.questions_of(session, survey)}
    for index, answers in enumerate(responses):
        form: dict[str, list[str]] = {}
        for question_id, values in answers.items():
            form.update(answer_form(question_id, *values))
        taking.submit_response(session, survey, form, f"device-{index}")


def stats_for(session, survey):
    session.expire_all()
    return collect_stats(session, factories.survey_by_slug(session, survey.slug))


def block(stats, text: str):
    return next(item for item in stats.questions if item.question.text == text)


class TestQuestionStatsSingle:
    def test_counts_and_percents_are_relative_to_answered(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        single = factories.questions_of(db_session, survey)[0]
        fill(db_session, survey, [
            {single.id: ["Отлично"]},
            {single.id: ["Отлично"]},
            {single.id: ["Хорошо"]},
            {single.id: []},
        ])

        block_ = block(stats_for(db_session, survey), "Как вам сервис?")

        assert stats_for(db_session, survey).total_responses == 4
        assert block_.answered == 3
        assert block_.skipped == 1
        assert [(o.text, o.count, o.percent) for o in block_.options] == [
            ("Отлично", 2, 66.7),
            ("Хорошо", 1, 33.3),
            ("Плохо", 0, 0.0),
        ]

    def test_all_choices_present_even_without_answers(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))

        block_ = block(stats_for(db_session, survey), "Как вам сервис?")

        assert [option.text for option in block_.options] == ["Отлично", "Хорошо", "Плохо"]
        assert block_.has_data is False
        assert block_.percent_answered == 0.0


class TestQuestionStatsYesNo:
    def test_yes_percent_of_answered(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        yes_no = factories.questions_of(db_session, survey)[2]
        fill(db_session, survey, [
            {yes_no.id: ["Да"]},
            {yes_no.id: ["Да"]},
            {yes_no.id: ["Нет"]},
            {yes_no.id: ["Нет"]},
            {yes_no.id: ["Нет"]},
        ])

        block_ = block(stats_for(db_session, survey), "Будете рекомендовать?")

        assert (block_.yes_count, block_.no_count) == (2, 3)
        assert block_.yes_percent == 40.0
        assert [(o.text, o.count) for o in block_.options] == [("Да", 2), ("Нет", 3)]


class TestQuestionStatsScale:
    def test_average_min_max(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        scale = factories.questions_of(db_session, survey)[3]
        fill(db_session, survey, [
            {scale.id: ["10"]},
            {scale.id: ["5"]},
            {scale.id: ["7"]},
        ])

        block_ = block(stats_for(db_session, survey), "Оценка от 1 до 10")

        assert block_.average == 7.33
        assert (block_.min_value, block_.max_value) == (5, 10)

    def test_scale_options_cover_whole_range_with_zeros(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        scale = factories.questions_of(db_session, survey)[3]
        fill(db_session, survey, [{scale.id: ["1"]}])

        options = block(stats_for(db_session, survey), "Оценка от 1 до 10").options

        assert [option.text for option in options] == [str(n) for n in range(1, 11)]
        assert options[0].count == 1
        assert sum(option.count for option in options[1:]) == 0


class TestQuestionStatsMultiple:
    def test_one_response_can_count_in_two_options(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        multiple = factories.questions_of(db_session, survey)[1]
        fill(db_session, survey, [
            {multiple.id: ["API", "Веб-интерфейс"]},
            {multiple.id: ["API"]},
        ])

        block_ = block(stats_for(db_session, survey), "Что использовали?")

        assert [(o.text, o.count) for o in block_.options] == [
            ("API", 2),
            ("Веб-интерфейс", 1),
        ]


class TestQuestionStatsText:
    def test_previews_are_truncated(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        text = factories.questions_of(db_session, survey)[4]
        long_answer = "я" * 200
        fill(db_session, survey, [{text.id: [long_answer]}, {text.id: ["коротко"]}])

        block_ = block(stats_for(db_session, survey), "Комментарий")

        assert block_.text.total_answered == 2
        assert len(block_.text.previews[0]) == TEXT_PREVIEW_LIMIT
        assert block_.text.previews[1] == "коротко"


class TestRecentAndTotals:
    def test_draft_response_does_not_count(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        single = factories.questions_of(db_session, survey)[0]
        taking.submit_response(
            db_session, survey, answer_form(single.id, "Отлично"), "device-draft"
        )
        draft = db_session.scalar(select(Response).order_by(Response.id.desc()))
        draft.submitted_at = None
        db_session.commit()

        assert stats_for(db_session, survey).total_responses == 0

    def test_recent_is_capped_and_newest_first(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        single = factories.questions_of(db_session, survey)[0]
        fill(db_session, survey, [{single.id: ["Отлично"]} for _ in range(15)])

        stats = stats_for(db_session, survey)

        assert len(stats.recent) == 10
        assert stats.total_responses == 15

    def test_recent_row_joins_multiple_answers_with_comma(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        multiple = factories.questions_of(db_session, survey)[1]
        fill(db_session, survey, [{multiple.id: ["API", "Веб-интерфейс"]}])

        cells = stats_for(db_session, survey).recent[0]["cells"]

        assert cells[1]["value"] == "API, Веб-интерфейс"
        assert cells[4]["value"] is None

    def test_recent_row_shows_only_own_answers(self, db_session, clean_tables):
        """Соседние строки не должны подмешивать ответы друг друга."""
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = factories.questions_of(db_session, survey)
        fill(db_session, survey, [
            {questions[0].id: ["Отлично"], questions[4].id: ["первый"]},
            {questions[0].id: ["Плохо"], questions[4].id: ["второй"]},
        ])

        rows = stats_for(db_session, survey).recent

        newest_first = [row["cells"][4]["value"] for row in rows]
        assert newest_first == ["второй", "первый"]
        assert [row["cells"][0]["value"] for row in rows] == ["Плохо", "Отлично"]


class TestCharts:
    def test_no_charts_without_answers(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))

        assert charts_mod.build_charts(stats_for(db_session, survey)) == []

    def test_chart_per_answered_question(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = factories.questions_of(db_session, survey)
        fill(db_session, survey, [{
            questions[0].id: ["Отлично"],
            questions[2].id: ["Да"],
            questions[3].id: ["8"],
        }])

        charts = charts_mod.build_charts(stats_for(db_session, survey))

        by_type = {chart["type"] for chart in charts}
        assert by_type == {"bar", "doughnut", "line"}
        assert all(chart["labels"] for chart in charts)

    def test_doughnut_values_match_yes_no(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        yes_no = factories.questions_of(db_session, survey)[2]
        fill(db_session, survey, [{yes_no.id: ["Да"]}, {yes_no.id: ["Нет"]}])

        chart = next(
            c for c in charts_mod.build_charts(stats_for(db_session, survey))
            if c["type"] == "doughnut"
        )

        assert chart["labels"] == ["Да", "Нет"]
        assert chart["values"] == [1, 1]

    def test_line_chart_mentions_average(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        scale = factories.questions_of(db_session, survey)[3]
        fill(db_session, survey, [{scale.id: ["6"]}, {scale.id: ["8"]}])

        chart = next(
            c for c in charts_mod.build_charts(stats_for(db_session, survey))
            if c["type"] == "line"
        )

        assert "7.0" in chart["chart"]


class TestCsvExport:
    def test_header_and_rows(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = factories.questions_of(db_session, survey)
        fill(db_session, survey, [
            {
                questions[0].id: ["Отлично"],
                questions[1].id: ["API", "Веб-интерфейс"],
                questions[2].id: ["Да"],
                questions[3].id: ["9"],
                questions[4].id: ["ок"],
            }
        ])

        rows = list(csv.reader(_csv_rows(csv_export.build_csv(db_session, survey)), delimiter=";"))

        assert rows[0] == ["Ответ №", "Дата"] + [q.text for q in questions]
        assert rows[1][2] == "Отлично"
        assert rows[1][3] == "API, Веб-интерфейс"
        assert rows[1][5] == "9"
        assert rows[1][6] == "ок"

    def test_semicolon_delimiter_and_bom_for_excel(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        fill(db_session, survey, [{}])

        body = csv_export.build_csv(db_session, survey)

        assert body.startswith(csv_export.CSV_PREFIX)
        assert ";" in body

    def test_formula_injection_is_quoted_not_executed(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        text = factories.questions_of(db_session, survey)[4]
        fill(db_session, survey, [{text.id: ["=1+1"]}])

        body = csv_export.build_csv(db_session, survey)

        assert "=1+1" in body

    def test_answer_with_delimiter_stays_in_one_cell(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        text = factories.questions_of(db_session, survey)[4]
        fill(db_session, survey, [{text.id: ["а;б"]}])

        rows = list(csv.reader(_csv_rows(csv_export.build_csv(db_session, survey)), delimiter=";"))

        assert rows[1][-1] == "а;б"

    def test_only_submitted_responses_are_exported(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        taking.submit_response(db_session, survey, {}, "device-draft")
        draft = db_session.scalar(select(Response).order_by(Response.id.desc()))
        draft.submitted_at = None
        db_session.commit()

        rows = list(csv.reader(_csv_rows(csv_export.build_csv(db_session, survey)), delimiter=";"))

        assert len(rows) == 1

    def test_each_row_keeps_its_own_answers(self, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        questions = factories.questions_of(db_session, survey)
        fill(db_session, survey, [
            {questions[0].id: ["Отлично"], questions[4].id: ["первый"]},
            {questions[0].id: ["Плохо"], questions[4].id: ["второй"]},
        ])

        rows = list(csv.reader(_csv_rows(csv_export.build_csv(db_session, survey)), delimiter=";"))

        assert [row[2] for row in rows[1:]] == ["Отлично", "Плохо"]
        assert [row[-1] for row in rows[1:]] == ["первый", "второй"]


def _csv_rows(body: str) -> io.StringIO:
    return io.StringIO(body.lstrip(csv_export.CSV_PREFIX))


class TestStatsRoutes:
    def test_owner_sees_stats(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey = factories.make_survey(db_session, mine)[0]
        fill(db_session, survey, [{}])

        response = client.get(f"/s/{survey.slug}/stats")

        assert response.status_code == 200
        assert "Статистика ответов" in response.text

    def test_foreign_owner_gets_403(self, client, db_session, clean_tables):
        survey, _ = factories.make_survey(db_session, owner(db_session))
        client.get("/dashboard")

        response = client.get(f"/s/{survey.slug}/stats")

        assert response.status_code == 403
        assert "Доступ закрыт" in response.text

    def test_unknown_slug_gets_404(self, client, db_session, clean_tables):
        client.get("/dashboard")

        response = client.get("/s/zzzzzzzz/stats")

        assert response.status_code == 404
        assert "Страница не найдена" in response.text

    def test_csv_download_sets_headers(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey = factories.make_survey(db_session, mine, title="Отчёт: продажи/2024")[0]

        response = client.get(f"/s/{survey.slug}/stats.csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers["content-disposition"]
        assert "csv" in response.headers["content-disposition"]

    def test_empty_stats_page_invites_to_share(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey = factories.make_survey(db_session, mine)[0]

        response = client.get(f"/s/{survey.slug}/stats")

        assert "Ответов пока нет" in response.text
        assert f"/s/{survey.slug}" in response.text

    def test_charts_payload_is_valid_json(self, client, db_session, clean_tables):
        mine = factories.login_as(client, db_session)
        survey = factories.make_survey(db_session, mine)[0]
        questions = factories.questions_of(db_session, survey)
        fill(db_session, survey, [{questions[0].id: ["Отлично"]}])

        response = client.get(f"/s/{survey.slug}/stats")

        match = re.search(
            r'<script id="charts-data" type="application/json">(.*?)</script>',
            response.text,
            re.S,
        )
        assert match is not None
        payload = json.loads(match.group(1))
        assert payload[0]["labels"] == ["Отлично", "Хорошо", "Плохо"]
        assert payload[0]["values"] == [1, 0, 0]


class TestWordCloud:
    """Облако тегов по свободным ответам."""

    def test_counts_words_across_answers(self):
        words, total = build_word_cloud(["скорость нужна ведь", "скорость приятна"])

        assert total == 4
        counts = {word.text: word.count for word in words}
        assert counts["скорость"] == 2
        assert counts["нужна"] == 1
        assert counts["приятна"] == 1
        assert words[0].text == "скорость"

    def test_ignores_stop_words(self):
        words, _total = build_word_cloud(["и в на с не что это"])

        assert words == []

    def test_ignores_short_words_and_numbers(self):
        words, _total = build_word_cloud(["я из 2024 10"])

        assert words == []

    def test_keeps_case_and_punctuation_out(self):
        words, _total = build_word_cloud(["Скорость, Удобство! скорость."])

        assert [word.text for word in words] == ["скорость", "удобство"]
        assert words[0].count == 2

    def test_sorted_by_frequency_desc(self):
        words, _total = build_word_cloud(["альфа", "бета", "гамма", "дельта", "альфа"])

        counts = [word.count for word in words]
        assert counts == sorted(counts, reverse=True)

    def test_font_grows_with_frequency(self):
        words, _total = build_word_cloud(["часто", "часто", "часто", "часто", "редко"])

        sizes = {word.text: word.font_size for word in words}
        assert sizes["часто"] > sizes["редко"]
        assert sizes["часто"] == CLOUD_FONT_MAX

    def test_font_never_exceeds_bounds(self):
        words, _total = build_word_cloud(["раз"] * 50 + ["один"])

        for word in words:
            assert CLOUD_FONT_MIN <= word.font_size <= CLOUD_FONT_MAX

    def test_percent_is_relative_to_peak(self):
        words, _total = build_word_cloud(["часто", "часто", "часто", "редко"])

        percents = {word.text: word.percent for word in words}
        assert percents["часто"] == 100
        assert percents["редко"] == 33

    def test_single_occurrence_uses_max_font(self):
        words, _total = build_word_cloud(["слово"])

        assert words[0].font_size == CLOUD_FONT_MAX

    def test_respects_limit(self):
        words, total = build_word_cloud([" ".join(f"слово{i}" for i in range(200))], limit=20)

        assert len(words) == 20
        assert total == 200

    def test_empty_input_gives_nothing(self):
        assert build_word_cloud([]) == ([], 0)
        assert build_word_cloud(["   ", "!!!"]) == ([], 0)


class TestTextQuestionCloud:
    def test_collected_stats_carry_words(self, db_session, clean_tables):
        user = owner(db_session)
        survey, _key = factories.make_survey(db_session, user)
        text_question = factories.questions_of(db_session, survey)[4]

        fill(
            db_session,
            survey,
            [
                {text_question.id: ["удобный интерфейс"]},
                {text_question.id: ["удобный интерфейс", "удобный"]},
            ],
        )

        stats = collect_stats(db_session, survey)
        block = next(item for item in stats.questions if item.question.id == text_question.id)
        words = {word.text: word for word in block.text.words}

        assert block.text.total_answered == 2
        assert words["удобный"].count == 3
        assert words["удобный"].font_size > words["интерфейс"].font_size

    def test_blank_text_answers_do_not_pollute_cloud(self, db_session, clean_tables):
        user = owner(db_session)
        survey, _key = factories.make_survey(db_session, user)
        text_question = factories.questions_of(db_session, survey)[4]

        fill(db_session, survey, [{text_question.id: ["   "]}])

        stats = collect_stats(db_session, survey)
        block = next(item for item in stats.questions if item.question.id == text_question.id)
        assert block.text.words == []

    def test_stats_page_renders_cloud(self, client, db_session, clean_tables):
        user = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, user)
        text_question = factories.questions_of(db_session, survey)[4]
        fill(
            db_session,
            survey,
            [{text_question.id: ["статистика удобная"]}, {text_question.id: ["статистика"]}],
        )

        response = client.get(f"/s/{survey.slug}/stats")

        assert response.status_code == 200
        assert 'class="tag-cloud"' in response.text
        assert 'class="tag-cloud-item"' in response.text
        assert re.search(r'style="font-size: \d+\.\d+rem"', response.text)

    def test_cloud_absent_without_text_answers(self, client, db_session, clean_tables):
        user = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, user)
        first = factories.questions_of(db_session, survey)[0]
        fill(db_session, survey, [{first.id: ["Отлично"]}])

        response = client.get(f"/s/{survey.slug}/stats")

        assert 'class="tag-cloud"' not in response.text
