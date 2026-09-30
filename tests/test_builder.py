from __future__ import annotations

import pytest

from surveys.builder import (
    BuildResult,
    build_survey_payload,
    parse_scale,
)


def _form(*blocks: dict, title: str = "Анкета", description: str = "") -> dict:
    form: dict[str, list[str]] = {"title": [title], "description": [description]}
    for position, block in enumerate(blocks, start=1):
        form[f"q{position}_type"] = [block.get("type", "text")]
        form[f"q{position}_text"] = [block.get("text", "Вопрос")]
        if block.get("type") in ("single", "multiple"):
            form[f"q{position}_choices"] = ["\n".join(block["choices"])]
        if block.get("type") == "scale":
            form[f"q{position}_scale"] = [block.get("scale", "1-10")]
        if block.get("required", True):
            form[f"q{position}_is_required"] = ["1"]
    return form


class TestParseScale:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("от 1 до 10", (1, 10)),
            ("1-5", (1, 5)),
            ("5", (1, 5)),
            ("7", (1, 7)),
        ],
    )
    def test_valid_scales(self, raw, expected):
        assert parse_scale(raw) == expected

    @pytest.mark.parametrize("raw", ["", None, "0", "1", "20", "10-1", "от 1 до 12", "много"])
    def test_invalid_scales(self, raw):
        assert parse_scale(raw) == (None, None)


class TestBuildSurveyPayload:
    def test_builds_all_five_types(self):
        result = build_survey_payload(
            _form(
                {"type": "single", "text": "Как вам сервис?", "choices": ["Отлично", "Плохо"]},
                {"type": "multiple", "text": "Что использовали?", "choices": ["API", "Веб"]},
                {"type": "yes_no", "text": "Будете рекомендовать?"},
                {"type": "scale", "text": "Оценка", "scale": "1-10"},
                {"type": "text", "text": "Комментарий"},
            )
        )

        assert result.is_valid
        assert [q.type for q in result.questions] == [
            "single", "multiple", "yes_no", "scale", "text"
        ]
        assert result.questions[0].choices == ["Отлично", "Плохо"]
        assert result.questions[3].scale_min == 1
        assert result.questions[3].scale_max == 10
        assert result.questions[4].choices == []

    def test_positions_are_sequential(self):
        result = build_survey_payload(
            _form({"type": "text", "text": "Первый"}, {"type": "text", "text": "Второй"})
        )

        assert [q.position for q in result.questions] == [0, 1]

    def test_title_is_trimmed_and_collapsed(self):
        result = build_survey_payload(_form(title="  Анкета   после  покупки  "))

        assert result.title == "Анкета после покупки"

    def test_empty_title_is_rejected(self):
        result = build_survey_payload(_form(title="   "))

        assert not result.is_valid
        assert "title" in result.errors

    def test_no_questions_is_rejected(self):
        result = build_survey_payload({"title": ["Опрос"]})

        assert not result.is_valid
        assert result.errors["questions"]

    def test_single_choice_needs_two_variants(self):
        result = build_survey_payload(
            _form({"type": "single", "text": "Вопрос", "choices": ["Только один"]})
        )

        assert not result.is_valid
        assert "q0" in result.errors

    def test_duplicate_choices_are_rejected(self):
        result = build_survey_payload(
            _form({"type": "single", "text": "Вопрос", "choices": ["Да", "да"]})
        )

        assert not result.is_valid
        assert "q0" in result.errors

    def test_unknown_type_is_rejected(self):
        result = build_survey_payload(_form({"type": "matrix", "text": "Вопрос"}))

        assert not result.is_valid
        assert "q0" in result.errors

    def test_bad_scale_is_rejected(self):
        result = build_survey_payload(
            _form({"type": "scale", "text": "Вопрос", "scale": "20"})
        )

        assert not result.is_valid
        assert "q0" in result.errors

    def test_empty_question_text_is_rejected(self):
        result = build_survey_payload(_form({"type": "text", "text": "   "}))

        assert not result.is_valid
        assert "q0" in result.errors

    def test_raw_blocks_kept_for_rerender(self):
        form = _form(
            {"type": "single", "text": "Как вам?", "choices": ["Хорошо", "Плохо"]},
            {"type": "text", "text": "Комментарий"},
        )
        result = build_survey_payload(form)

        assert len(result.raw_blocks) == 2
        assert result.raw_blocks[0]["text"] == "Как вам?"
        assert "Хорошо" in result.raw_blocks[0]["choices"]

    def test_is_required_flag_respected(self):
        result = build_survey_payload(
            _form(
                {
                    "type": "text",
                    "text": "Обязательный",
                    "required": True,
                },
                {
                    "type": "text",
                    "text": "Необязательный",
                    "required": False,
                },
            )
        )

        assert [q.is_required for q in result.questions] == [True, False]

    def test_max_questions_limit(self):
        blocks = [{"type": "text", "text": f"Вопрос {i}"} for i in range(31)]
        result = build_survey_payload(_form(*blocks))

        assert not result.is_valid
        assert "questions" in result.errors


def test_build_result_defaults_are_valid():
    assert BuildResult().is_valid
