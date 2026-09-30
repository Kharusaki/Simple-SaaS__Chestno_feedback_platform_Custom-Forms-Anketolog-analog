"""Оформление выбирается вместе с анкетой, а не после неё.

Три вещи, которые ломались по отдельности:
- у формы обложки не было кнопки отправки, пока обложки ещё нет: файл
  выбирался и молча терялся, а меню выглядело рабочим;
- тема и шрифт жили только в редакторе, поэтому приходилось создавать
  анкету и сразу возвращаться за оформлением;
- ключи из формы шли в базу без проверки, и испорченное значение сделало бы
  страницу нечитаемой.
"""

from __future__ import annotations

import re

import pytest

from core.models import Survey
from surveys import crud
from tests import factories
from core.themes import (
    DEFAULT_FONT,
    DEFAULT_THEME,
    FONT_CHOICES,
    THEMES,
    THEME_CHOICES,
    appearance_catalog,
    is_valid_font,
    is_valid_theme,
    theme_css,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _selected_option(html: str, value: str) -> bool:
    """Отмечен ли `<option>` с таким значением.

    Проверяется признак `selected` внутри тега, а не подстрока вида
    `<option value="x" selected>`: разметка `<option>` менялась уже дважды
    (появились подписи и `data-google`), и точные строки в тестах ломались
    на каждой такой правке вместо того, чтобы проверять смысл.
    """
    for tag in re.findall(r"<option\b[^>]*>", html):
        if f'value="{value}"' in tag and "selected" in tag:
            return True
    return False


def create_form(**overrides) -> dict:
    payload = {
        "title": ["Анкета после покупки"],
        "q0_type": ["single"],
        "q0_text": ["Как вам сервис?"],
        "q0_choices": ["Отлично\nПлохо"],
    }
    payload.update(overrides)
    return payload


class TestAppearanceAtCreation:
    def test_form_offers_theme_and_font(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        response = client.get("/surveys/new")

        assert response.status_code == 200
        assert 'name="theme"' in response.text
        assert 'name="font"' in response.text
        for value, _label in THEME_CHOICES:
            assert f'value="{value}"' in response.text
        for value, _label in FONT_CHOICES:
            assert f'value="{value}"' in response.text

    def test_form_has_live_preview(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        response = client.get("/surveys/new")

        assert "data-preview" in response.text
        assert "data-appearance-catalog" in response.text
        assert "/static/js/survey_appearance.js" in response.text

    def test_preview_catalog_covers_every_choice(self) -> None:
        catalog = appearance_catalog()

        for value, _label in THEME_CHOICES:
            assert f"theme:{value}" in catalog
        for value, _label in FONT_CHOICES:
            assert f"font:{value}" in catalog

    def test_theme_is_saved_on_create(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)

        response = client.post(
            "/surveys/new",
            data=create_form(theme=["night"], font=["serif"]),
            follow_redirects=False,
        )

        assert response.status_code == 303
        survey = db_session.query(Survey).one()
        assert survey.theme == "night"
        assert survey.font == "serif"

    def test_unknown_keys_do_not_reach_the_database(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)

        response = client.post(
            "/surveys/new",
            data=create_form(theme=["нет-такой"], font=["нет-такого"]),
            follow_redirects=False,
        )

        assert response.status_code == 303
        survey = db_session.query(Survey).one()
        assert survey.theme == DEFAULT_THEME
        assert survey.font == DEFAULT_FONT

    def test_chosen_appearance_survives_a_validation_error(
        self, client, db_session, clean_tables
    ) -> None:
        factories.login_as(client, db_session)

        response = client.post(
            "/surveys/new",
            data={"title": ["Без вопросов"], "theme": ["night"], "font": ["mono"]},
            follow_redirects=False,
        )

        assert response.status_code == 400
        # Раскладка `<option>` теперь содержит `data-google`, поэтому отбор
        # проверяется по признаку выбранного ключа, а не по точному тексту:
        # проверка на строку ломалась на каждой правке разметки.
        assert _selected_option(response.text, "night")
        assert _selected_option(response.text, "mono")

    def test_appearance_matches_the_public_page(self, client, db_session, clean_tables) -> None:
        factories.login_as(client, db_session)
        client.post(
            "/surveys/new",
            data=create_form(theme=["night"], font=["serif"]),
            follow_redirects=False,
        )
        survey = db_session.query(Survey).one()

        response = client.get(f"/s/{survey.slug}")

        assert response.status_code == 200
        # Значение попадает в атрибут style, поэтому Jinja экранирует кавычки
        # внутри шрифта: сравниваем по переменным, а не по строке целиком.
        assert "--font-body:Georgia" in response.text
        assert "themed" in response.text


class TestCoverUpload:
    def test_cover_form_has_submit_button_when_there_is_no_cover(
        self, client, db_session, clean_tables
    ) -> None:
        """Раньше кнопки не было вовсе: файл выбирался и никуда не уходил."""
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)
        assert survey.cover_image is None

        response = client.get(f"/s/{survey.slug}/edit")

        assert response.status_code == 200
        assert 'name="cover"' in response.text
        assert 'type="submit"' in response.text
        assert "Загрузить обложку" in response.text

    def test_first_cover_is_uploaded(self, client, db_session, clean_tables) -> None:
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)

        response = client.post(
            f"/s/{survey.slug}/cover",
            files={"cover": ("cover.png", PNG, "image/png")},
            follow_redirects=False,
        )

        assert response.status_code in (200, 303)
        db_session.refresh(survey)
        assert survey.cover_image
        assert client.get(f"/s/{survey.slug}").text.count(survey.cover_image) >= 1

    def test_cover_can_be_replaced(self, client, db_session, clean_tables) -> None:
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)
        client.post(
            f"/s/{survey.slug}/cover",
            files={"cover": ("first.png", PNG, "image/png")},
            follow_redirects=False,
        )
        db_session.refresh(survey)
        first = survey.cover_image

        client.post(
            f"/s/{survey.slug}/cover",
            files={"cover": ("second.png", PNG + b"\x01", "image/png")},
            follow_redirects=False,
        )

        db_session.refresh(survey)
        assert survey.cover_image != first

    def test_cover_can_be_removed(self, client, db_session, clean_tables) -> None:
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)
        client.post(
            f"/s/{survey.slug}/cover",
            files={"cover": ("cover.png", PNG, "image/png")},
            follow_redirects=False,
        )

        client.post(f"/s/{survey.slug}/cover", data={"clear": "1"}, follow_redirects=False)

        db_session.refresh(survey)
        assert survey.cover_image is None


class TestAppearanceCatalog:
    def test_every_theme_exposes_the_themed_variables(self) -> None:
        """Блок `.themed` перекрашивается только этими именами."""
        needed = {"bg", "surface", "border", "text", "muted", "primary"}

        for theme in THEMES:
            missing = needed - set(theme.colors)
            assert not missing, f"в теме {theme.key} не хватает {missing}"

    def test_catalog_entry_matches_rendered_css(self) -> None:
        catalog = appearance_catalog()
        theme_key, _ = THEME_CHOICES[0]
        font_key, _ = FONT_CHOICES[0]

        rendered = theme_css(theme_key, font_key)
        combined = {**catalog[f"theme:{theme_key}"], **catalog[f"font:{font_key}"]}

        for name, value in combined.items():
            assert f"{name}:{value}" in rendered
        assert len(combined) == len(theme_css(theme_key, font_key).split(";"))

    def test_unknown_key_is_rejected(self) -> None:
        assert not is_valid_theme("нет-такой")
        assert not is_valid_font("")
        assert not is_valid_font(None)

    def test_theme_css_falls_back_on_garbage(self) -> None:
        assert theme_css("нет-такой", "нет-такого") == theme_css(DEFAULT_THEME, DEFAULT_FONT)

    def test_catalog_is_json_serialisable(self) -> None:
        import json

        assert json.loads(json.dumps(appearance_catalog()))


class TestSurveyImageAfterCreation:
    def test_image_can_be_added_to_an_existing_survey(
        self, client, db_session, clean_tables
    ) -> None:
        """Картинка вопроса добавляется правкой, а не только при создании."""
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)
        question = factories.questions_of(db_session, survey)[0]
        assert question.image is None

        response = client.post(
            f"/s/{survey.slug}/edit",
            data={
                "title": survey.title,
                "q0_type": question.type,
                "q0_text": question.text,
                "q0_choices": "\n".join(factories.choices_of(db_session, question)),
            },
            files={"q0_image": ("shot.png", PNG, "image/png")},
            follow_redirects=False,
        )

        assert response.status_code == 303
        db_session.refresh(question)
        assert question.image
        # Картинка должна быть видна в редакторе и отдаваться по медиа-адресу.
        editor = client.get(f"/s/{survey.slug}/edit")
        assert f"/media/{question.image}" in editor.text
        assert client.get(f"/media/{question.image}").status_code == 200

    def test_existing_image_survives_an_unrelated_edit(
        self, client, db_session, clean_tables
    ) -> None:
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)
        question = factories.questions_of(db_session, survey)[0]
        choices = factories.choices_of(db_session, question)

        client.post(
            f"/s/{survey.slug}/edit",
            data={
                "title": "Другое название",
                "q0_type": question.type,
                "q0_text": question.text,
                "q0_choices": "\n".join(choices),
            },
            files={"q0_image": ("shot.png", PNG, "image/png")},
            follow_redirects=False,
        )
        db_session.refresh(question)
        stored = question.image
        assert stored

        client.post(
            f"/s/{survey.slug}/edit",
            data={
                "title": "Другое название",
                "q0_type": question.type,
                "q0_text": question.text,
                "q0_choices": "\n".join(choices),
                "q0_image_keep": stored,
            },
            follow_redirects=False,
        )

        db_session.refresh(question)
        assert question.image == stored

    def test_image_can_be_removed_with_the_checkbox(
        self, client, db_session, clean_tables
    ) -> None:
        owner = factories.login_as(client, db_session)
        survey, _key = factories.make_survey(db_session, owner)
        question = factories.questions_of(db_session, survey)[0]
        choices = factories.choices_of(db_session, question)

        client.post(
            f"/s/{survey.slug}/edit",
            data={
                "title": survey.title,
                "q0_type": question.type,
                "q0_text": question.text,
                "q0_choices": "\n".join(choices),
            },
            files={"q0_image": ("shot.png", PNG, "image/png")},
            follow_redirects=False,
        )
        db_session.refresh(question)
        assert question.image

        client.post(
            f"/s/{survey.slug}/edit",
            data={
                "title": survey.title,
                "q0_type": question.type,
                "q0_text": question.text,
                "q0_choices": "\n".join(choices),
                "q0_image_keep": question.image,
                "q0_image_clear": "1",
            },
            follow_redirects=False,
        )

        db_session.refresh(question)
        assert question.image is None


class TestCreateSurveyRejectsGarbage:
    def test_crud_ignores_unknown_keys(self, db_session, clean_tables) -> None:
        import anyio

        owner = factories.unique_owner(db_session, "appearance")
        payload = factories.make_payload()

        survey, _key = anyio.run(
            lambda: crud.create_survey(db_session, owner, payload, theme="нет", font="нет")
        )

        assert survey.theme == DEFAULT_THEME
        assert survey.font == DEFAULT_FONT

    def test_known_keys_are_kept(self, db_session, clean_tables) -> None:
        import anyio

        owner = factories.unique_owner(db_session, "appearance2")
        payload = factories.make_payload()
        theme_key, _ = THEME_CHOICES[1]
        font_key, _ = FONT_CHOICES[1]

        survey, _key = anyio.run(
            lambda: crud.create_survey(
                db_session, owner, payload, theme=theme_key, font=font_key
            )
        )

        assert survey.theme == theme_key
        assert survey.font == font_key


def test_theme_choices_have_unique_labels() -> None:
    labels = [label for _key, label in THEME_CHOICES]
    assert len(labels) == len(set(labels))
    assert all(re.search(r"[А-Яа-я]", label) for label in labels)


@pytest.mark.parametrize("field", ["theme", "font"])
def test_appearance_fields_are_in_the_create_form(client, db_session, clean_tables, field) -> None:
    factories.login_as(client, db_session)
    response = client.get("/surveys/new")
    assert f'name="{field}"' in response.text
