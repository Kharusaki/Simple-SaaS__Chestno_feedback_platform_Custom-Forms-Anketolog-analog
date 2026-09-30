"""Готовые анкеты, с которых можно начать без пустого листа.

Пустая форма — это десять минут на придумывание вопросов, а человек обычно
заходит за анкетой уже с готовым списком. Здесь лежат три сценария, которые
закрывают большинство случаев, и каждый из них проходит через тот же
разбор формы, что и ручной ввод: шаблон не создаёт анкету в обход
`builder.py`, а лишь заполняет форму.

Тест знаний сразу включает режим «Балл и правильные ответы» и приходит с
размеченными правильными ответами — иначе автору пришлось бы заново
проставлять их у каждого вопроса, и главная ценность шаблона пропала бы.

Тексты правильных ответов хранятся тем же текстом, что и варианты ответа:
при подсчёте балла сравнение идёт по строке. Переименование варианта
разрывает связь, поэтому после правки шаблона правильные ответы нужно
отметить заново — это цена за то, что анкета остаётся обычной формой.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import NO_CHOICE, REVIEW_EXPLAIN, REVIEW_NONE, YES_CHOICE


@dataclass(frozen=True)
class Preset:
    """Один готовый сценарий анкеты."""

    key: str
    label: str
    hint: str
    title: str
    description: str
    review_mode: str = REVIEW_NONE
    questions: tuple[dict[str, object], ...] = field(default_factory=tuple)

    def as_form(self) -> dict[str, object]:
        """Данные для `_render_form`: то же, что приходит из браузера."""
        return {
            "title": self.title,
            "description": self.description,
            "review_mode": self.review_mode,
            "questions": [dict(question) for question in self.questions],
        }


def _single(
    text: str,
    choices: list[str],
    *,
    correct: list[str] | None = None,
    explanation: str = "",
    required: bool = True,
) -> dict[str, object]:
    return {
        "type": "single",
        "text": text,
        "is_required": required,
        "choices": "\n".join(choices),
        "correct_choices": list(correct or []),
        "correct_value_text": None,
        "correct_value_int": None,
        "explanation": explanation,
    }


def _multiple(
    text: str,
    choices: list[str],
    *,
    correct: list[str] | None = None,
    explanation: str = "",
    required: bool = True,
) -> dict[str, object]:
    block = _single(text, choices, correct=correct, explanation=explanation, required=required)
    block["type"] = "multiple"
    return block


def _yes_no(
    text: str,
    *,
    correct: str = "",
    explanation: str = "",
    required: bool = True,
) -> dict[str, object]:
    return {
        "type": "yes_no",
        "text": text,
        "is_required": required,
        "choices": "",
        "correct_choices": [],
        "correct_value_text": correct or None,
        "correct_value_int": None,
        "explanation": explanation,
    }


def _scale(
    text: str,
    *,
    correct: int | None = None,
    explanation: str = "",
    required: bool = True,
    scale: str = "1-10",
) -> dict[str, object]:
    return {
        "type": "scale",
        "text": text,
        "is_required": required,
        "scale": scale,
        "choices": "",
        "correct_choices": [],
        "correct_value_text": None,
        "correct_value_int": correct,
        "explanation": explanation,
    }


def _text(
    text: str,
    *,
    required: bool = True,
) -> dict[str, object]:
    return {
        "type": "text",
        "text": text,
        "is_required": required,
        "choices": "",
        "correct_choices": [],
        "correct_value_text": None,
        "correct_value_int": None,
        "explanation": "",
    }


SATISFACTION = Preset(
    key="satisfaction",
    label="Удовлетворённость",
    hint="5 вопросов: оценка, что улучшить, рекомендация",
    title="Удовлетворённость сервисом",
    description=(
        "Пять вопросов о том, как вам сервис и что стоит улучшить. "
        "Ответы помогут команде понять, где болит сильнее всего."
    ),
    questions=(
        _scale("Оцените работу сервиса от 1 до 10"),
        _multiple(
            "Что стоит улучшить в первую очередь?",
            [
                "Скорость работы",
                "Качество ответов",
                "Поддержка",
                "Цены",
                "Интерфейс",
                "Документация",
            ],
        ),
        _yes_no("Порекомендуете ли сервис коллегам?"),
        _single(
            "Вернётесь ли вы к нам снова?",
            ["Да", "Скорее да", "Не уверен(а)", "Нет"],
        ),
        _text("Что вам особенно понравилось?", required=False),
    ),
)

AFTER_PURCHASE = Preset(
    key="after_purchase",
    label="После покупки",
    hint="5 вопросов: оценка покупки, что использовали, проблемы",
    title="Анкета после покупки",
    description=(
        "Пять вопросов сразу после покупки: довольны ли результатом, что использовали, "
        "не возникло ли проблем. Отвечают за пару минут."
    ),
    questions=(
        _single(
            "Насколько вы довольны покупкой?",
            ["Полностью доволен", "Скорее доволен", "Скорее не доволен", "Не доволен"],
        ),
        _multiple(
            "Что из этого вы уже использовали?",
            ["Основную функцию", "Мобильное приложение", "Интеграции", "Отчёты", "Пока ничего"],
        ),
        _yes_no("Возникли ли проблемы при настройке?"),
        _single(
            "Как вы оцениваете поддержку?",
            ["Отлично", "Хорошо", "Так себе", "Не обращался"],
        ),
        _text("Что улучшить в продукте?", required=False),
    ),
)

KNOWLEDGE_TEST = Preset(
    key="knowledge",
    label="Тест знаний",
    hint="5 вопросов с проверкой: респондент видит свой балл",
    title="Тест знаний",
    description=(
        "Пять вопросов с готовыми правильными ответами. После отправки участник "
        "увидит свой балл и разбор каждого вопроса."
    ),
    review_mode=REVIEW_EXPLAIN,
    questions=(
        _single(
            "Какая страна имеет столицу Париж?",
            ["Франция", "Бельгия", "Швейцария", "Канада"],
            correct=["Франция"],
            explanation="Париж — столица Франции, крупнейший город страны.",
        ),
        _scale(
            "Сколько будет 2 + 2?",
            correct=4,
            explanation="Сложение двух двоек даёт четыре.",
        ),
        _multiple(
            "Выберите все чётные числа",
            ["2", "4", "6", "7", "9"],
            correct=["2", "4", "6"],
            explanation="Чётным называют число, которое делится на два без остатка.",
        ),
        _yes_no(
            "Земля обращается вокруг Солнца?",
            correct=YES_CHOICE,
            explanation=(
                f"Да, именно так. Правильный вариант здесь «{YES_CHOICE}», "
                f"а «{NO_CHOICE}» означал бы обратный порядок."
            ),
        ),
        _single(
            "Что делает HTTP-клиент?",
            ["Отправляет запросы серверу", "Хранит базу данных", "Рисует интерфейс"],
            correct=["Отправляет запросы серверу"],
            explanation="Клиент обращается к серверу и получает ответ: это основа работы API.",
        ),
    ),
)

PRESETS: tuple[Preset, ...] = (SATISFACTION, AFTER_PURCHASE, KNOWLEDGE_TEST)

PRESETS_BY_KEY: dict[str, Preset] = {preset.key: preset for preset in PRESETS}


def get_preset(key: str) -> Preset | None:
    """Шаблон по ключу или `None`, если ключ неизвестен."""
    return PRESETS_BY_KEY.get(key)
