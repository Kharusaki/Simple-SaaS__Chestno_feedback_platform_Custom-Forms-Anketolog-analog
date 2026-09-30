"""Подсчёт статистики по ответам. Не знает про HTTP и шаблоны.

Один вопрос = один блок `QuestionStats`. Блок всегда содержит список
вариантов, даже если ответов не было, — иначе графики «прыгают» при первом
ответе.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy.orm import Session

from database.repositories import answers as answers_repo
from database.repositories import questions as questions_repo
from core.models import ANSWER_SKIPPED, NO_CHOICE, YES_CHOICE, Question, Survey

TEXT_PREVIEW_LIMIT = 60
TEXT_PREVIEW_COUNT = 5
CLOUD_LIMIT = 60
MIN_WORD_LENGTH = 3
CLOUD_FONT_MIN = 0.85
CLOUD_FONT_MAX = 2.1

STOP_WORDS = frozenset(
    """
    этот это тот та те эти это
    как какой какая какие кто что где когда почему
    и а но или же ли бы у в во на не ни за по из от до для о об при про
    без через над под между среди около после перед
    он она оно они мы вы ты мне нам вам ему ей им меня тебя себя
    его её их моя моё мои твой ваш наш ваш
    был была были было быть есть нет да
    уже ещё ещё всё всё всех всю всё это
    можно нужно надо должен хочет любят люблю
    там тут тогда сейчас потом раньше всегда иногда
    очень много мало больше меньше самый самые
    только тоже даже ли бы вот так тоже
    чтоб чтобы если когда пока
    thing things that this these those with from your have was were are but
    """.split()
)

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


@dataclass
class OptionStat:
    text: str
    count: int = 0
    percent: float = 0.0


@dataclass
class WordStat:
    text: str
    count: int = 0
    percent: int = 0
    font_size: float = CLOUD_FONT_MIN


@dataclass
class TextStat:
    total_answered: int = 0
    skipped: int = 0
    previews: list[str] = field(default_factory=list)
    words: list[WordStat] = field(default_factory=list)
    words_total: int = 0


@dataclass
class QuestionStats:
    question: Question | QuestionStub
    total_answers: int = 0
    skipped: int = 0
    answered: int = 0
    percent_answered: float = 0.0
    options: list[OptionStat] = field(default_factory=list)
    average: float | None = None
    min_value: int | None = None
    max_value: int | None = None
    yes_count: int = 0
    no_count: int = 0
    yes_percent: float = 0.0
    text: TextStat = field(default_factory=TextStat)

    @property
    def has_data(self) -> bool:
        return self.answered > 0


@dataclass
class SurveyStats:
    survey: Survey | SurveyStub
    total_responses: int = 0
    questions: list[QuestionStats] = field(default_factory=list)
    recent: list[dict[str, object]] = field(default_factory=list)


def _apply_percent(options: list[OptionStat], total: int) -> None:
    for option in options:
        option.percent = round(option.count * 100 / total, 1) if total else 0.0


def _tokenize(text: str) -> Iterator[str]:
    for chunk in _TOKEN_SPLIT.split(text.lower()):
        if len(chunk) < MIN_WORD_LENGTH or chunk.isdigit():
            continue
        if chunk in STOP_WORDS:
            continue
        yield chunk


def _font_size(count: int, peak: int) -> float:
    """Логарифмическая шкала: одно очень частое слово не приплющивает остальные."""
    if peak <= 1:
        return CLOUD_FONT_MAX
    ratio = math.log1p(count) / math.log1p(peak)
    return round(CLOUD_FONT_MIN + (CLOUD_FONT_MAX - CLOUD_FONT_MIN) * ratio, 2)


def build_word_cloud(texts: list[str], limit: int = CLOUD_LIMIT) -> tuple[list[WordStat], int]:
    """Облако тегов по свободным ответам: чем чаще слово, тем крупнее шрифт."""
    counter: Counter[str] = Counter()
    for text in texts:
        counter.update(_tokenize(text))
    if not counter:
        return [], 0

    top = counter.most_common(limit)
    peak = top[0][1]
    words = [
        WordStat(
            text=word,
            count=count,
            percent=round(count * 100 / peak),
            font_size=_font_size(count, peak),
        )
        for word, count in top
    ]
    return words, len(counter)


def _question_stats(
    question: Question, rows: list, total_responses: int
) -> QuestionStats:
    stats = QuestionStats(question=question, total_answers=total_responses)
    answered = [row for row in rows if row.kind != ANSWER_SKIPPED]
    stats.skipped = len(rows) - len(answered)
    stats.answered = len(answered)
    stats.percent_answered = (
        round(stats.answered * 100 / total_responses, 1) if total_responses else 0.0
    )

    if question.type in ("single", "multiple"):
        counter = Counter(row.value_text for row in answered if row.value_text)
        stats.options = [
            OptionStat(text=choice.text, count=counter.get(choice.text, 0))
            for choice in question.choices
        ]
        _apply_percent(stats.options, stats.answered)

    elif question.type == "yes_no":
        stats.yes_count = sum(1 for row in answered if row.value_text == YES_CHOICE)
        stats.no_count = sum(1 for row in answered if row.value_text == NO_CHOICE)
        stats.options = [
            OptionStat(text=YES_CHOICE, count=stats.yes_count),
            OptionStat(text=NO_CHOICE, count=stats.no_count),
        ]
        _apply_percent(stats.options, stats.answered)
        stats.yes_percent = (
            round(stats.yes_count * 100 / stats.answered, 1) if stats.answered else 0.0
        )

    elif question.type == "scale":
        values = [row.value_int for row in answered if row.value_int is not None]
        if values:
            stats.average = round(sum(values) / len(values), 2)
            stats.min_value = min(values)
            stats.max_value = max(values)
        low = question.scale_min if question.scale_min is not None else 1
        high = question.scale_max if question.scale_max is not None else 5
        counter = Counter(values)
        stats.options = [
            OptionStat(text=str(value), count=counter.get(value, 0))
            for value in range(low, high + 1)
        ]
        _apply_percent(stats.options, stats.answered)

    elif question.type == "text":
        texts = [row.value_text.strip() for row in answered if row.value_text]
        words, words_total = build_word_cloud(texts)
        stats.text = TextStat(
            total_answered=len(texts),
            skipped=stats.skipped,
            previews=[text[:TEXT_PREVIEW_LIMIT] for text in texts[:TEXT_PREVIEW_COUNT]],
            words=words,
            words_total=words_total,
        )

    return stats


def _recent_rows(
    session: Session, survey: Survey, questions: list[Question], limit: int
) -> list[dict[str, object]]:
    if not questions:
        return []
    responses = answers_repo.latest_submitted(session, survey.id, limit=limit)
    if not responses:
        return []

    grouped = answers_repo.answers_for_responses(
        session, [response.id for response in responses]
    )
    labels = {question.id: question.text for question in questions}

    rows: list[dict[str, object]] = []
    for response in responses:
        by_question: dict[int, list] = {}
        for answer in grouped.get(response.id, []):
            by_question.setdefault(answer.question_id, []).append(answer)

        cells = []
        for question in questions:
            values = [
                row.value_text if row.value_text is not None else str(row.value_int)
                for row in by_question.get(question.id, [])
                if row.kind != ANSWER_SKIPPED
            ]
            cells.append(
                {
                    "text": labels[question.id],
                    "value": ", ".join(values) if values else None,
                }
            )
        rows.append(
            {
                "id": response.id,
                "submitted_at": response.submitted_at,
                "cells": cells,
            }
        )
    return rows


def collect_stats(
    session: Session, survey: Survey, recent_limit: int = 10
) -> SurveyStats:
    questions = questions_repo.list_for_survey(session, survey.id)
    total = answers_repo.count_submitted(session, survey.id)
    grouped = answers_repo.answers_for_questions(
        session, [question.id for question in questions]
    )

    return SurveyStats(
        survey=survey,
        total_responses=total,
        questions=[
            _question_stats(question, grouped.get(question.id) or [], total)
            for question in questions
        ],
        recent=_recent_rows(session, survey, questions, recent_limit),
    )


@dataclass
class QuestionStub:
    """Вопрос из кэша вместо SQLAlchemy-модели.

    Кэш хранит JSON, а ORM-объект туда не положить. Шаблонам и графикам нужны
    только четыре поля, поэтому вместо целой модели достаточно этого среза.
    """

    id: int
    text: str
    type: str
    scale_max: int | None = None
    scale_min: int | None = None


@dataclass
class SurveyStub:
    id: int
    slug: str
    title: str


def stats_to_payload(stats: SurveyStats) -> dict[str, Any]:
    """Сводит агрегаты к JSON-совместимому виду для кэша."""
    return {
        "survey": {
            "id": stats.survey.id,
            "slug": stats.survey.slug,
            "title": stats.survey.title,
        },
        "total_responses": stats.total_responses,
        "questions": [
            {
                "question": {
                    "id": item.question.id,
                    "text": item.question.text,
                    "type": item.question.type,
                    "scale_max": item.question.scale_max,
                    "scale_min": item.question.scale_min,
                },
                "total_answers": item.total_answers,
                "skipped": item.skipped,
                "answered": item.answered,
                "percent_answered": item.percent_answered,
                "average": item.average,
                "min_value": item.min_value,
                "max_value": item.max_value,
                "yes_count": item.yes_count,
                "yes_percent": item.yes_percent,
                "options": [
                    {
                        "text": option.text,
                        "count": option.count,
                        "percent": option.percent,
                    }
                    for option in item.options
                ],
                "text": {
                    "total_answered": item.text.total_answered,
                    "skipped": item.text.skipped,
                    "previews": list(item.text.previews),
                    "words_total": item.text.words_total,
                    "words": [
                        {
                            "text": word.text,
                            "count": word.count,
                            "percent": word.percent,
                            "font_size": word.font_size,
                        }
                        for word in item.text.words
                    ],
                },
            }
            for item in stats.questions
        ],
        "recent": [
            {
                "id": row["id"],
                "submitted_at": row["submitted_at"].isoformat()
                if row["submitted_at"]
                else None,
                "cells": list(row["cells"]),
            }
            for row in stats.recent
        ],
    }


def stats_from_payload(payload: dict[str, Any]) -> SurveyStats:
    """Обратное преобразование: те же агрегаты, но без обращения к базе."""
    survey = payload["survey"]
    return SurveyStats(
        survey=SurveyStub(
            id=survey["id"], slug=survey["slug"], title=survey["title"]
        ),
        total_responses=payload["total_responses"],
        questions=[
            QuestionStats(
                question=QuestionStub(**item["question"]),
                total_answers=item["total_answers"],
                skipped=item["skipped"],
                answered=item["answered"],
                percent_answered=item["percent_answered"],
                average=item["average"],
                min_value=item["min_value"],
                max_value=item["max_value"],
                yes_count=item["yes_count"],
                yes_percent=item["yes_percent"],
                options=[OptionStat(**option) for option in item["options"]],
                text=TextStat(
                    total_answered=item["text"]["total_answered"],
                    skipped=item["text"]["skipped"],
                    previews=list(item["text"]["previews"]),
                    words_total=item["text"]["words_total"],
                    words=[WordStat(**word) for word in item["text"]["words"]],
                ),
            )
            for item in payload["questions"]
        ],
        recent=[
            {
                "id": row["id"],
                "submitted_at": datetime.fromisoformat(row["submitted_at"])
                if row["submitted_at"]
                else None,
                "cells": list(row["cells"]),
            }
            for row in payload["recent"]
        ],
    )
