"""Нормализация данных конструктора вопросов.

На входе — сырая форма (`q_N_*`), на выходе — список словарей, готовых
превратиться в модели. Пустые и некорректные вопросы отбрасываются,
ошибки собираются по полям, чтобы форма могла их показать.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import QUESTION_TYPES, REVIEW_MODES, REVIEW_NONE, YES_NO_CHOICES

TITLE_MAX = 200
DESCRIPTION_MAX = 2000
QUESTION_MAX = 500
CHOICE_MAX = 300
CHOICE_MIN = 2
EXPLANATION_MAX = 1000
SCALE_PRESETS = (5, 10)
MAX_QUESTIONS = 30
MAX_CHOICES = 20

FIELD_ERRORS: dict[str, str] = {
    "title": "Введите название анкеты — не длиннее 200 символов.",
    "questions": "Добавьте хотя бы один вопрос.",
    "too_many_questions": f"Максимум {MAX_QUESTIONS} вопросов в одной анкете.",
    "empty_question": "Один из вопросов пустой — заполните его или удалите.",
    "bad_type": "Непонятный тип вопроса.",
    "few_choices": f"Нужно минимум {CHOICE_MIN} вариантов ответа.",
    "no_choices": "Добавьте хотя бы один вариант ответа.",
    "empty_choice": "Один из вариантов ответа пустой — заполните его или удалите.",
    "too_many_choices": f"Максимум {MAX_CHOICES} вариантов ответа.",
    "duplicate_choice": "Варианты ответа повторяются — уберите дубли.",
    "bad_scale": "Шкала должна иметь вид «от N до M», где 2 ≤ N < M ≤ 10.",
}


@dataclass
class BuiltQuestion:
    text: str
    type: str
    is_required: bool
    position: int
    choices: list[str] = field(default_factory=list)
    scale_min: int | None = None
    scale_max: int | None = None
    # Проверка ответов. `correct_choices` — тексты верных вариантов, а не
    # индексы: приходят они из формы текстом, и сверять их с `choices`
    # придётся уже в crud, где есть готовые модели.
    correct_choices: list[str] = field(default_factory=list)
    correct_value_text: str | None = None
    correct_value_int: int | None = None
    explanation: str = ""


@dataclass
class BuildResult:
    title: str = ""
    description: str = ""
    review_mode: str = REVIEW_NONE
    questions: list[BuiltQuestion] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    raw_blocks: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def parse_scale(value: str | None) -> tuple[int | None, int | None]:
    """'от 1 до 10' / '1-10' / '5' → (min, max). Мусор → (None, None)."""
    if not value:
        return None, None
    normalized = value.lower().replace("от", " ").replace("до", " ").replace("-", " ")
    parts = [chunk for chunk in normalized.split() if chunk.isdigit()]
    if len(parts) == 1 and 2 <= int(parts[0]) <= 10:
        return 1, int(parts[0])
    if len(parts) == 2:
        low, high = int(parts[0]), int(parts[1])
        if 1 <= low < high <= 10:
            return low, high
    return None, None


def normalize_title(raw: str | None) -> str:
    return " ".join((raw or "").split())[:TITLE_MAX]


def normalize_description(raw: str | None) -> str:
    return (raw or "").strip()[:DESCRIPTION_MAX]


def _clean_choices(raw_choices: list[str]) -> tuple[list[str], str | None]:
    cleaned: list[str] = []
    seen: set[str] = set()
    has_empty = False
    has_duplicate = False
    for raw in raw_choices:
        text = " ".join((raw or "").split())[:CHOICE_MAX]
        if not text:
            has_empty = True
            continue
        key = text.casefold()
        if key in seen:
            has_duplicate = True
            continue
        seen.add(key)
        cleaned.append(text)
    if has_empty:
        return cleaned, FIELD_ERRORS["empty_choice"]
    if has_duplicate:
        return cleaned, FIELD_ERRORS["duplicate_choice"]
    if len(cleaned) < CHOICE_MIN:
        return cleaned, FIELD_ERRORS["few_choices"]
    if len(cleaned) > CHOICE_MAX:
        return cleaned, FIELD_ERRORS["too_many_choices"]
    return cleaned, None


def _normalize_review_mode(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return value if value in REVIEW_MODES else REVIEW_NONE


def _correct_choice_texts(raw: str | None, choices: list[str]) -> list[str]:
    """Верные варианты из текста поля `qN_correct`.

    Тексты сверяются с реальными вариантами вопроса: иначе опечатка в
    редакторе тихо убрала бы вопрос из зачёта, и респондент получил бы
    «неверно» на вопрос, у которого правильного ответа просто нет.
    """
    allowed = set(choices)
    picked = {" ".join(part.split()) for part in (raw or "").split("\n") if part.strip()}
    return [choice for choice in choices if choice in picked and choice in allowed]


def _build_one(position: int, raw: dict[str, str], result: BuildResult) -> BuiltQuestion | None:
    text = " ".join((raw.get("text") or "").split())[:QUESTION_MAX]
    qtype = (raw.get("type") or "").strip().lower()

    if not text:
        result.errors["q%d" % position] = FIELD_ERRORS["empty_question"]
        return None

    if qtype not in QUESTION_TYPES:
        result.errors["q%d" % position] = FIELD_ERRORS["bad_type"]
        return None

    is_required = bool(raw.get("is_required"))
    choices: list[str] = []
    scale_min: int | None = None
    scale_max: int | None = None
    correct_choices: list[str] = []
    correct_value_text: str | None = None
    correct_value_int: int | None = None

    if qtype in ("single", "multiple"):
        choices, error = _clean_choices((raw.get("choices") or "").split("\n"))
        if error:
            result.errors["q%d" % position] = error
            return None
        correct_choices = _correct_choice_texts(raw.get("correct"), choices)

    if qtype == "yes_no":
        candidate = (raw.get("correct_yes_no") or "").strip()
        if candidate in YES_NO_CHOICES:
            correct_value_text = candidate

    if qtype == "scale":
        scale_min, scale_max = parse_scale(raw.get("scale"))
        if scale_min is None or scale_max is None:
            result.errors["q%d" % position] = FIELD_ERRORS["bad_scale"]
            return None
        candidate = (raw.get("correct_scale") or "").strip()
        if candidate.isdigit() and scale_min <= int(candidate) <= scale_max:
            correct_value_int = int(candidate)

    return BuiltQuestion(
        text=text,
        type=qtype,
        is_required=is_required,
        position=len(result.questions),
        choices=choices,
        scale_min=scale_min,
        scale_max=scale_max,
        correct_choices=correct_choices,
        correct_value_text=correct_value_text,
        correct_value_int=correct_value_int,
        explanation=(raw.get("explanation") or "").strip()[:EXPLANATION_MAX],
    )


def build_survey_payload(form: dict[str, list[str]]) -> BuildResult:
    """Собирает и проверяет опрос из распарсенной HTML-формы.

    Ожидаемые поля: `title`, `description`, `review_mode`, и на каждый
    вопрос блок `q1_text`, `q1_type`, `q1_is_required`, `q1_choices`,
    `q1_scale`. Проверка ответов приходит как `q1_correct` (тексты верных
    вариантов через перевод строки), `q1_correct_yes_no`,
    `q1_correct_scale` и `q1_explanation`.
    Номер блока выводится из имени поля, порядок сохраняется по нему же.
    """
    result = BuildResult()

    result.title = normalize_title(_first(form, "title"))
    result.description = normalize_description(_first(form, "description"))
    result.review_mode = _normalize_review_mode(_first(form, "review_mode"))

    if not result.title:
        result.errors["title"] = FIELD_ERRORS["title"]

    raw_blocks = _collect_blocks(form)
    if not raw_blocks:
        result.errors["questions"] = FIELD_ERRORS["questions"]
        return result
    if len(raw_blocks) > MAX_QUESTIONS:
        result.errors["questions"] = FIELD_ERRORS["too_many_questions"]
        return result

    for position, (_index, raw) in enumerate(raw_blocks):
        built = _build_one(position, raw, result)
        if built is not None:
            result.questions.append(built)
        result.raw_blocks.append(raw)

    if not result.questions and "questions" not in result.errors:
        result.errors["questions"] = FIELD_ERRORS["questions"]

    return result


def _first(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name) or []
    return values[0] if values else ""


def _collect_blocks(form: dict[str, list[str]]) -> list[tuple[int, dict[str, str]]]:
    """Группирует поля вида `q<N>_<поле>` по N, сортируя по номеру.

    `q<N>_choices` и `q<N>_correct` приходят одним значением с переводами
    строк внутри, остальные — по одному значению на поле.
    """
    blocks: dict[int, dict[str, str]] = {}
    for name, values in form.items():
        if not name.startswith("q") or "_" not in name:
            continue
        index_part, _, field_name = name.partition("_")
        if not index_part[1:].isdigit():
            continue
        index = int(index_part[1:])
        block = blocks.setdefault(index, {"choices": "", "correct": ""})
        if field_name in ("choices", "correct"):
            block[field_name] = "\n".join(values)
        elif field_name == "is_required":
            block["is_required"] = "1" if values and values[0] else ""
        else:
            block[field_name] = values[0] if values else ""
    return [(index, blocks[index]) for index in sorted(blocks)]
