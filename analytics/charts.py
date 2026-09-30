"""Данные для Chart.js.

Графики получают на вход готовые списки, а не сырые ответы: иначе разбор
данных уезжает в шаблон, а логика расчёта долей дублируется в двух местах.
"""

from __future__ import annotations

from analytics.service import QuestionStats, SurveyStats

CHART_VERSION = "4.4.7"
CHART_CDN = (
    f"https://cdn.jsdelivr.net/npm/chart.js@{CHART_VERSION}/dist/chart.umd.min.js"
)

PALETTE = [
    "#4f46e5", "#0ea5e9", "#059669", "#d97706",
    "#dc2626", "#7c3aed", "#0891b2", "#65a30d",
]


def option_chart(stats: QuestionStats) -> dict | None:
    """Горизонтальная столбчатая диаграмма распределения вариантов."""
    options = [option for option in stats.options]
    if not any(option.count for option in options):
        return None
    return {
        "question_id": stats.question.id,
        "type": "bar",
        "chart": stats.question.text,
        "labels": [option.text for option in options],
        "values": [option.count for option in options],
        "colors": PALETTE[: max(1, len(options))],
    }


def scale_chart(stats: QuestionStats) -> dict | None:
    """Линейная диаграмма средней оценки по шкале."""
    if stats.average is None:
        return None
    labels = [option.text for option in stats.options]
    values = [option.count for option in stats.options]
    return {
        "question_id": stats.question.id,
        "type": "line",
        "chart": f"Распределение оценок · средняя {stats.average}",
        "labels": labels,
        "values": values,
        "colors": PALETTE,
    }


def yes_no_chart(stats: QuestionStats) -> dict | None:
    if not stats.answered:
        return None
    return {
        "question_id": stats.question.id,
        "type": "doughnut",
        "chart": stats.question.text,
        "labels": [option.text for option in stats.options],
        "values": [option.count for option in stats.options],
        "colors": PALETTE[: len(stats.options)],
    }


def build_charts(survey_stats: SurveyStats) -> list[dict]:
    charts: list[dict] = []
    for stats in survey_stats.questions:
        if stats.question.type == "yes_no":
            chart = yes_no_chart(stats)
        elif stats.question.type == "scale":
            chart = scale_chart(stats)
        else:
            chart = option_chart(stats)
        if chart is not None:
            charts.append(chart)
    return charts
