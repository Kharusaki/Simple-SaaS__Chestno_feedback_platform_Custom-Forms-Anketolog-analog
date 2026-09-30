"""Готовые темы и шрифты оформления анкеты.

Набор намеренно маленький и зашит в код, а не хранится в базе: тема — это
несколько цветов, которые обязаны совпадать между собой (текст на фоне,
рамки, мягкая подложка). Хранить такой набор в таблице значит завести
редактор цветов и всюду разваливать читаемость.

`Survey.theme` и `Survey.font` хранят только ключ из этого словаря. Ключ,
которого здесь нет, читается как дефолтный: испорченное значение в базе не
должно ронять страницу анкеты.

Модуль ничего не знает про HTTP и про базу — это словарь, который
одинаково читают роутер и шаблон.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_THEME = "light"
DEFAULT_FONT = "system"


@dataclass(frozen=True)
class Theme:
    """Набор цветов одной темы. Значения уходят в CSS-переменные."""

    key: str
    label: str
    description: str
    colors: dict[str, str]


@dataclass(frozen=True)
class Font:
    """Шрифтовая пара: заголовки и основной текст.

    `google_family` пуст у системных шрифтов. У остальных содержит точное
    имя семейства из Google Fonts: по нему собирается ссылка на веб-шрифт,
    а `stacks` нужны как запасной вариант, если Google Fonts недоступен и
    браузер остался на системных шрифтах.
    """

    key: str
    label: str
    heading: str
    body: str
    google_family: str = ""
    weights: str = "400;500;600;700"


# Тёмная тема задаёт и поверхность, и текст: иначе тёмный фон с тёмным
# текстом — это просто нечитаемая страница.
THEMES: tuple[Theme, ...] = (
    Theme(
        key="light",
        label="Светлая",
        description="Спокойный белый фон, синий акцент",
        colors={
            "bg": "#f6f7fb",
            "surface": "#ffffff",
            "border": "#e3e6ef",
            "text": "#1a1d29",
            "muted": "#6b7280",
            "primary": "#4f46e5",
            "primary-dark": "#4338ca",
            "primary-soft": "#eef2ff",
            "success": "#059669",
            "danger": "#dc2626",
            "warning": "#d97706",
        },
    ),
    Theme(
        key="dark",
        label="Тёмная",
        description="Тёмный фон, светлый текст",
        colors={
            "bg": "#12141c",
            "surface": "#1c1f2b",
            "border": "#2c3142",
            "text": "#e8eaf2",
            "muted": "#9aa1b5",
            "primary": "#818cf8",
            "primary-dark": "#a5b4fc",
            "primary-soft": "#252a3d",
            "success": "#34d399",
            "danger": "#f87171",
            "warning": "#fbbf24",
        },
    ),
    Theme(
        key="mint",
        label="Мятная",
        description="Свежий зелёный акцент",
        colors={
            "bg": "#f2fbf7",
            "surface": "#ffffff",
            "border": "#d6ece2",
            "text": "#13312a",
            "muted": "#5c7a72",
            "primary": "#059669",
            "primary-dark": "#047857",
            "primary-soft": "#e7f7f0",
            "success": "#059669",
            "danger": "#dc2626",
            "warning": "#d97706",
        },
    ),
    Theme(
        key="sand",
        label="Песочная",
        description="Тёплый фон, оранжевый акцент",
        colors={
            "bg": "#fbf7f0",
            "surface": "#ffffff",
            "border": "#ecdfcd",
            "text": "#2e2418",
            "muted": "#7d6e5c",
            "primary": "#d97706",
            "primary-dark": "#b45309",
            "primary-soft": "#fdf1de",
            "success": "#059669",
            "danger": "#dc2626",
            "warning": "#b45309",
        },
    ),
    Theme(
        key="night",
        label="Ночная",
        description="Глубокий синий, бирюзовый акцент",
        colors={
            "bg": "#0f172a",
            "surface": "#172033",
            "border": "#27334a",
            "text": "#e2e8f0",
            "muted": "#94a3b8",
            "primary": "#2dd4bf",
            "primary-dark": "#5eead4",
            "primary-soft": "#1d3b40",
            "success": "#4ade80",
            "danger": "#fb7185",
            "warning": "#fbbf24",
        },
    ),
)

# Все стеки системные: внешний шрифт тянулся бы из сети, а страница с
# анкетой не должна зависеть от интернета. Serif и моноширинный есть
# в системе у всех трёх платформ.
FONTS: tuple[Font, ...] = (
    Font(
        key="system",
        label="Системный",
        heading="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
        body="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
    ),
    Font(
        key="serif",
        label="Книжный",
        heading="Georgia, 'Times New Roman', serif",
        body="Georgia, 'Iowan Old Style', 'Times New Roman', serif",
    ),
    Font(
        key="rounded",
        label="Мягкий",
        heading="'Trebuchet MS', 'Segoe UI', sans-serif",
        body="'Trebuchet MS', 'Segoe UI', Verdana, sans-serif",
    ),
    Font(
        key="mono",
        label="Моноширинный",
        heading="'JetBrains Mono', Consolas, 'Courier New', monospace",
        body="'JetBrains Mono', Consolas, 'Courier New', monospace",
    ),
    Font(
        key="golos",
        label="Google Golos Text",
        heading="'Golos Text', sans-serif",
        body="'Golos Text', sans-serif",
        google_family="Golos Text",
    ),
    Font(
        key="onest",
        label="Google Onest",
        heading="'Onest', sans-serif",
        body="'Onest', sans-serif",
        google_family="Onest",
    ),
    Font(
        key="manrope",
        label="Google Manrope",
        heading="'Manrope', sans-serif",
        body="'Manrope', sans-serif",
        google_family="Manrope",
    ),
    Font(
        key="inter",
        label="Google Inter",
        heading="'Inter', sans-serif",
        body="'Inter', sans-serif",
        google_family="Inter",
    ),
    Font(
        key="ptserif",
        label="Google PT Serif",
        heading="'PT Serif', Georgia, serif",
        body="'PT Serif', Georgia, serif",
        google_family="PT Serif",
    ),
    Font(
        key="spectral",
        label="Google Spectral",
        heading="'Spectral', Georgia, serif",
        body="'Spectral', Georgia, serif",
        google_family="Spectral",
    ),
    Font(
        key="jetbrains",
        label="Google JetBrains Mono",
        heading="'JetBrains Mono', monospace",
        body="'JetBrains Mono', monospace",
        google_family="JetBrains Mono",
    ),
    Font(
        key="rubik",
        label="Google Rubik",
        heading="'Rubik', sans-serif",
        body="'Rubik', sans-serif",
        google_family="Rubik",
    ),
    Font(
        key="playfair",
        label="Google Playfair Display",
        heading="'Playfair Display', Georgia, serif",
        body="'Playfair Display', Georgia, serif",
        google_family="Playfair Display",
    ),
    Font(
        key="ibmplex",
        label="Google IBM Plex Sans",
        heading="'IBM Plex Sans', sans-serif",
        body="'IBM Plex Sans', sans-serif",
        google_family="IBM Plex Sans",
    ),
    Font(
        key="montserrat",
        label="Google Montserrat",
        heading="'Montserrat', sans-serif",
        body="'Montserrat', sans-serif",
        google_family="Montserrat",
    ),
)

THEMES_BY_KEY = {theme.key: theme for theme in THEMES}
FONTS_BY_KEY = {font.key: font for font in FONTS}

# Ключи для <select> в шаплонах.
THEME_CHOICES = tuple((theme.key, theme.label) for theme in THEMES)
FONT_CHOICES = tuple((font.key, font.label) for font in FONTS)


def get_theme(key: str | None) -> Theme:
    """Тема по ключу. Неизвестный или пустой ключ — светлая тема.

    Значение приходит из базы, поэтому проверка обязательна: испорченное
    значение не должно превращать страницу в белое полотно.
    """
    return THEMES_BY_KEY.get(key or "", THEMES_BY_KEY[DEFAULT_THEME])


def get_font(key: str | None) -> Font:
    return FONTS_BY_KEY.get(key or "", FONTS_BY_KEY[DEFAULT_FONT])


def is_valid_theme(key: str | None) -> bool:
    return bool(key) and key in THEMES_BY_KEY


def is_valid_font(key: str | None) -> bool:
    return bool(key) and key in FONTS_BY_KEY


def font_groups() -> tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]:
    """Шрифты, разложенные по источнику: системные и из Google Fonts.

    Заголовок группы идёт в `<optgroup>`. Google Fonts отделены от
    системных, потому что они тянутся из сети, а системные доступны всегда.
    Третьим элементом в кортеже идёт семейство из Google Fonts (пустая строка
    у системных): превью подгружает его на лету и не берёт все 11 семейств
    одной ссылкой на каждую страницу.
    """
    system = tuple((f.key, f.label, "") for f in FONTS if not f.google_family)
    google = tuple((f.key, f.label, f.google_family) for f in FONTS if f.google_family)
    return (("Системные", system), ("Google Fonts", google))


COLOR_FIELDS: tuple[dict[str, str], ...] = (
    {
        "name": "bg_color",
        "label": "Цвет подложки",
        "hint": "Фон анкеты. Текст и рамки пересчитаются под него.",
    },
    {
        "name": "accent_color",
        "label": "Акцентный цвет",
        "hint": "Кнопки, ссылки, номера вопросов.",
    },
    {
        "name": "ink_color",
        "label": "Цвет текста",
        "hint": "Пусто — подберём автоматически, чтобы текст читался.",
    },
)


def theme_defaults(theme_key: str | None) -> dict[str, str]:
    """Цвета темы, на которые подменяются пустые поля своих цветов.

    Превью показывает цвет пипетки до того, как человек что-то ввёл. Если
    показать там прозрачный чёрный, первое же нажатие выдаст подложку,
    от которой текст перестаёт читаться, а человек ещё не выбирал цвета.
    """
    theme = get_theme(theme_key)
    return {
        "bg_color": theme.colors.get("bg", ""),
        "accent_color": theme.colors.get("primary", ""),
        "ink_color": theme.colors.get("text", ""),
    }


def appearance_context(
    theme_key: str | None,
    font_key: str | None,
    bg_color: str | None = None,
    accent_color: str | None = None,
    ink_color: str | None = None,
) -> dict[str, object]:
    """Контекст для меню кастомизации.

    Собирается в одном месте, потому что то же самое требуется созданию,
    редактору и предпросмотру, а расходиться им нельзя: иначе превью
    покажет одно, а анкета получит другое.
    """
    return {
        "appearance": {
            "theme": get_theme(theme_key).key,
            "font": get_font(font_key).key,
            "bg_color": normalize_hex(bg_color) or "",
            "accent_color": normalize_hex(accent_color) or "",
            "ink_color": normalize_hex(ink_color) or "",
        },
        "theme_choices": THEME_CHOICES,
        "font_groups": font_groups(),
        "color_fields": COLOR_FIELDS,
        "defaults": theme_defaults(theme_key),
        "theme_css": theme_css(
            theme_key,
            font_key,
            bg_color=bg_color,
            ink_color=ink_color,
            accent_color=accent_color,
        ),
        "appearance_catalog": appearance_catalog(),
        "google_fonts_link": google_fonts_link(font_key),
    }


def theme_css(
    theme_key: str | None,
    font_key: str | None,
    *,
    bg_color: str | None = None,
    ink_color: str | None = None,
    accent_color: str | None = None,
) -> str:
    """Готовый блок CSS-переменных для атрибута `style`.

    Принимает ключи и цвета вместо объектов: вызывать приходится прямо из
    роутера по полям `Survey`, и разбирать их в двух местах незачем. Отдельный
    файл темы на каждый вариант означал бы лишние запросы и кэш, который
    придётся чистить при смене оформления.

    Свои цвета накладываются поверх темы. Текст и рамки пересчитываются под
    выбранную подложку, иначе светлый текст на тёмном фоне (или наоборот)
    сделал бы анкету нечитаемой. Акцент, если он задан, остаётся как есть:
    он и есть «фирменный» цвет, иначе он перестаёт отличаться от фона.
    """
    theme = get_theme(theme_key)
    font = get_font(font_key)
    declarations = [f"--{name}:{value}" for name, value in theme.colors.items()]

    background = normalize_hex(bg_color)
    if background is not None:
        ink = normalize_hex(ink_color) or readable_ink(background)
        declarations.append(f"--bg:{background}")
        declarations.append(f"--text:{ink}")
        declarations.append(f"--muted:{readable_muted(background)}")
        # Поверхность — это карточки на подложке. На тёмном фоне она тоже
        # должна быть тёмной, иначе белые блоки выглядят как чужие.
        declarations.append(f"--surface:{blend(background, ink, 0.08)}")
        declarations.append(f"--border:{blend(background, ink, 0.22)}")
        declarations.append(f"--primary-soft:{blend(background, ink, 0.14)}")

    accent = normalize_hex(accent_color)
    if accent is not None:
        declarations.append(f"--primary:{accent}")
        declarations.append(f"--primary-dark:{accent}")
        declarations.append(f"--signal:{accent}")

    declarations.append(f"--font-heading:{font.heading}")
    declarations.append(f"--font-body:{font.body}")
    return ";".join(declarations)


def blend(base_hex: str, toward_hex: str, ratio: float) -> str:
    """Смешивает два цвета: доля `ratio` второго в результате.

    Нужен, чтобы карточки и рамки на своей подложке были «родными», а не
    вырезанными из чужой темы. Работает в sRGB — для плоских заливок этого
    достаточно, а переход в линейное пространство тут был бы лишним кодом
    ради разницы, которой на экране не видно.
    """
    base = normalize_hex(base_hex) or "#ffffff"
    toward = normalize_hex(toward_hex) or "#000000"
    channels = []
    for index in (1, 3, 5):
        first = int(base[index : index + 2], 16)
        second = int(toward[index : index + 2], 16)
        channels.append(round(first + (second - first) * ratio))
    return "#" + "".join(f"{value:02x}" for value in channels)


HEX_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def normalize_hex(value: str | None) -> str | None:
    """Приводит цвет к каноническому виду `#rrggbb` либо отбрасывает его.

    Короткая запись `#fff` разворачивается в `#ffffff`: иначе в базу легли бы
    два значения одного и того же цвета, и сравнивать их пришлось бы в шаблоне.
    """
    if not value:
        return None
    text = str(value).strip()
    if not HEX_PATTERN.match(text):
        return None
    if len(text) == 4:
        text = "#" + "".join(char * 2 for char in text[1:])
    return text.lower()


def _channel_luminance(value: int) -> float:
    """Относительная яркость канала по WCAG — решает, тёмный фон или нет."""
    srgb = value / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    color = normalize_hex(hex_color)
    if color is None:
        raise ValueError(f"не цвет: {hex_color!r}")
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return (
        0.2126 * _channel_luminance(red)
        + 0.7152 * _channel_luminance(green)
        + 0.0722 * _channel_luminance(blue)
    )


def readable_ink(bg_hex: str) -> str:
    """Цвет текста, который читается на подложке `bg_hex`.

    Считается по контрасту WCAG, а не «на глаз»: тёмная подложка с тёмным
    текстом — это не «смелый выбор», а нечитаемая анкета. Возвращает тот
    из чёрного и белого, у которого выше контраст.
    """
    if relative_luminance(bg_hex) > 0.179:
        return "#141414"
    return "#ffffff"


def readable_muted(bg_hex: str) -> str:
    """Приглушённый текст на подложке: тот же полюс, но мягче.

    Отдельная функция нужна из-за порога: приглушённый серый, читаемый на
    белом, на почти белом фоне проваливается в невидимость, а на чёрном —
    наоборот, сереет слишком сильно.
    """
    return "#4f4f4c" if relative_luminance(bg_hex) > 0.179 else "#b8b8b2"


def google_fonts_link(font_key: str | None) -> str:
    """Полный `<link>` для `<head>`: preconnect, сам стиль и подключение.

    Ссылка собирается только для тех шрифтов, у которых в каталоге есть
    семейство: системные варианты живут в самой системе и тянуть их из сети
    незачем. Если сеть недоступна, `heading`/`body` внутри `Font` оставляют
    страницу на системном шрифте — внешний шрифт тут украшением, а не
    условием работы.
    """
    font = get_font(font_key)
    if not font.google_family:
        return ""
    family = font.google_family.replace(" ", "+")
    url = (
        "https://fonts.googleapis.com/css2"
        f"?family={family}:wght@{font.weights}&display=swap"
    )
    return (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{url}">'
    )


def appearance_catalog() -> dict[str, dict[str, str]]:
    """Все темы и шрифты ключ → CSS-переменные, для превью в браузере.

    Ключи верхнего уровня с префиксом `theme:` и `font:`, а не вложенный
    словарь: превью собирает из них одну строку `style` и не должно знать,
    что тема и шрифт — разные сущности. Отдельный файл на каждый вариант
    означал бы лишние запросы, которых приходилось бы касаться при каждом
    добавлении темы.
    """
    catalog: dict[str, dict[str, str]] = {}
    for theme in THEMES:
        catalog[f"theme:{theme.key}"] = {
            f"--{name}": value for name, value in theme.colors.items()
        }
    for font in FONTS:
        catalog[f"font:{font.key}"] = {
            "--font-heading": font.heading,
            "--font-body": font.body,
        }
    return catalog
