"""Наполняет базу демонстрационными анкетами и ответами.

Нужен, чтобы посмотреть аналитику, облако слов и CSV руками, не набивая
данные вручную. Ссылки печатаются в конце вместе с паролем владельца.

Запуск локально:
    python -m scripts.seed_demo
    python -m scripts.seed_demo --responses 25 --reset

Внутри контейнера (тот же том с базой, сервис называется `app`):
    docker compose exec app python -m scripts.seed_demo
    docker compose exec app python -m scripts.seed_demo --reset

Скрипт идёт через боевой код — `build_survey_payload()`, `create_survey()`,
`submit_response()`, — поэтому наполненная база не может оказаться в
состоянии, которого приложение не создаёт само.

Ответы специально неравномерны: одно слово повторяется намного чаще
остальных, иначе облако тегов получается плоским и не показывает, ради чего
сделано.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys

from auth.service import register_creator
from database.repositories import answers as answers_repo
from database.session import SessionLocal, create_all
from surveys import crud, taking
from surveys.builder import build_survey_payload
from core.utils import respondent_hash

import core.models as models  # noqa: F401  регистрация таблиц в метаданных

logger = logging.getLogger(__name__)

DEMO_LOGIN = "demo"
DEMO_PASSWORD = "demo1234"

SINGLE = "single"
MULTIPLE = "multiple"
TEXT = "text"
SCALE = "scale"
YES_NO = "yes_no"

# Слова-лидеры облака. Ключ — сколько респондентов произнесут слово, значение —
# набор коротких фраз, в которых оно единственное общее слово. Общие слова у
# фраз разные, поэтому «скорость» остаётся первым, а не делит вершину с
# собственным хвостом.
FEATURED_TEXTS: dict[str, list[str]] = {
    "скорость": [
        "скорость на высоте",
        "скорость отличная",
        "скорость очень помогает",
        "скорость приятно удивляет",
        "скорость главный плюс",
        "скорость без нареканий",
        "скорость радует",
        "скорость выручает",
    ],
    "поддержка": [
        "поддержка отвечает быстро",
        "поддержка помогла решить вопрос",
        "поддержка всегда на связи",
        "поддержка вежливая и терпеливая",
    ],
}

FEATURED_COUNTS = {"скорость": 14, "поддержка": 4}


NOISE_TEXTS = [
    "удобно и быстро, рекомендую коллегам",
    "иногда подвисает при открытии, особенно вечером",
    "отличный интерфейс, всё интуитивно",
    "хотелось бы выгрузку в excel",
    "удобно, но не хватает фильтров по дате",
    "всё отлично, никаких замечаний",
    "долго грузится статистика на больших анкетах",
    "простой и понятный сервис без лишнего",
    "планирую перевести всю команду",
    "сломалось на телефоне, пришлось перезагрузить",
    "здорово, но не хватает тёмной темы",
    "номер заказа запомнился, всё быстро",
    "нравится, что данные остаются в своих руках",
    "мелочь, но неудобно переключать анкеты",
    "работает стабильно уже полгода",
]

DEMOS = [
    {
        "title": "Демо: обратная связь после покупки",
        "description": "Демонстрационная анкета, наполненная скриптом.",
        "theme": "light",
        "questions": [
            {
                "text": "Как вы оцениваете покупку?",
                "type": SINGLE,
                "choices": ["Отлично", "Хорошо", "Плохо"],
            },
            {
                "text": "Что понравилось больше всего?",
                "type": MULTIPLE,
                "choices": ["Скорость", "Поддержка", "Удобная оплата", "Качество"],
            },
            {"text": "Оцените сервис", "type": SCALE, "scale": "1-10"},
            {"text": "Насколько вероятно, что порекомендуете нас?", "type": YES_NO},
            {"text": "Что улучшить?", "type": TEXT},
        ],
        "answers": [
            {0: 0, 1: [0, 2], 2: 9, 3: 0, 4: "скорость на высоте"},
            {0: 1, 1: [1], 2: 7, 3: 0, 4: "поддержка отвечает быстро"},
            {0: 0, 1: [0, 1, 3], 2: 10, 3: 0, 4: "скорость очень помогает"},
            {0: 0, 1: [0], 2: 8, 3: 1, 4: "скорость выручает"},
        ],
    },
    {
        "title": "Демо: типы вопросов",
        "description": "По одному вопросу каждого типа, чтобы посмотреть все графики.",
        "theme": "mint",
        "questions": [
            {"text": "Вопрос с одним выбором", "type": SINGLE, "choices": ["Вариант А", "Вариант Б"]},
            {"text": "Вопрос с несколькими выборами", "type": MULTIPLE, "choices": ["Раз", "Два", "Три"]},
            {"text": "Шкала от 1 до 5", "type": SCALE, "scale": "1-5"},
            {"text": "Да или нет", "type": YES_NO},
            {"text": "Свободный текст", "type": TEXT},
        ],
        "answers": [
            {0: 0, 1: [0, 2], 2: 4, 3: 0, 4: "открытый ответ про интерфейс"},
        ],
    },
    {
        "title": "Демо: рабочая встреча",
        "description": "Показывает заморозку структуры: после ответов вопросы не меняются.",
        "theme": "sand",
        "questions": [
            {"text": "Насколько полезна встреча?", "type": SINGLE, "choices": ["Очень", "Средне", "Не очень"]},
            {"text": "Что обсудить в следующий раз?", "type": TEXT},
        ],
        "answers": [
            {0: 0, 1: "обсудить сроки и разбить задачи"},
            {0: 1, 1: "подготовить примеры для следующей встречи"},
        ],
    },
]


def _form_for(demo: dict) -> dict[str, list[str]]:
    """Собирает форму конструктора из описания демо-анкеты.

    Имена полей повторяют те, что шлёт браузер: скрипт должен ломаться
    ровно тогда же, когда ломается настоящая форма.
    """
    form: dict[str, list[str]] = {
        "title": [demo["title"]],
        "description": [demo["description"]],
    }
    for index, question in enumerate(demo["questions"], start=1):
        form[f"q{index}_text"] = [question["text"]]
        form[f"q{index}_type"] = [question["type"]]
        form[f"q{index}_is_required"] = ["1"]
        if question.get("scale"):
            form[f"q{index}_scale"] = [question["scale"]]
        if question.get("choices"):
            form[f"q{index}_choices"] = ["\n".join(question["choices"])]
    return form


def _answer_form(demo: dict, questions, answer: dict) -> dict[str, list[str]]:
    """Ответ одного респондента в виде формы.

    Ключи в `answer` — номера вопросов в `demo["questions"]`, а значения
    берутся у тех вопросов, для которых ключ задан. Вопрос без ключа
    респондент пропустил: это проверяет, как в статистике считаются
    незаполненные вопросы.
    """
    form: dict[str, list[str]] = {}
    for index, question in enumerate(demo["questions"]):
        if index not in answer:
            continue
        question_id = questions[index].id
        kind = question["type"]
        value = answer[index]
        if kind == SINGLE:
            form[f"q{question_id}"] = [question["choices"][value]]
        elif kind == MULTIPLE:
            form[f"q{question_id}"] = [question["choices"][i] for i in value]
        elif kind == SCALE:
            form[f"q{question_id}"] = [str(value)]
        elif kind == YES_NO:
            form[f"q{question_id}"] = ["Да" if value == 0 else "Нет"]
        elif kind == TEXT:
            form[f"q{question_id}"] = [value]
    return form


def _extra_answers(demo: dict, rng: random.Random, count: int, featured: list[str]) -> list[dict]:
    """Генерирует правдоподобные ответы, чтобы облако тегов было живым.

    Первые респонденты получают фразы из `FEATURED_TEXTS`: одно слово
    повторяется двадцать с лишним раз, а хвост у каждой фразы свой, поэтому
    вершина облака остаётся однозначной. Остальные пишут разнообразный шум.

    Часть респондентов намеренно пропускает вопрос — иначе в статистике
    не видно, как считаются незаполненные вопросы.
    """
    if count <= 0:
        return []
    text_positions = [
        index for index, question in enumerate(demo["questions"]) if question["type"] == TEXT
    ]
    single_positions = [
        index for index, question in enumerate(demo["questions"]) if question["type"] == SINGLE
    ]
    multiple_positions = [
        index for index, question in enumerate(demo["questions"]) if question["type"] == MULTIPLE
    ]
    scale_positions = [
        index for index, question in enumerate(demo["questions"]) if question["type"] == SCALE
    ]
    yes_no_positions = [
        index for index, question in enumerate(demo["questions"]) if question["type"] == YES_NO
    ]
    if not text_positions:
        return []

    made: list[dict] = []
    for number in range(count):
        answer: dict = {}
        text_at = text_positions[number % len(text_positions)]
        if number < len(featured):
            answer[text_at] = featured[number]
        else:
            answer[text_at] = rng.choice(NOISE_TEXTS)

        for index in single_positions:
            answer[index] = rng.randrange(len(demo["questions"][index]["choices"]))
        for index in multiple_positions:
            choices = demo["questions"][index]["choices"]
            answer[index] = rng.sample(range(len(choices)), rng.randint(1, len(choices)))
        for index in scale_positions:
            low, high = (int(part) for part in demo["questions"][index]["scale"].split("-"))
            answer[index] = rng.randint(low, min(high, low + 5))
        for index in yes_no_positions:
            answer[index] = 0 if rng.random() < 0.7 else 1

        # Обязательные вопросы пропускать нельзя, иначе заявка не сохранится.
        required = set(single_positions) | set(multiple_positions) | set(yes_no_positions)
        optional = [i for i in list(scale_positions) + text_positions if i not in required]
        if optional and rng.random() < 0.25:
            answer.pop(rng.choice(optional))
        made.append(answer)
    return made


def _featured_pool() -> list[str]:
    """Готовые фразы, распределённые по респондентам.

    Слова идут по убыванию частоты, поэтому «скорость» достаётся первым и
    становится лидером облака, а «поддержка» — заметным вторым местом.
    Фразы одного слова чередуются по кругу: иначе все респонденты сказали
    бы одно предложение и поделили вершину облана с его собственным хвостом.
    """
    pool: list[str] = []
    for word, times in FEATURED_COUNTS.items():
        variants = FEATURED_TEXTS[word]
        pool.extend(variants[number % len(variants)] for number in range(times))
    return pool


def _wipe(session, owner: models.User) -> int:
    """Удаляет только демо-анкеты и возвращает их количество.

    Чистить приходится для повторного прогона: без этого каждый запуск
    добавлял бы ещё одну копию тех же анкет и база росла незаметно.

    Отбор намеренно узкий — по владельцу **и** по названию из `DEMOS`.
    Простое «удалить все анкеты» выглядело безобидно, пока скрипт не запускали
    на рабочей базе: вместе с демо-данными исчезли бы и ручные анкеты
    владельца, а откатить это можно только из бэкапа.
    """
    titles = {demo["title"] for demo in DEMOS}
    removed = 0
    surveys = (
        session.query(models.Survey)
        .filter(
            models.Survey.owner_id == owner.id,
            models.Survey.title.in_(titles),
        )
        .order_by(models.Survey.id)
        .all()
    )
    for survey in surveys:
        logger.info("Удаляю прежнюю демо-анкету «%s»", survey.title)
        crud.delete_survey(session, survey)
        removed += 1
    return removed


def _ensure_owner(session):
    """Учётка владельца демо-анкет. Существующая не трогается."""
    existing = (
        session.query(models.User).filter(models.User.username == DEMO_LOGIN).one_or_none()
    )
    if existing is not None:
        logger.info("Владелец %s уже есть, пароль оставлен как есть", DEMO_LOGIN)
        return existing
    user = register_creator(session, DEMO_LOGIN, DEMO_PASSWORD)
    session.commit()
    logger.info("Создан владелец %s", DEMO_LOGIN)
    return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Демоданные для «Опросов»")
    parser.add_argument(
        "--responses", type=int, default=25, help="сколько респондентов догенерировать"
    )
    parser.add_argument("--seed", type=int, default=20240501, help="зерно генератора")
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "сначала удалить прежние демо-анкеты этого владельца "
            "(анкеты и ответы, не учётки и не ручные анкеты)"
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rng = random.Random(args.seed)
    featured = _featured_pool()

    create_all()
    session = SessionLocal()
    created: list[tuple[str, str, str]] = []
    try:
        owner = _ensure_owner(session)
        if args.reset:
            removed = _wipe(session, owner)
            session.commit()
            logger.info("Удалено прежних демо-анкет: %d", removed)

        for demo in DEMOS:
            payload = build_survey_payload(_form_for(demo))
            if payload.errors:
                raise SystemExit(f"Ошибки в демо-анкете «{demo['title']}»: {payload.errors}")

            survey, primary = asyncio.run(crud.create_survey(session, owner, payload))
            crud.set_appearance(session, survey, theme=demo.get("theme"), font="system")
            questions = crud.get_questions(session, survey)

            base = demo["answers"]
            # `--responses` — это число респондентов всего, а не «сверху».
            # Ручные ответы из `base` входят в этот счёт, иначе демо всегда
            # показывало бы на четыре человека больше, чем просил запуск.
            needed = max(0, args.responses - len(base))
            generated = _extra_answers(demo, rng, needed, featured)
            for index, answer in enumerate((base + generated)[: args.responses]):
                # Отпечаток здесь синтетический, но уникальный: иначе второй
                # ответ с «того же устройства» отбросился бы как дубль.
                device = respondent_hash(f"10.0.0.{index % 250 + 1}", f"seed-{index}")
                form = _answer_form(demo, questions, answer)
                try:
                    taking.submit_response(session, survey, form, device)
                except taking.AlreadyRespondedError:
                    continue
                except taking.SurveyClosedError:
                    break
            session.expire_all()
            submitted = answers_repo.count_submitted(session, survey.id)
            crud.set_open(session, survey, True)
            crud.create_key(session, survey, "для коллеги", can_edit=False)
            created.append((survey.title, survey.slug, primary.token))
            logger.info(
                "Создана анкета %r: slug=%s ответов=%d", survey.title, survey.slug, submitted
            )
    finally:
        session.close()

    sys.stdout.flush()
    print()
    print("Демоданные готовы.")
    print(f"Вход владельца: {DEMO_LOGIN} / {DEMO_PASSWORD}")
    print()
    for title, slug, token in created:
        print(f"  {title}")
        print(f"    прохождение:  /s/{slug}")
        print(f"    статистика:   /s/{slug}/stats?key={token}")
        print(f"    редактор:     /s/{slug}/edit")
    print()
    print("Владелец демо-анкет — demo. Свои анкеты заводите под своей учёткой.")
    if not args.reset:
        print("Повторный запуск без --reset создаст ещё одну копию анкет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
