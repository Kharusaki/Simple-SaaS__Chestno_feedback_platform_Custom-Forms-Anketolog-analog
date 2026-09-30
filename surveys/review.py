"""Проверка ответов: подсчёт балла и разбор по вопросам.

Слой не знает про HTTP. На вход — вопросы анкеты и уже сохранённые ответы,
на выход — `ReviewResult`, который шаблон разворачивает как есть.

Три режима (`models.REVIEW_MODES`), и все они про анкету целиком:
- `none` — обычный опрос, респонденту не показывают ничего, кроме «спасибо»;
- `score` — только набранный балл;
- `explain` — балл плюс правильные ответы с пояснениями.

Правила подсчёта по типам:
- `single` — верен ровно один отмеченный вариант из верных;
- `multiple` — набор выбранных должен совпасть с набором верных целиком,
  лишний выбор тоже ошибка: частично верный набор балла не даёт;
- `yes_no` — совпадение с «Да»/«Нет»;
- `scale` — совпадение с отмеченным значением;
- `text` — не оценивается, в балл не входит и в разбор не выводится.

Вопрос без отмеченного правильного ответа тоже не оценивается: за него
нельзя дать ни балл, ни ошибку, иначе респондент увидел бы «неверно» вместо
«минус один вопрос не оценивался».

В режиме `score` список `questions` остаётся пустым намеренно: «только балл»
должен быть правдой по данным, а не по тому, что шаблон что-то не отрисовал.
Разбор набирается только в режиме `explain`, где он и нужен.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import (
    ANSWER_SKIPPED,
    REVIEW_EXPLAIN,
    REVIEW_NONE,
    REVIEW_SCORE,
    REVIEW_SCORED_MODES,
    Answer,
    Question,
)

REVIEW_MODE_LABELS: dict[str, str] = {
    REVIEW_NONE: "Без проверки",
    REVIEW_SCORE: "Только балл",
    REVIEW_EXPLAIN: "Балл и правильные ответы",
}

NOTHING_TO_GRADE = (
    "Отметьте правильные ответы хотя бы у одного вопроса — иначе проверять будет нечего."
)


@dataclass(frozen=True)
class QuestionReview:
    """Разбор одного вопроса для страницы результата."""

    question_id: int
    position: int
    text: str
    kind: str
    is_correct: bool
    correct_text: str
    given_text: str
    explanation: str
    is_skipped: bool

    @property
    def shows_details(self) -> bool:
        return bool(self.correct_text or self.explanation)


@dataclass
class ReviewResult:
    mode: str = REVIEW_NONE
    total: int = 0
    correct: int = 0
    questions: list[QuestionReview] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.mode in REVIEW_SCORED_MODES

    @property
    def percent(self) -> int:
        if not self.total:
            return 0
        return round(self.correct * 100 / self.total)

    @property
    def shows_answers(self) -> bool:
        return self.mode == REVIEW_EXPLAIN


def _given_text(answers: list[Answer]) -> str:
    """Что респондент реально выбрал, одной строкой."""
    parts: list[str] = []
    for answer in answers:
        if answer.kind == ANSWER_SKIPPED:
            continue
        if answer.value_text:
            parts.append(answer.value_text)
        elif answer.value_int is not None:
            parts.append(str(answer.value_int))
    return ", ".join(parts)


def _is_skipped(answers: list[Answer]) -> bool:
    return not answers or all(answer.kind == ANSWER_SKIPPED for answer in answers)


def _judge(question: Question, answers: list[Answer]) -> bool:
    """Верен ли ответ респондента."""
    given_texts = {
        answer.value_text for answer in answers if answer.kind != ANSWER_SKIPPED
    }
    given_ints = {
        answer.value_int for answer in answers if answer.value_int is not None
    }

    if question.type in ("single", "multiple"):
        correct_texts = {choice.text for choice in question.correct_choices}
        return bool(given_texts) and given_texts == correct_texts
    if question.type == "yes_no":
        return bool(given_texts) and given_texts == {question.correct_value_text}
    if question.type == "scale":
        return bool(given_ints) and question.correct_value_int in given_ints
    return False


def _is_graded(question: Question) -> bool:
    return question.is_gradable and question.has_correct_answer()


def gradable_count(questions: list[Question]) -> int:
    """Сколько вопросов вообще можно оценить."""
    return sum(1 for question in questions if _is_graded(question))


def build_review(
    questions: list[Question],
    answers_by_question: dict[int, list[Answer]],
    mode: str,
) -> ReviewResult:
    """Считает результат по сохранённым ответам респондента."""
    result = ReviewResult(mode=mode if mode in REVIEW_SCORED_MODES else REVIEW_NONE)
    if not result.is_active:
        return result

    for question in questions:
        answers = answers_by_question.get(question.id, [])
        skipped = _is_skipped(answers)
        graded = _is_graded(question)
        is_correct = _judge(question, answers) if (graded and not skipped) else False

        if graded:
            result.total += 1
            if is_correct:
                result.correct += 1

        # В режиме `score` разбор не собирается вообще: «только балл» должен
        # быть правдой по данным, а не по тому, что шаблон забыл что-то скрыть.
        if not result.shows_answers:
            continue

        result.questions.append(
            QuestionReview(
                question_id=question.id,
                position=question.position,
                text=question.text,
                kind=question.type,
                is_correct=is_correct,
                correct_text=question.correct_display(),
                given_text=_given_text(answers),
                explanation=question.explanation or "",
                is_skipped=skipped,
            )
        )

    return result


def group_by_question(answers: list[Answer]) -> dict[int, list[Answer]]:
    """Раскладывает ответы одного респондента по вопросам."""
    grouped: dict[int, list[Answer]] = {}
    for answer in answers:
        grouped.setdefault(answer.question_id, []).append(answer)
    return grouped


def build_review_for_response(
    questions: list[Question],
    answers: list[Answer],
    mode: str,
) -> ReviewResult:
    """Считает результат прямо по сохранённым ответам респондента.

    Считать на лету безопаснее, чем класть балл в подписанную ссылку или
    cookie: клиент не сможет подделать ни балл, ни разбор, потому что в
    расчёт идут строки из `answers`.
    """
    return build_review(questions, group_by_question(answers), mode)


def grade_label(result: ReviewResult) -> str:
    """Подпись результата для шаблона."""
    if not result.is_active or not result.total:
        return ""
    return f"{result.correct} из {result.total} · {result.percent}%"
