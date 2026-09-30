"""Проверки конструктора анкеты.

Отдельный файл, потому что почти всё здесь ловится только по HTML и JS:
серверные тесты формы проходят, даже если атрибуты `name` в `<template>`
потеряны — тогда вопрос молча исчезает при сохранении.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from tests import factories

FORM_PATH = "/surveys/new"
TEMPLATE_ID = "question-template"
NAME_PATTERN = re.compile(r"^q\d+_[a-z_]+$")
REQUIRED_FIELDS = ("type", "text", "choices", "scale", "is_required", "correct", "explanation")


class FieldCollector(HTMLParser):
    """Собирает поля формы внутри элемента с заданным id.

    Отдаёт кортежи (tag, class, name, disabled). Поле `disabled` в форму не
    уходит — это визуальный маркер ответа внутри фрейма, ему `name` не нужен.
    """

    def __init__(self, target_id: str):
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.depth = 0
        self.found = False
        self.fields: list[tuple[str, str, str, bool]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if not self.found and attributes.get("id") == self.target_id:
            self.found = True
            self.depth = 1
            return
        if self.found:
            if tag in ("template", "div", "article", "label", "section"):
                self.depth += 1
            if tag in ("input", "select", "textarea"):
                self.fields.append(
                    (
                        tag,
                        attributes.get("class", ""),
                        attributes.get("name", ""),
                        "disabled" in attributes,
                    )
                )

    def handle_endtag(self, tag):
        if not self.found:
            return
        if tag in ("template", "div", "article", "label", "section"):
            self.depth -= 1
            if self.depth <= 0:
                self.found = False


def submitted_fields(html: str, target_id: str = TEMPLATE_ID) -> list[tuple[str, str, str]]:
    """Поля, которые реально уедут на сервер: не disabled и с name."""
    result = []
    for tag, class_name, name, disabled in collect(html, target_id).fields:
        if disabled or not name:
            continue
        result.append((tag, class_name, name))
    return result


@pytest.fixture
def form_html(client, clean_tables) -> str:
    return client.get(FORM_PATH).text


def collect(html: str, target_id: str = TEMPLATE_ID) -> FieldCollector:
    parser = FieldCollector(target_id)
    parser.feed(html)
    return parser


class TestTemplateFields:
    def test_template_is_present(self, form_html):
        assert TEMPLATE_ID in form_html

    def test_every_template_field_has_a_name(self, form_html):
        """Главная защита от тихой потери вопроса.

        Скрипт renumber() переписывает индекс только у полей, у которых `name`
        уже есть. Поле без `name` не отправится, и builder.py отбросит
        вопрос как пустой — анкета сохранится без него.

        Два вида полей имена не требуют: маркеры ответа внутри фрейма
        (`disabled`, в форму не уходят) и поля ввода варианта `choice-text`,
        которые скрипт собирает в именованные скрытые поля `choices-value`
        и `correct-value`. Наличие обоих проверяется отдельными тестами.
        """
        ignored = ("choice-text", "choice-correct-check")
        enabled = [
            (tag, class_name, name)
            for tag, class_name, name, disabled in collect(form_html).fields
            if not disabled
            and not any(token in class_name.split() for token in ignored)
        ]

        assert enabled, "в <template> не найдено ни одного отправляемого поля"
        unnamed = [item for item in enabled if not item[2]]
        assert not unnamed, f"поля без name: {unnamed}"

    def test_template_names_have_numbered_prefix(self, form_html):
        for _tag, _class_name, name in submitted_fields(form_html):
            assert NAME_PATTERN.match(name), f"имя {name!r} не вида q<N>_поле"

    def test_template_contains_all_needed_fields(self, form_html):
        names = {name.split("_", 1)[1] for _tag, _class_name, name in submitted_fields(form_html)}

        missing = [field for field in REQUIRED_FIELDS if field not in names]
        assert not missing, f"в шаблоне нет полей: {missing}"

    def test_initial_block_is_renamed_to_index_zero(self, form_html):
        """Первый блок рисует сервер, он тоже обязан быть индексирован."""
        names = [name for _tag, _class_name, name in submitted_fields(form_html, "questions-list")]

        assert names, "в первом блоке нет отправляемых полей с name"
        assert all(name.startswith("q0_") for name in names), names

    def test_required_checkbox_is_checked_by_default(self, form_html):
        html = re.search(r'<template id="question-template">(.*?)</template>', form_html, re.S)

        assert "question-required" in html.group(1)
        assert "checked" in html.group(1)

    def test_template_has_hidden_collectors(self, form_html):
        """Варианты и правильные ответы едут скрытыми полями, а не отдельными.

        Без них галочки «верно» просто не отправились бы: у них нет имени.
        """
        names = {name for _tag, _class_name, name in submitted_fields(form_html)}

        assert "q0_choices" in names
        assert "q0_correct" in names

    def test_review_select_offers_all_modes(self, form_html):
        values = re.findall(r'<option value="(\w+)"', form_html)

        assert {"none", "score", "explain"} <= set(values)

    def test_review_only_fields_are_marked(self, form_html):
        """Поля проверки помечены, иначе applyReview их не спрячет."""
        html = re.search(r'<template id="question-template">(.*?)</template>', form_html, re.S).group(1)

        assert "js-review-only" in html
        assert 'data-review-select' in form_html

class TestBuilderScript:
    def test_renumber_targets_named_fields_only(self, client, clean_tables):
        """Скрипт чинит индекс только у полей с name — это должно быть видно."""
        script = (client.get("/static/js/survey_builder.js")).text

        assert 'querySelectorAll("[name]")' in script
        assert "q\\d+_" in script or "q\\d+" in script

    def test_script_is_served(self, client, clean_tables):
        assert client.get("/static/js/survey_builder.js").status_code == 200


class TestEditorMarkup:
    def test_editor_explains_multiple_questions(self, form_html):
        """Перед вопросами должно быть сказано, что их может быть много."""
        assert "как один вопрос, так и несколько" in form_html

    def test_editor_lists_all_question_types(self, form_html):
        note = re.search(r'<p class="editor-note">(.*?)</p>', form_html, re.S)

        assert note, "нет пояснения перед списком вопросов"
        text = note.group(1)
        for expected in ("один вариант", "несколько вариантов", "«да/нет»", "шкала", "свободный текст"):
            assert expected in text, expected

    def test_add_button_exists(self, form_html):
        assert 'id="add-question"' in form_html

    def test_no_separate_preview_panel(self, form_html):
        """Превью отдельной панелью перегружало страницу — его быть не должно."""
        assert "question-preview" not in form_html
        assert "Как это увидит участник" not in form_html


class TestAnswerFrames:
    """Варианты ответа вводятся прямо в фрейм ответа, а не в отдельный
    textarea и не в отдельное превью."""

    def test_choices_are_edited_in_place(self, form_html):
        assert "data-choice-rows" in form_html
        assert "choice-row" in form_html

    def test_choice_rows_use_real_answer_controls(self, form_html):
        """Маркер строки — тот же radio/checkbox, что увидит участник."""
        rows = re.findall(r'<div class="choice-row">(.*?)</div>', form_html, re.S)

        assert rows, "не найдено строк вариантов"
        for row in rows:
            assert 'class="choice-mark"' in row
            assert 'class="choice-text"' in row

    def test_choice_text_has_no_name(self, form_html):
        """У полей ввода варианта не должно быть name: собирает их скрытое поле."""
        rows = re.findall(r'<input[^>]*class="choice-text"[^>]*>', form_html)

        assert rows, "не найдено полей ввода варианта"
        for row in rows:
            assert "name=" not in row, row

    def test_hidden_field_carries_choices(self, form_html):
        """Сервер по-прежнему ждёт q<N>_choices одной строкой с переносами."""
        assert re.search(r'<input[^>]*class="choices-value"[^>]*name="q0_choices"', form_html)

    def test_yes_no_frame_is_rendered(self, form_html):
        frame = re.search(r'<div class="question-yesno".*?</div>\s*</div>', form_html, re.S)

        assert frame, "нет фрейма да/нет"
        assert "Да" in frame.group(0)
        assert "Нет" in frame.group(0)

    def test_scale_frame_is_declared(self, form_html):
        assert "data-scale-frame" in form_html

    def test_text_frame_is_declared(self, form_html):
        frame = re.search(r'<div class="question-open"[^>]*>(.*?)</textarea>', form_html, re.S)

        assert frame, "нет фрейма свободного ответа"
        assert "disabled" in frame.group(0)

    def test_add_choice_button_exists(self, form_html):
        assert "js-add-choice" in form_html

    def test_choices_are_written_into_hidden_field_by_script(self, client, clean_tables):
        script = client.get("/static/js/survey_builder.js").text

        assert "syncChoices" in script
        assert "lines.join" in script

    def test_submit_syncs_choices(self, client, clean_tables):
        """Без синхронизации на submit пустые строки попадут в анкету."""
        script = client.get("/static/js/survey_builder.js").text
        handler = script[script.find('form.addEventListener("submit"'):]

        assert "syncChoices(node)" in handler
        assert "syncCorrect(node)" in handler
