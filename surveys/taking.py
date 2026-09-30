"""Прохождение опроса: сбор сырой формы, валидация и запись ответов.

Правила мягкие: любой невалидный или отсутствующий ответ превращается в
`kind='skipped'` и не ломает отправку всего опроса. Обязательность влияет
только на статистику, а не на приём формы — иначе один неверно заполненный
вопрос отбрасывал бы весь ответ респондента.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from analytics import cache as stats_cache
from database.repositories import answers as answers_repo
from database.repositories import questions as questions_repo
from database.repositories import surveys as surveys_repo
from core.models import (
    ANSWER_SKIPPED,
    YES_NO_CHOICES,
    Answer,
    Question,
    Response,
    Survey,
)
from core.utils import utcnow

logger = logging.getLogger(__name__)

TEXT_MAX = 2000


class SurveyClosedError(Exception):
    """Опрос закрыт для приёма ответов."""


class AlreadyRespondedError(Exception):
    """Этот респондент уже отправлял ответ на этот опрос."""


@dataclass
class PreparedAnswer:
    kind: str
    value_text: str | None = None
    value_int: int | None = None


@dataclass
class SubmissionResult:
    response: Response
    saved: int = 0
    skipped: int = 0
    kinds: dict[str, int] = field(default_factory=dict)


def _submitted_texts(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split("\n") if part.strip()]


def _prepare_choice(
    question: Question, raw: str
) -> list[PreparedAnswer]:
    allowed = {choice.text: choice.text for choice in question.choices}
    return [
        PreparedAnswer(kind=question.type, value_text=allowed[text])
        for text in _submitted_texts(raw)
        if text in allowed
    ]


def _prepare_yes_no(question: Question, raw: str) -> PreparedAnswer:
    value = (raw or "").strip()
    if value in YES_NO_CHOICES:
        return PreparedAnswer(kind="yes_no", value_text=value)
    return PreparedAnswer(kind=ANSWER_SKIPPED)


def _prepare_scale(question: Question, raw: str) -> PreparedAnswer:
    text = (raw or "").strip()
    if not text:
        return PreparedAnswer(kind=ANSWER_SKIPPED)
    try:
        value = int(text)
    except ValueError:
        return PreparedAnswer(kind=ANSWER_SKIPPED)
    low = question.scale_min if question.scale_min is not None else 1
    high = question.scale_max if question.scale_max is not None else 5
    if not low <= value <= high:
        return PreparedAnswer(kind=ANSWER_SKIPPED)
    return PreparedAnswer(kind="scale", value_int=value)


def _prepare_text(question: Question, raw: str) -> PreparedAnswer:
    value = (raw or "").strip()[:TEXT_MAX]
    if not value:
        return PreparedAnswer(kind=ANSWER_SKIPPED)
    return PreparedAnswer(kind="text", value_text=value)


def prepare_answer(question: Question, raw_value: str) -> list[PreparedAnswer]:
    if question.type in ("single", "multiple"):
        prepared = _prepare_choice(question, raw_value)
        if question.type == "single" and len(prepared) != 1:
            return [PreparedAnswer(kind=ANSWER_SKIPPED)]
        return prepared or [PreparedAnswer(kind=ANSWER_SKIPPED)]
    if question.type == "yes_no":
        return [_prepare_yes_no(question, raw_value)]
    if question.type == "scale":
        return [_prepare_scale(question, raw_value)]
    return [_prepare_text(question, raw_value)]


def submit_response(
    session: Session,
    survey: Survey,
    form: dict[str, list[str]],
    respondent_hash: str,
    respondent_token: str | None = None,
) -> SubmissionResult:
    """Сохраняет ответ респондента одной транзакцией."""
    if not survey.is_open:
        raise SurveyClosedError("Опрос закрыт для приёма ответов")

    if answers_repo.submitted_exists_for_respondent(
        session, survey.id, respondent_token, respondent_hash
    ):
        raise AlreadyRespondedError("Ответ с этого устройства уже отправлен")

    question_rows = questions_repo.list_for_survey(session, survey.id)
    response = answers_repo.add_response(
        session,
        Response(
            survey_id=survey.id,
            submitted_at=utcnow(),
            respondent_hash=respondent_hash,
            respondent_token=respondent_token,
        ),
    )

    prepared_rows: list[Answer] = []
    saved = 0
    skipped = 0
    kinds: dict[str, int] = {}

    for question in question_rows:
        raw_value = "\n".join(form.get(f"q{question.id}", []))
        for prepared in prepare_answer(question, raw_value):
            prepared_rows.append(
                Answer(
                    response_id=response.id,
                    question_id=question.id,
                    kind=prepared.kind,
                    value_text=prepared.value_text,
                    value_int=prepared.value_int,
                )
            )
            kinds[prepared.kind] = kinds.get(prepared.kind, 0) + 1
            if prepared.kind == ANSWER_SKIPPED:
                skipped += 1
            else:
                saved += 1

    answers_repo.add_answers(session, prepared_rows)
    session.commit()
    stats_cache.invalidate(survey.slug)

    logger.info(
        "Сохранён ответ survey=%s ответов=%d пропущено=%d",
        survey.slug,
        saved,
        skipped,
    )
    return SubmissionResult(response=response, saved=saved, skipped=skipped, kinds=kinds)


def get_public_survey(session: Session, slug: str) -> Survey | None:
    return surveys_repo.get_by_slug(session, slug)
