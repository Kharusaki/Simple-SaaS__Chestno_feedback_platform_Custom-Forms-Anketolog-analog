"""Встроенные шаблоны: от выбора в форме до балла у респондента.

Самая ценная проверка здесь последняя: шаблон отдаёт вопросы в форму, форма
их разбирает, и только потом появляется анкета. Ошибка в любом из трёх слоёв
выглядит снаружи одинаково — пустая анкета, — поэтому тест проходит весь
путь и считает балл по-настоящему, а не сверяет словарь с самим собой.
"""

from __future__ import annotations

import re

import pytest

from core.models import Survey
from surveys import presets
from tests import factories


def preset_form_data(preset: presets.Preset) -> dict[str, list[str]]:
    """Переводит шаблон в поля формы ровно так, как это делает браузер.

    Скрипт редактора отправляет варианты и правильные ответы скрытыми
    полями `qN_choices` и `qN_correct`, а «да/нет» и шкалу — отдельными
    именами. Здесь те же имена собираются вручную: это независимая
    проверка того, что серверный разбор понимает то, что прислал браузер.
    """
    data: dict[str, list[str]] = {
        "title": [preset.title],
        "description": [preset.description],
        "review_mode": [preset.review_mode],
    }
    for index, question in enumerate(preset.questions):
        prefix = f"q{index}"
        data[f"{prefix}_type"] = [str(question["type"])]
        data[f"{prefix}_text"] = [str(question["text"])]
        if question["is_required"]:
            data[f"{prefix}_is_required"] = ["1"]
        choices = str(question["choices"] or "")
        if choices:
            data[f"{prefix}_choices"] = [choices]
            data[f"{prefix}_correct"] = ["\n".join(question["correct_choices"])]
        if question["correct_value_text"]:
            data[f"{prefix}_correct_yes_no"] = [str(question["correct_value_text"])]
        if question["correct_value_int"] is not None:
            data[f"{prefix}_correct_scale"] = [str(question["correct_value_int"])]
        if question.get("scale"):
            data[f"{prefix}_scale"] = [str(question["scale"])]
        if question["explanation"]:
            data[f"{prefix}_explanation"] = [str(question["explanation"])]
    return data


def post_form(client, url: str, payload: dict):
    """Отправляет форму без перехода по редиректу.

    Тестовый клиент сам идёт за редиректами, и тогда успешное создание
    выглядело бы как 200 дашборда — по коду нельзя отличить его от
    отказа валидации, который тоже рендерится в 200.
    """
    return client.post(url, data=payload, files={}, follow_redirects=False)


@pytest.fixture(autouse=True)
def _signed_in(client, db_session, clean_tables):
    factories.login_as(client, db_session)


class TestPresetData:
    def test_three_presets_are_offered(self):
        assert [preset.key for preset in presets.PRESETS] == [
            "satisfaction",
            "after_purchase",
            "knowledge",
        ]

    def test_every_preset_is_five_questions(self):
        for preset in presets.PRESETS:
            assert len(preset.questions) == 5, preset.key

    def test_only_the_test_preset_checks_answers(self):
        """Проверка включается ровно там, где она осмысленна.

        Анкета об удовлетворённости не имеет правильных ответов, и включённый
        режим проверки показал бы респонденту пустую страницу итога.
        """
        for preset in presets.PRESETS:
            if preset.key == "knowledge":
                assert preset.review_mode == "explain"
            else:
                assert preset.review_mode == "none"

    def test_graded_questions_have_explanations(self):
        """Разбор без пояснения — просто «неверно», в нём нет смысла."""
        for question in presets.KNOWLEDGE_TEST.questions:
            assert question["explanation"], question["text"]

    def test_correct_answers_match_existing_choices(self):
        """Правильный ответ обязан совпадать с текстом варианта.

        Балл считается сравнением строк, и опечатка в шаблоне сделала бы
        вопрос неоцениваемым без единого сообщения об ошибке.
        """
        for question in presets.KNOWLEDGE_TEST.questions:
            for correct in question["correct_choices"]:
                assert correct in str(question["choices"]).split("\n"), question["text"]

    def test_unknown_key_is_ignored(self):
        assert presets.get_preset("nope") is None


class TestPresetForm:
    def test_plain_form_shows_all_presets(self, client):
        page = client.get("/surveys/new")

        assert page.status_code == 200
        for preset in presets.PRESETS:
            assert f"preset={preset.key}" in page.text
            assert preset.label in page.text

    def test_unknown_preset_falls_back_to_empty_form(self, client):
        page = client.get("/surveys/new?preset=does-not-exist")

        assert page.status_code == 200
        assert 'value="Тест знаний"' not in page.text
        # Пустая форма рисует ровно один вопрос: первый блок плюс
        # заготовка в <template>, из которой скрипт клонирует новые.
        assert 'name="q0_text"' in page.text
        assert 'name="q1_text"' not in page.text

    def test_preset_renders_five_blocks(self, client):
        page = client.get("/surveys/new?preset=knowledge")

        for index in range(5):
            assert f'name="q{index}_text"' in page.text
        assert 'name="q5_text"' not in page.text

    def test_preset_fills_question_texts(self, client):
        page = client.get("/surveys/new?preset=satisfaction")

        assert "Как вам сервис" not in page.text
        for question in presets.SATISFACTION.questions:
            assert str(question["text"]) in page.text

    def test_quiz_preset_marks_correct_answers(self, client):
        page = client.get("/surveys/new?preset=knowledge")

        # Галочка «верно» у нужного варианта и выбранный режим.
        assert 'value="Франция"' in page.text
        assert page.text.count("choice-correct-check") > 5
        assert 'value="explain" selected' in page.text.replace("\n", "").replace("  ", " ")

    def test_plain_preset_hides_correct_marks(self, client):
        page = client.get("/surveys/new?preset=satisfaction")

        assert 'value="none" selected' in page.text.replace("\n", "").replace("  ", " ")


class TestPresetCreatesSurvey:
    def test_satisfaction_preset_saves_five_questions(self, client, db_session):
        response = post_form(client, "/surveys/new", preset_form_data(presets.SATISFACTION))

        assert response.status_code == 303
        survey = db_session.query(Survey).one()
        assert survey.title == presets.SATISFACTION.title
        assert survey.review_mode == "none"
        assert len(factories.questions_of(db_session, survey)) == 5

    def test_choices_survive_the_form(self, client, db_session):
        post_form(client, "/surveys/new", preset_form_data(presets.AFTER_PURCHASE))

        survey = db_session.query(Survey).one()
        first = factories.questions_of(db_session, survey)[0]
        assert factories.choices_of(db_session, first) == [
            "Полностью доволен",
            "Скорее доволен",
            "Скорее не доволен",
            "Не доволен",
        ]

    def test_quiz_preset_keeps_correct_answers(self, client, db_session):
        post_form(client, "/surveys/new", preset_form_data(presets.KNOWLEDGE_TEST))

        survey = db_session.query(Survey).one()
        questions = {q.text: q for q in factories.questions_of(db_session, survey)}

        capital = questions["Какая страна имеет столицу Париж?"]
        assert [c.text for c in capital.correct_choices] == ["Франция"]
        assert questions["Сколько будет 2 + 2?"].correct_value_int == 4
        assert questions["Земля обращается вокруг Солнца?"].correct_value_text == "Да"
        assert len(questions["Выберите все чётные числа"].correct_choices) == 3


class TestQuizPresetIsActuallyScored:
    """Конечная цель шаблона: участник получает балл без единой правки."""

    def _created_quiz(self, client, db_session) -> Survey:
        post_form(client, "/surveys/new", preset_form_data(presets.KNOWLEDGE_TEST))
        db_session.expire_all()
        return db_session.query(Survey).one()

    def test_perfect_answers_give_full_score(self, client, db_session):
        survey = self._created_quiz(client, db_session)

        client.post(
            f"/s/{survey.slug}",
            data={
                "q0": ["Франция"],
                "q1": ["4"],
                "q2": ["2", "4", "6"],
                "q3": ["Да"],
                "q4": ["Отправляет запросы серверу"],
            },
            follow_redirects=True,
        )

        page = client.get(f"/s/{survey.slug}/thanks")
        assert page.status_code == 200
        assert "5" in page.text
        assert "Париж — столица Франции" in page.text

    def test_half_correct_gives_partial_score(self, client, db_session):
        survey = self._created_quiz(client, db_session)

        client.post(
            f"/s/{survey.slug}",
            data={
                "q0": ["Франция"],
                "q1": ["5"],
                "q2": ["2"],
                "q3": ["Нет"],
                "q4": ["Хранит базу данных"],
            },
            follow_redirects=True,
        )

        page = client.get(f"/s/{survey.slug}/thanks")
        assert "1" in page.text
        assert "Париж — столица Франции" in page.text

    def test_survey_page_works_for_every_question_type(self, client, db_session):
        """Шаблон проходит через `taking`: типы не должны ломать приём ответов."""
        survey = self._created_quiz(client, db_session)

        page = client.get(f"/s/{survey.slug}")

        assert page.status_code == 200
        assert "name=\"q2\"" in page.text
        assert "checkbox" in page.text
