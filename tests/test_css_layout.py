"""Раскладка в CSS, которую не видно в разметке.

Главный ориентир — единая сетка слева: текст внутри карточек, вопросов и
статистики отстоит от края фрейма на один и тот же `--gutter-card`. Так уже
ломалась статистика: `.stat-question` перебивал `.card` и обнулял боковые
отступы, и заголовки вопросов ложились на рамку.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from surveys import taking
from tests import factories

ROOT = Path(__file__).resolve().parents[1]
_CSS_RAW = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", " ", _CSS_RAW, flags=re.S)
TEMPLATES = ROOT / "templates"

FRAMED_BLOCKS = (".card", ".stat-question", ".recent-answers", ".question-block")


def css_rules() -> dict[str, str]:
    """Плоские правила вида `.cls { ... }` без медиазапросов и псевдоклассов."""
    rules: dict[str, str] = {}
    for match in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS):
        for selector in match.group(1).split(","):
            name = selector.strip()
            if name.startswith(".") and ":" not in name and name.count(" ") == 0:
                rules.setdefault(name, " ".join(match.group(2).split()))
    return rules


def padding_sides(value: str) -> list[str]:
    """Горизонтальные отступы из значения `padding`, как их видит браузер."""
    parts = value.split()
    if len(parts) == 1:
        return [parts[0], parts[0]]
    if len(parts) in (2, 3):
        return [parts[1], parts[1]]
    if len(parts) == 4:
        return [parts[3], parts[1]]
    raise AssertionError(f"непонятный padding: {value!r}")


def is_zero(side: str) -> bool:
    return side in ("0", "0px")


def test_gutter_variable_exists() -> None:
    assert re.search(r"--gutter-card:\s*\d+px;", CSS), "в :root нет --gutter-card с размером"


@pytest.mark.parametrize("selector", FRAMED_BLOCKS)
def test_text_keeps_clearance_from_frame(selector: str) -> None:
    """Текст внутри фрейма не должен ложиться на границу."""
    body = css_rules().get(selector)
    assert body is not None, f"{selector} не найден в style.css"

    match = re.search(r"(?<!-)padding:\s*([^;]+)", body)
    assert match is not None, f"{selector} не задаёт padding"

    left, right = padding_sides(match.group(1))
    assert not is_zero(left) and not is_zero(right), (
        f"{selector} обнуляет боковые отступы: {match.group(1).strip()}"
    )
    assert left == right, f"{selector}: слева {left}, справа {right}"


def test_stat_row_shares_the_card_gutter() -> None:
    """Цифры в верхнем ряду и заголовки вопросов стоят на одной вертикали."""
    rules = css_rules()
    row = re.search(r"padding-left:\s*([^;]+)", rules[".stat-cards"])
    assert row is not None, "у .stat-cards нет padding-left, ряд уедет к краю"

    _, question_right = padding_sides(re.search(r"padding:\s*([^;]+)", rules[".stat-question"]).group(1))
    assert row.group(1).strip() == "var(--gutter-card)"
    assert question_right == "var(--gutter-card)", "ряд и карточки заданы разными значениями"


def test_class_beside_card_never_zeroes_padding() -> None:
    """Класс в паре с `card` не должен обнулять её боковые отступы."""
    rules = css_rules()
    offenders: list[str] = []
    checked: set[str] = set()

    for template in TEMPLATES.rglob("*.html"):
        for match in re.finditer(r'class="([^"]*)"', template.read_text(encoding="utf-8")):
            classes = match.group(1).split()
            if "card" not in classes:
                continue
            for name in classes:
                if name == "card" or name in checked:
                    continue
                checked.add(name)
                for selector in (f".{name}", f".card.{name}"):
                    body = rules.get(selector)
                    if body is None:
                        continue
                    found = re.search(r"(?<!-)padding:\s*([^;]+)", body)
                    if found is None:
                        continue
                    sides = padding_sides(found.group(1))
                    if any(is_zero(side) for side in sides):
                        offenders.append(
                            f"{template.relative_to(ROOT)}: {selector} -> padding: {found.group(1).strip()}"
                        )

    assert not offenders, "боковые отступы обнулены у блоков с карточкой:\n" + "\n".join(offenders)


def test_braces_balanced() -> None:
    assert CSS.count("{") == CSS.count("}")


def test_stylesheet_served_in_full(client) -> None:
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert response.text == _CSS_RAW


def _rem(value: str) -> float:
    """Число из кегля в rem.

    `var(--fs-micro)` разыменовывается: без этого любое правило, отданное
    через переменную, считалось бы «без числа» и проверка падала бы не по
    смыслу, а по неспособности распарсить ссылку.
    """
    match = re.search(r"var\(--([a-z0-9-]+)\)", value)
    if match:
        # `_scale` ключи без префикса `fs-`, а в `var()` он есть.
        return _scale()[match.group(1).removeprefix("fs-")]
    return float(re.search(r"[\d.]+", value).group(0))


def _scale() -> dict[str, float]:
    """Верхняя граница каждой ступени: у `clamp(a, b, c)` это `c`."""
    scale: dict[str, float] = {}
    for name, value in re.findall(r"--fs-([a-z0-9-]+):\s*([^;]+);", CSS):
        if "clamp(" in value:
            value = value[value.rindex(",") + 1 :].rstrip(")").strip()
        scale[name] = _rem(value)
    return scale


def test_type_scale_grows_from_body_to_display() -> None:
    """Иерархия держится размером, а не оттенком серого.

    Нижние ступени больше не обязаны быть строго убывающими: `--fs-micro`
    обслуживает подписи полей, и их просили увеличить на 20%, из-за чего
    кегль подписей сравнялся с `--fs-sm`. Упор делается на то, что текст
    остаётся не крупнее базового кегля, а заголовки — крупнее.
    """
    scale = _scale()

    assert scale["body"] > 1, "базовый кегль не увеличен"
    assert scale["sm"] < scale["body"] < scale["h1"]
    assert scale["xs"] <= scale["body"], "вспомогательный текст крупнее основного"
    assert scale["micro"] <= scale["body"], "подпись полей крупнее основного текста"
    assert scale["h3"] < scale["h2"] < scale["h1"]


def test_field_labels_are_larger_and_semibold() -> None:
    """Подписи полей читаемы: кегль не меньше вспомогательного текста.

    Селектор со спуском (`.field label`) в `css_rules` не попадает — там
    остаются только правила в один класс, — поэтому ищем по сырому CSS.
    """
    match = re.search(r"\.field label,\s*\.field-caption\s*\{([^}]*)\}", CSS)
    assert match is not None, "нет стиля для подписей полей"

    size = re.search(r"font-size:\s*([^;]+)", match.group(1)).group(1)
    weight = re.search(r"font-weight:\s*([^;]+)", match.group(1)).group(1).strip()

    assert _rem(size) >= _scale()["xs"], "подпись полей мельче вспомогательного текста"
    assert int(weight) >= 600, "подпись полей набрана светлым начертанием"


def test_appearance_menu_has_color_pickers() -> None:
    """Поля своих цветов размечены: без них превью нечем обновлять."""
    rules = css_rules()
    assert ".appearance-colors" in rules, "нет блока своих цветов"
    assert ".color-field" in rules, "нет строки образца и hex-поля"


def test_color_swatch_is_large_enough_to_read() -> None:
    """Образец цвета обязан быть виден: по нему судят, какой цвет выбран."""
    rules = css_rules()
    size = re.search(r"width:\s*(\d+)px", rules[".color-picker"])

    assert size, "у образца не задана ширина"
    assert int(size.group(1)) >= 48, "образец цвета слишком мелкий, чтобы разглядеть"


def test_own_color_is_marked_apart_from_theme_color() -> None:
    """Свой цвет должен отличаться от цвета темы: иначе их не различить."""
    rules = css_rules()
    assert ".color-picker.is-own" in rules, "свой цвет не помечен"


def test_logo_is_larger_than_body_text() -> None:
    rules = css_rules()
    logo = re.search(r"font-size:\s*([^;]+)", rules[".logo"]).group(1)
    mark = re.search(r"width:\s*(\d+)px", rules[".logo-mark"]).group(1)

    assert _rem(logo) > _scale()["body"], "логотип мельче основного текста"
    assert int(mark) >= 12, "красный маркер логотипа слишком мелкий"


def test_muted_text_keeps_contrast() -> None:
    """Серый не должен уходить в фон: подписи обязаны читаться."""
    def channel(value: str) -> float:
        raw = re.search(r"#([0-9a-f]{6})", CSS.split(value)[1][:20], re.I).group(1)
        return sum(int(raw[i : i + 2], 16) for i in (0, 2, 4)) / 3

    muted = channel("--muted:")
    assert muted <= 110, f"--muted слишком светлый для подписей: {muted}"


def test_stats_page_is_narrower_than_main_layout() -> None:
    """Полосы ответов не должны растягиваться на всю ширину макета."""
    rules = css_rules()
    maxw = re.search(r"--maxw:\s*(\d+)px", CSS).group(1)
    stats_max = re.search(r"--maxw-stats:\s*(\d+)px", CSS).group(1)

    assert "var(--maxw-stats)" in rules[".stats-page"]
    assert int(stats_max) < int(maxw), "страница статистики не уже основного макета"
    assert int(stats_max) <= 960


def test_type_scale_variables_are_consumed() -> None:
    """Каждая ступень шкалы должна где-то применяться."""
    for name in _scale():
        assert f"var(--fs-{name})" in CSS, f"--fs-{name} объявлена, но не используется"


def test_stats_page_keeps_frames_around_questions(client, db_session, creator, clean_tables) -> None:
    """Фрейм карточки и текст внутри него должны остаться на странице.

    Раньше блок вопроса оставался в разметке с классом `card`, но его
    отступы съедались правилом `.stat-question`, и это выглядело как вёрстка,
    соехавшая к границе.
    """
    owner = factories.login_as(client, db_session)
    survey, _key = factories.make_survey(db_session, owner)
    taking.submit_response(db_session, survey, {}, "device-layout")

    response = client.get(f"/s/{survey.slug}/stats")
    assert response.status_code == 200
    assert 'class="card stat-question"' in response.text
