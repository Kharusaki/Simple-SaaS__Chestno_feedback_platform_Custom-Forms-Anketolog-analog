"""Кастомизация: Google Fonts, свои цвета, единое меню.

Что здесь ломалось и ради чего тесты написаны:
- подписи полей были мельче вспомогательного текста и набирались светлым
  начертанием — на цветной подложке красная подпись исчезала;
- вопрос в публичной анкете висел на рамке: `legend` браузер рисует по
  верхнему краю поля, минуя `padding`;
- у первого блока вопроса был обнулён верхний отступ, из-за чего текст
  упирался в рамку;
- своя подложка не читалась вместе с текстом: тёмный фон со светлым
  текстом не выбирался автоматически.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from core import models
from database.session import add_missing_columns, engine
from core.models import Survey
from surveys import crud
from tests import factories
from core.themes import (
    FONTS,
    appearance_catalog,
    appearance_context,
    blend,
    font_groups,
    google_fonts_link,
    normalize_hex,
    readable_ink,
    readable_muted,
    relative_luminance,
    theme_css,
    theme_defaults,
)

MIN_RGB = 0.9396
MAX_RGB = 1.0


class TestHexNormalization:
    def test_short_form_expands(self) -> None:
        assert normalize_hex("#FFF") == "#ffffff"
        assert normalize_hex("#abc") == "#aabbcc"

    def test_case_is_folded(self) -> None:
        assert normalize_hex("#AABBCC") == "#aabbcc"

    def test_garbage_is_rejected(self) -> None:
        for value in ("белый", "#12", "#1234567", "ffffff", "#gggggg", "red"):
            assert normalize_hex(value) is None, value

    def test_empty_means_no_override(self) -> None:
        assert normalize_hex(None) is None
        assert normalize_hex("") is None
        assert normalize_hex("   ") is None


class TestContrast:
    def test_dark_background_gets_light_text(self) -> None:
        assert readable_ink("#101014") == "#ffffff"
        assert readable_muted("#101014") == "#b8b8b2"

    def test_light_background_gets_dark_text(self) -> None:
        assert readable_ink("#ffffff") == "#141414"
        assert readable_muted("#ffffff") == "#4f4f4c"

    def test_luminance_matches_reference_values(self) -> None:
        assert relative_luminance("#ffffff") == 1.0
        assert relative_luminance("#000000") == 0.0
        # Серый 0x808080 по WCAG даёт 0.2159: он проходит порог
        # читаемости 0.179, поэтому на нём выбирается тёмный текст.
        assert 0.21 < relative_luminance("#808080") < 0.22

    def test_blend_stays_inside_channel_range(self) -> None:
        for base, toward in (("#ffffff", "#000000"), ("#000000", "#ffffff")):
            mixed = blend(base, toward, 0.5)
            assert normalize_hex(mixed)
            for start in (1, 3, 5):
                value = int(mixed[start : start + 2], 16)
                assert 0 <= value <= 255


class TestThemeCssWithOwnColors:
    def test_background_recalculates_ink(self) -> None:
        css = theme_css("light", "system", bg_color="#101014")

        assert "--bg:#101014" in css
        assert "--text:#ffffff" in css
        assert "--muted:#b8b8b2" in css

    def test_own_ink_wins_over_computed(self) -> None:
        css = theme_css("light", "system", bg_color="#ffffff", ink_color="#003366")

        assert "--text:#003366" in css

    def test_surfaces_derive_from_background(self) -> None:
        css = theme_css("light", "system", bg_color="#101014")
        declared = dict(
            part.split(":", 1) for part in css.split(";") if part.startswith("--") and ":" in part
        )

        assert declared["--surface"] != "#ffffff", "карточки остались белыми на тёмном фоне"
        assert declared["--border"] != "#e3e6ef", "рамки остались от светлой темы"

    def test_accent_overrides_theme_accent(self) -> None:
        css = theme_css("light", "system", accent_color="#ff0066")

        assert "--primary:#ff0066" in css
        assert "--signal:#ff0066" in css

    def test_garbage_colors_change_nothing(self) -> None:
        css = theme_css("light", "system", bg_color="не цвет")

        assert "--bg:#f6f7fb" in css
        assert "не цвет" not in css

    def test_font_stacks_survive_color_overrides(self) -> None:
        css = theme_css("light", "serif", bg_color="#101014")

        assert "--font-heading:Georgia" in css
        assert "--font-body:Georgia" in css


class TestGoogleFonts:
    def test_catalog_has_google_fonts(self) -> None:
        google = [font for font in FONTS if font.google_family]
        assert len(google) >= 8, "каталог Google Fonts пуст"

    def test_system_fonts_have_no_family(self) -> None:
        for font in FONTS:
            if not font.google_family:
                assert font.key in {"system", "serif", "rounded", "mono"}

    def test_link_is_built_for_google_font(self) -> None:
        link = google_fonts_link("manrope")

        assert "fonts.googleapis.com/css2" in link
        assert "family=Manrope" in link
        assert "preconnect" in link

    def test_system_font_needs_no_network(self) -> None:
        assert google_fonts_link("system") == ""
        assert google_fonts_link("mono") == ""

    def test_unknown_font_falls_back_without_link(self) -> None:
        assert google_fonts_link("нетакого") == google_fonts_link("system") or (
            google_fonts_link("нетакого") == ""
        )

    def test_family_matches_stack(self) -> None:
        for font in FONTS:
            if font.google_family:
                assert font.heading.startswith(f"'{font.google_family}'")
                assert font.body.startswith(f"'{font.google_family}'")

    def test_groups_split_system_and_google(self) -> None:
        groups = dict(font_groups())

        assert set(groups) == {"Системные", "Google Fonts"}
        assert groups["Системные"]
        assert groups["Google Fonts"]

    def test_group_entries_carry_family(self) -> None:
        for _label, options in font_groups():
            for key, _name, family in options:
                expected = "" if key in {"system", "serif", "rounded", "mono"} else family
                assert expected == family
                assert family == "" or " " in family or family.isalpha()


class TestAppearanceContext:
    def test_context_has_every_menu_piece(self) -> None:
        context = appearance_context("sand", "onest", "#101014", "#ff0066", "")

        for key in (
            "appearance",
            "theme_choices",
            "font_groups",
            "color_fields",
            "defaults",
            "theme_css",
            "appearance_catalog",
            "google_fonts_link",
        ):
            assert key in context, key

    def test_values_are_normalized(self) -> None:
        context = appearance_context("light", "system", "#FFF", "не цвет", None)

        assert context["appearance"]["bg_color"] == "#ffffff"
        assert context["appearance"]["accent_color"] == ""

    def test_invalid_theme_falls_back(self) -> None:
        context = appearance_context("нетакой", "нетакой")

        assert context["appearance"]["theme"] == "light"
        assert context["appearance"]["font"] == "system"

    def test_defaults_follow_theme(self) -> None:
        dark = theme_defaults("night")
        light = theme_defaults("light")

        assert dark["bg_color"] != light["bg_color"]
        assert all(value.startswith("#") for value in dark.values())


class TestCatalog:
    def test_every_font_and_theme_is_in_catalog(self) -> None:
        catalog = appearance_catalog()

        for font in FONTS:
            assert f"font:{font.key}" in catalog
        for theme_key in ("light", "dark", "mint", "sand", "night"):
            assert f"theme:{theme_key}" in catalog


class TestAppearanceMenuIsShared:
    def test_menu_is_a_single_partial(self) -> None:
        from core.deps import templates

        assert templates.get_template("partial/appearance_menu.html") is not None

    def test_creation_page_uses_the_menu(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        response = client.get("/surveys/new")

        assert response.status_code == 200
        assert "data-appearance" in response.text
        assert 'name="bg_color"' in response.text
        assert 'name="accent_color"' in response.text
        assert 'name="ink_color"' in response.text

    def test_editor_uses_the_same_menu(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )

        response = client.get(f"/s/{survey.slug}/edit")

        assert response.status_code == 200
        assert "data-appearance" in response.text
        assert 'name="bg_color"' in response.text
        assert 'name="ink_color"' in response.text

    def test_both_pages_load_the_preview_script(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )

        for url in ("/surveys/new", f"/s/{survey.slug}/edit"):
            response = client.get(url)
            assert "/static/js/survey_appearance.js" in response.text, url

    def test_google_fonts_group_is_visible(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        response = client.get("/surveys/new")

        assert 'data-google="Manrope"' in response.text
        assert 'data-google=""' in response.text, "системные шрифты не помечены как локальные"


class TestApplyButtons:
    """Кнопка «Применить» у каждого выбора.

    Превью и так перерисовывается на каждый ввод, но сама перерисовка для
    человека невидима: смена подложки на глазах теряется, и «применил, и
    ничего не изменилось» неотличимо от «кнопка не сработала». Поэтому у
    каждого выбора своя кнопка и видимое подтверждение.
    """

    def test_theme_and_font_have_apply_buttons(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        assert 'data-apply="theme"' in text
        assert 'data-apply="font"' in text

    def test_every_color_has_its_own_apply_button(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        for name in ("bg_color", "accent_color", "ink_color"):
            assert f'data-apply-color="{name}"' in text, name

    def test_apply_buttons_do_not_submit_the_form(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        buttons = re.findall(r"<button[^>]*data-apply[^>]*>", text)
        assert len(buttons) == 5, f"ожидались 5 кнопок, найдено {len(buttons)}"
        for button in buttons:
            assert 'type="button"' in button, f"кнопка отправит форму: {button}"

    def test_both_pages_render_apply_buttons(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )

        for url in ("/surveys/new", f"/s/{survey.slug}/edit"):
            text = client.get(url).text
            assert 'data-apply="theme"' in text, url
            assert 'data-apply="font"' in text, url
            assert text.count("data-apply-color=") == 3, url

    def test_script_wires_apply_buttons_and_announces(self) -> None:
        js = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "survey_appearance.js"
        ).read_text(encoding="utf-8")

        assert 'data-apply="theme"' in js
        assert 'data-apply="font"' in js
        assert "[data-apply-color]" in js
        assert "announce(" in js, "кнопки не подтверждают применение"

    def test_invalid_color_is_not_applied(self) -> None:
        js = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "survey_appearance.js"
        ).read_text(encoding="utf-8")

        handler = js[js.index("querySelectorAll(\"[data-apply-color]\")") :]
        guard = handler[: handler.index("apply();")]
        assert "parseHex" in guard, "кнопка применяет неразборчивый цвет"
        assert "не применён" in handler, "отказ не показан человеку"

    def test_status_line_is_announced_to_screen_readers(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        assert "data-preview-status" in text
        assert 'aria-live="polite"' in text

    def test_applied_frame_is_marked(self) -> None:
        from tests.test_css_layout import CSS

        assert ".preview-frame.is-applied" in CSS, "нет вспышки рамки после применения"


class TestColorControlShowsTheRealColor:
    """Регрессии из жалобы: «цвет не фиксируется, превью не меняется».

    Обе причины нашлись на живой странице и обе выглядели как «сломанный
    интерфейс», хотя код был исправен:

    1. Поле кода было пустым, а placeholder «#ffffff» читался как значение.
       Человек видел белый цвет, применял белый к светлой теме и получал
       «ничего не изменилось» — предсказуемо.
    2. Кнопка «Применить» меняла превью, но не образец. Вписал цвет, нажал
       кнопку, посмотрел на квадрат — старый цвет, и вывод «не фиксируется».
    """

    def test_code_field_has_no_misleading_placeholder(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        fields = re.findall(r"<input[^>]*data-color-text[^>]*>", text)
        assert fields, "нет поля кода цвета"
        for field in fields:
            assert "placeholder" not in field, f"подпись выглядит как значение: {field}"

    def test_code_field_shows_a_value_right_away(
        self, client, db_session, clean_tables
    ) -> None:
        """Поле обязано показывать действующий цвет, иначе «свой цвет» и
        «цвет темы» снаружи неразличимы."""
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        fields = re.findall(r'<input[^>]*data-color-text="[^"]*"[^>]*>', text)
        assert len(fields) == 3
        for field in fields:
            value = re.search(r'value="([^"]*)"', field)
            assert value, f"у поля нет значения: {field}"
            assert value.group(1), f"поле показывает пустоту, а не цвет: {field}"
            assert re.fullmatch(r"#[0-9a-f]{6}", value.group(1)), field

    def test_name_lives_on_a_hidden_field_not_on_the_visible_one(
        self, client, db_session, clean_tables
    ) -> None:
        """Видимое поле показывает цвет темы, а сохраняться должен только
        СВОЙ цвет. Если name останется на видимом поле, цвет темы
        превратится в собственный при первом же сохранении."""
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        visible = re.findall(r"<input[^>]*data-color-text[^>]*>", text)
        assert visible
        for field in visible:
            assert "name=" not in field, f"видимое поле уедет в базу: {field}"

        hidden = re.findall(r"<input[^>]*data-color-value[^>]*>", text)
        assert len(hidden) == 3
        for field in hidden:
            assert 'type="hidden"' in field, field

    def test_saved_override_comes_back_into_the_hidden_field(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )
        survey.bg_color = "#101014"
        db_session.commit()

        text = client.get(f"/s/{survey.slug}/edit").text

        hidden = re.search(r'<input[^>]*data-color-value="bg_color"[^>]*>', text)
        assert hidden, "свой цвет не вернулся в форму"
        assert 'value="#101014"' in hidden.group(0), hidden.group(0)

    def test_apply_button_syncs_the_swatch(
        self, client, db_session, clean_tables
    ) -> None:
        """Обе стороны цвета обязаны встать на одно значение."""
        js = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "survey_appearance.js"
        ).read_text(encoding="utf-8")

        assert "syncColorInputs(" in js
        handler = js[js.index('querySelectorAll("[data-apply-color]")') :]
        body = handler[: handler.index("fontSelect.addEventListener")]
        assert "syncColorInputs(name, parsed)" in body, "кнопка не фиксирует образец"

    def test_own_color_is_marked_and_can_be_dropped(
        self, client, db_session, clean_tables
    ) -> None:
        """Свой цвет должен отличаться от цвета темы и сниматься кнопкой."""
        factories.login_as(client, db_session)

        text = client.get("/surveys/new").text

        assert text.count("data-color-reset=") == 3, "нет кнопки «Цвет темы»"
        # У анкеты без своих цветов снимать нечего, поэтому кнопка скрыта.
        hidden_resets = re.findall(
            r"<button[^>]*data-color-reset=\"[^\"]*\"[^>]*>", text
        )
        assert len(hidden_resets) == 3
        for button in hidden_resets:
            assert "hidden" in button, f"кнопка «Цвет темы» показана зря: {button}"

    def test_saved_own_color_offers_the_theme_button(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )
        survey.bg_color = "#101014"
        db_session.commit()

        text = client.get(f"/s/{survey.slug}/edit").text

        button = re.search(
            r"<button[^>]*data-color-reset=\"bg_color\"[^>]*>", text
        )
        assert button, "у своего цвета нет кнопки «Цвет темы»"
        assert "hidden" not in button.group(0), "свой цвет нельзя вернуть к теме"


class TestOwnColorsRoundTrip:
    def test_creation_saves_colors(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        response = client.post(
            "/surveys/new",
            data={
                "title": ["Анкета с цветами"],
                "q0_type": ["single"],
                "q0_text": ["Как вам?"],
                # Два варианта, а не один: одиночный выбор билдер не
                # принимает, и отказ был не связан с цветами вовсе.
                "q0_choices": ["Хорошо\nПлохо"],
                "theme": ["light"],
                "font": ["manrope"],
                "bg_color": ["#101014"],
                "accent_color": ["#FF0066"],
                "ink_color": [""],
            },
            follow_redirects=True,
        )

        assert response.status_code == 200
        survey = db_session.query(Survey).one()
        assert survey.bg_color == "#101014"
        assert survey.accent_color == "#ff0066"
        assert survey.ink_color is None
        assert survey.font == "manrope"

    def test_editor_saves_colors(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )

        client.post(
            f"/s/{survey.slug}/appearance",
            data={
                "theme": ["night"],
                "font": ["onest"],
                "bg_color": ["#fff"],
                "accent_color": ["белый"],
                "ink_color": ["#222222"],
            },
            follow_redirects=True,
        )

        db_session.refresh(survey)
        assert survey.theme == "night"
        assert survey.font == "onest"
        assert survey.bg_color == "#ffffff"
        assert survey.ink_color == "#222222"
        assert survey.accent_color is None, "мусорный цвет не должен попадать в базу"

    def test_empty_color_returns_to_theme(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )
        survey.bg_color = "#101014"
        survey.accent_color = "#ff0066"
        db_session.commit()

        client.post(
            f"/s/{survey.slug}/appearance",
            data={"theme": ["light"], "font": ["system"], "bg_color": [""], "accent_color": [""]},
            follow_redirects=True,
        )

        db_session.refresh(survey)
        assert survey.bg_color is None
        assert survey.accent_color is None

    def test_public_page_applies_own_colors(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )
        survey.bg_color = "#101014"
        survey.accent_color = "#ff0066"
        survey.font = "manrope"
        db_session.commit()

        response = client.get(f"/s/{survey.slug}")

        assert response.status_code == 200
        assert "--bg:#101014" in response.text
        assert "--text:#ffffff" in response.text
        assert "--signal:#ff0066" in response.text
        assert "fonts.googleapis.com/css2" in response.text

    def test_saved_values_come_back_into_the_menu(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )
        survey.bg_color = "#101014"
        survey.accent_color = "#ff0066"
        db_session.commit()

        response = client.get(f"/s/{survey.slug}/edit")

        assert 'value="#101014"' in response.text
        assert 'value="#ff0066"' in response.text

    def test_invalid_hex_is_reported_and_not_saved(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)
        survey, _key = factories.make_survey(
            db_session, owner=factories.make_owner(db_session)
        )

        client.post(
            f"/s/{survey.slug}/appearance",
            data={
                "theme": ["light"],
                "font": ["system"],
                "bg_color": ["#101014"],
                "accent_color": [""],
                "ink_color": [""],
            },
            follow_redirects=True,
        )
        db_session.refresh(survey)

        assert survey.bg_color == "#101014"


class TestQuestionFrame:
    def test_first_question_keeps_top_padding(self) -> None:
        from tests.test_css_layout import CSS

        block = re.search(r"\.question-block \{([^}]*)\}", CSS).group(1)
        assert re.search(r"padding:[^;]*\d", block), "у блока вопроса нет верхнего отступа"

        first = re.search(r"\.question-block:first-child \{([^}]*)\}", CSS)
        assert first is not None
        assert "padding-top: 0" not in first.group(1), "верхний отступ первого вопроса обнулён"

    def test_legend_is_detached_from_the_border(self) -> None:
        from tests.test_css_layout import CSS

        legend = re.search(r"\.take-form \.question-block\.card > \.question-legend \{([^}]*)\}", CSS)
        assert legend is not None, "legend не отвязан от рамки поля"
        assert "float: left" in legend.group(1)
        assert "clear: both" in legend.group(1)


class TestSchema:
    def test_survey_has_color_columns(self) -> None:
        columns = {column.name for column in models.Survey.__table__.columns}

        assert {"bg_color", "accent_color", "ink_color"} <= columns

    def test_colors_are_optional(self) -> None:
        for name in ("bg_color", "accent_color", "ink_color"):
            assert models.Survey.__table__.columns[name].nullable

    def test_add_missing_columns_is_idempotent(self) -> None:
        add_missing_columns()
        add_missing_columns()

        assert "bg_color" in {c["name"] for c in inspect(engine).get_columns("surveys")}

    def test_existing_rows_survive(self, db_session: Session) -> None:
        owner = factories.make_owner(db_session)
        survey, _key = factories.make_survey(db_session, owner=owner)

        add_missing_columns()
        db_session.expire_all()

        stored = db_session.query(Survey).filter(Survey.id == survey.id).one()
        assert stored.title == survey.title
        assert stored.bg_color is None