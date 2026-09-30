"""Оформление анкеты глазами создателя: картинки, тема, шрифт.

Проверяем четыре вещи, которые ломались по отдельности:
- картинка доходит до публичной страницы и лежит под серверным именем;
- неудачная загрузка не оставляет анкету наполовину и не оставляет файл
  на диске (с обложкой это было отдельным дефектом);
- правка анкеты не стирает картинки молча и не подставляет имя файла,
  присланное клиентом;
- ключ без права правки не может поменять оформление чужой анкеты.
"""

from __future__ import annotations

import re

import pytest

from core.models import Survey
from surveys import crud
from tests import factories

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def form_payload(**overrides) -> dict:
    payload = {
        "title": ["Анкета после покупки"],
        "q0_type": ["single"],
        "q0_text": ["Как вам сервис?"],
        "q0_choices": ["Отлично\nПлохо"],
        "q0_is_required": ["1"],
        "q1_type": ["text"],
        "q1_text": ["Комментарий"],
    }
    payload.update(overrides)
    return payload


def upload(field: str, payload: bytes, filename: str, mime: str) -> dict:
    return {field: (filename, payload, mime)}


def question_image_of(db_session, survey) -> str | None:
    images = [
        question.image
        for question in factories.questions_of(db_session, survey)
        if question.image
    ]
    return images[0] if images else None


def post_form(client, url: str, payload: dict, follow_redirects: bool = False):
    """Отправляет форму, разнося текст и файлы по разным аргументам httpx.

    `multipart` в анкете обязателен: текстовые поля и картинки едут одним
    запросом, но httpx требует, чтобы файлы лежали в `files`, иначе значение
    отправится строкой.
    """
    data: dict = {}
    files: dict = {}
    for key, value in payload.items():
        if isinstance(value, tuple):
            files[key] = value
        else:
            data[key] = value
    return client.post(url, data=data, files=files, follow_redirects=follow_redirects)


class TestUploadOnCreate:
    @pytest.fixture(autouse=True)
    def _signed_in(self, client, db_session, clean_tables):
        factories.login_as(client, db_session)

    def test_cover_and_question_image_are_saved(self, client, db_session, clean_tables, media_dir):
        response = post_form(
            client,
            "/surveys/new",
            {
                **form_payload(),
                **upload("cover", PNG, "обложка.png", "image/png"),
                **upload("q1_image", JPEG, "shot.jpg", "image/jpeg"),
            },
        )

        assert response.status_code == 303
        survey = db_session.query(Survey).one()
        assert survey.cover_image and survey.cover_image.endswith(".png")
        assert "обложка" not in survey.cover_image

        question_images = [
            question.image for question in factories.questions_of(db_session, survey)
        ]
        assert question_images[1].endswith(".jpg")
        assert len(list(media_dir.iterdir())) == 2

    def test_files_are_visible_on_public_page(self, client, db_session, clean_tables, media_dir):
        post_form(
            client,
            "/surveys/new",
            {
                **form_payload(),
                **upload("cover", PNG, "c.png", "image/png"),
                **upload("q1_image", PNG, "q.png", "image/png"),
            },
        )
        survey = db_session.query(Survey).one()

        response = client.get(f"/s/{survey.slug}")

        assert response.status_code == 200
        # Именно значение `src`, а не его наличие в тексте: страница печатала
        # в атрибут весь объект `ImageView(...)`, и подстрока `/media/...`
        # внутри этого мусора попадалась в проверку, хотя картинка не грузилась.
        sources = re.findall(r'<img[^>]*src="([^"]*)"', response.text)
        assert f"/media/{survey.cover_image}" in sources
        assert f"/media/{question_image_of(db_session, survey)}" in sources
        assert 'class="question-image"' in response.text

    def test_cover_url_actually_serves_the_file(self, client, db_session, clean_tables, media_dir):
        """Не «в разметке есть», а по-настоящему отдаётся картинкой."""
        post_form(
            client,
            "/surveys/new",
            {**form_payload(), **upload("cover", PNG, "c.png", "image/png")},
        )
        survey = db_session.query(Survey).one()

        response = client.get(f"/media/{survey.cover_image}")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_rejected_question_image_leaves_no_survey_and_no_files(
        self, client, db_session, clean_tables, media_dir
    ):
        response = post_form(
            client,
            "/surveys/new",
            {
                **form_payload(),
                **upload("cover", PNG, "c.png", "image/png"),
                **upload("q1_image", SVG, "logo.svg", "image/svg+xml"),
            },
        )

        assert response.status_code == 400
        assert db_session.query(Survey).count() == 0
        # Обложка успела записаться до падения — её тоже должно унести.
        assert not list(media_dir.iterdir())
        assert "не поддерживаемая картинка" in response.text

    def test_rejected_cover_keeps_typed_questions(self, client, db_session, clean_tables, media_dir):
        response = post_form(
            client,
            "/surveys/new",
            {**form_payload(), **upload("cover", SVG, "x.svg", "image/svg+xml")},
        )

        assert response.status_code == 400
        assert "Комментарий" in response.text
        assert not list(media_dir.iterdir())


class TestErrorPlacement:
    """Отказ должен показываться у того поля, к которому относится.

    Иначе создатель ищет причину у обложки, которую он не менял, и
    повторяет попытку — с тем же результатом.
    """

    ERROR_TEXT = "не поддерживаемая картинка"

    @pytest.fixture(autouse=True)
    def _signed_in(self, client, db_session, clean_tables):
        self.user = factories.login_as(client, db_session)

    def _question_blocks(self, response) -> list[str]:
        """Текст каждой карточки вопроса на странице."""
        html = response.text
        blocks = re.split(r'<article class="question-block', html)[1:]
        return [block.split("</article>")[0] for block in blocks]

    def test_rejected_cover_does_not_blame_a_question(
        self, client, db_session, clean_tables, media_dir
    ):
        response = post_form(
            client,
            "/surveys/new",
            {**form_payload(), **upload("cover", SVG, "x.svg", "image/svg+xml")},
        )

        assert self.ERROR_TEXT in response.text
        assert not any(self.ERROR_TEXT in block for block in self._question_blocks(response))

    def test_rejected_question_image_blames_only_that_question(
        self, client, db_session, clean_tables, media_dir
    ):
        response = post_form(
            client,
            "/surveys/new",
            {**form_payload(), **upload("q1_image", SVG, "x.svg", "image/svg+xml")},
        )

        blamed = [
            block
            for block in self._question_blocks(response)
            if self.ERROR_TEXT in block
        ]
        assert len(blamed) == 1
        assert 'data-error-for="q1"' in blamed[0]
        assert 'data-error-for="q0"' in response.text
        assert "Комментарий" in response.text

    def test_rejected_image_on_edit_blames_only_that_question(
        self, client, db_session, clean_tables, media_dir
    ):
        survey, _ = factories.make_survey(db_session, self.user)

        response = post_form(
            client,
            f"/s/{survey.slug}/edit",
            {**form_payload(), **upload("q0_image", SVG, "x.svg", "image/svg+xml")},
        )

        blamed = [
            block
            for block in self._question_blocks(response)
            if self.ERROR_TEXT in block
        ]
        assert len(blamed) == 1
        assert 'data-error-for="q0"' in blamed[0]

    def test_hidden_template_block_has_no_error(
        self, client, db_session, clean_tables, media_dir
    ):
        """Заготовка нового вопроса не должна тащить чужой текст ошибки:
        скрипт клонирует её, и предупреждение оказывается не там, где
        создатель его искал."""
        response = post_form(
            client,
            "/surveys/new",
            {**form_payload(), **upload("q1_image", SVG, "x.svg", "image/svg+xml")},
        )

        template = response.text.split('<template id="question-template">')[1]
        template = template.split("</template>")[0]
        assert self.ERROR_TEXT not in template


class TestAppearanceSettings:
    @pytest.fixture(autouse=True)
    def _signed_in(self, client, db_session, clean_tables):
        self.user = factories.login_as(client, db_session)

    def test_theme_and_font_are_saved(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)

        response = client.post(
            f"/s/{survey.slug}/appearance",
            data={"theme": "dark", "font": "serif"},
        )

        assert response.status_code == 200
        assert "Оформление обновлено." in response.text
        db_session.refresh(survey)
        assert (survey.theme, survey.font) == ("dark", "serif")

    def test_unknown_theme_is_ignored(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)

        client.post(
            f"/s/{survey.slug}/appearance",
            data={"theme": "не-существует", "font": "../etc/passwd"},
        )

        db_session.refresh(survey)
        assert (survey.theme, survey.font) == ("light", "system")

    def test_theme_reaches_public_page(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)
        client.post(f"/s/{survey.slug}/appearance", data={"theme": "mint", "font": "mono"})

        response = client.get(f"/s/{survey.slug}")

        assert response.status_code == 200
        assert "--primary:#059669" in response.text
        assert "themed" in response.text

    def test_editor_offers_all_themes_and_fonts(
        self, client, db_session, clean_tables, media_dir
    ):
        survey, _ = factories.make_survey(db_session, self.user)

        response = client.get(f"/s/{survey.slug}/edit")

        assert response.status_code == 200
        for key in ("light", "dark", "mint", "sand", "night"):
            assert f'value="{key}"' in response.text
        for key in ("system", "serif", "rounded", "mono"):
            assert f'value="{key}"' in response.text

    def test_settings_page_no_longer_holds_appearance_forms(
        self, client, db_session, clean_tables, media_dir
    ):
        """Оформление живёт в редакторе. На странице «Ключи и ссылки» его
        вообще нет, а на карточке анкеты редактор открывается кнопкой."""
        survey, _ = factories.make_survey(db_session, self.user)

        response = client.get(f"/s/{survey.slug}/settings")

        assert f'action="/s/{survey.slug}/cover"' not in response.text
        assert f'action="/s/{survey.slug}/appearance"' not in response.text
        assert f'href="/s/{survey.slug}/edit"' not in response.text
        assert "Оформление" not in response.text

        dashboard = client.get("/dashboard")
        assert f'href="/s/{survey.slug}/edit"' in dashboard.text

    def test_editor_carries_cover_form(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)

        response = client.get(f"/s/{survey.slug}/edit")

        assert f'action="/s/{survey.slug}/cover"' in response.text
        assert f'action="/s/{survey.slug}/appearance"' in response.text
        assert 'enctype="multipart/form-data"' in response.text

    def test_cover_replace_deletes_old_file(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)
        first = client.post(
            f"/s/{survey.slug}/cover", files=upload("cover", PNG, "a.png", "image/png")
        )
        assert first.status_code == 200
        db_session.refresh(survey)
        old_name = survey.cover_image

        client.post(f"/s/{survey.slug}/cover", files=upload("cover", JPEG, "b.jpg", "image/jpeg"))

        db_session.refresh(survey)
        assert survey.cover_image != old_name
        assert not (media_dir / old_name).exists()
        assert (media_dir / survey.cover_image).exists()

    def test_cover_can_be_removed(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)
        client.post(f"/s/{survey.slug}/cover", files=upload("cover", PNG, "a.png", "image/png"))
        db_session.refresh(survey)
        assert survey.cover_image

        response = client.post(f"/s/{survey.slug}/cover", data={"clear": "1"})

        assert response.status_code == 200
        db_session.refresh(survey)
        assert survey.cover_image is None
        assert not list(media_dir.iterdir())

    def test_rejected_cover_keeps_previous_file(self, client, db_session, clean_tables, media_dir):
        survey, _ = factories.make_survey(db_session, self.user)
        client.post(f"/s/{survey.slug}/cover", files=upload("cover", PNG, "a.png", "image/png"))
        db_session.refresh(survey)
        original = survey.cover_image

        response = client.post(
            f"/s/{survey.slug}/cover", files=upload("cover", SVG, "x.svg", "image/svg+xml")
        )

        assert response.status_code == 400
        assert "не поддерживаемая картинка" in response.text
        db_session.refresh(survey)
        assert survey.cover_image == original
        assert (media_dir / original).exists()


class TestQuestionImagesOnEdit:
    @pytest.fixture(autouse=True)
    def _signed_in(self, client, db_session, clean_tables, media_dir):
        self.user = factories.login_as(client, db_session)
        self.survey, _ = factories.make_survey(db_session, self.user)
        post_form(
            client,
            f"/s/{self.survey.slug}/edit",
            {**form_payload(), **upload("q0_image", PNG, "q0.png", "image/png")},
        )
        db_session.refresh(self.survey)

    def _images(self, db_session) -> dict:
        return {
            question.position: question.image
            for question in factories.questions_of(db_session, self.survey)
        }

    def test_image_is_saved_on_first_edit(self, client, db_session, clean_tables, media_dir):
        assert self._images(db_session)[0].endswith(".png")

    def test_keeping_image_preserves_file(self, client, db_session, clean_tables, media_dir):
        kept = self._images(db_session)[0]

        response = post_form(
            client,
            f"/s/{self.survey.slug}/edit",
            {
                **form_payload(),
                "q0_image_keep": kept,
                **upload("q1_image", PNG, "q1.png", "image/png"),
            },
        )

        assert response.status_code == 303
        images = self._images(db_session)
        assert images[0] == kept
        assert (media_dir / kept).exists()
        assert images[1].endswith(".png")

    def test_replacing_image_deletes_old_file(self, client, db_session, clean_tables, media_dir):
        old = self._images(db_session)[0]

        post_form(
            client,
            f"/s/{self.survey.slug}/edit",
            {**form_payload(), **upload("q0_image", JPEG, "new.jpg", "image/jpeg")},
        )

        images = self._images(db_session)
        assert images[0] != old
        assert not (media_dir / old).exists()

    def test_clear_checkbox_removes_image(self, client, db_session, clean_tables, media_dir):
        old = self._images(db_session)[0]

        client.post(
            f"/s/{self.survey.slug}/edit",
            data={**form_payload(), "q0_image_keep": old, "q0_image_clear": "1"},
            follow_redirects=False,
        )

        assert self._images(db_session)[0] is None
        assert not (media_dir / old).exists()

    def test_forged_keep_field_is_ignored(self, client, db_session, clean_tables, media_dir):
        """Скрытое поле формы приходит от клиента: чужой путь сохранять нельзя."""
        (media_dir / "чужой.png").write_bytes(PNG)

        client.post(
            f"/s/{self.survey.slug}/edit",
            data={**form_payload(), "q0_image_keep": "чужой.png"},
            follow_redirects=False,
        )

        assert self._images(db_session)[0] is None

    def test_rejected_image_keeps_questions(self, client, db_session, clean_tables, media_dir):
        old = self._images(db_session)[0]

        response = post_form(
            client,
            f"/s/{self.survey.slug}/edit",
            {**form_payload(), **upload("q0_image", SVG, "x.svg", "image/svg+xml")},
        )

        assert response.status_code == 400
        # Вопросы не должны исчезнуть из-за отклонённого файла.
        assert len(factories.questions_of(db_session, self.survey)) == 2
        assert (media_dir / old).exists()


class TestAccessToAppearance:
    def test_read_only_key_cannot_change_appearance(
        self, client, db_session, clean_tables, media_dir
    ):
        mine = factories.login_as(client, db_session)
        survey, _ = factories.make_survey(db_session, mine)
        read_only = crud.create_key(db_session, survey, "коллега")
        # Сессия владельца сбрасывается: иначе доступ дал бы cookie, а не ключ,
        # и проверка прав ключа ничего не значила бы.
        client.cookies.clear()

        for url, data in (
            (f"/s/{survey.slug}/appearance", {"theme": "dark", "font": "mono"}),
            (f"/s/{survey.slug}/cover", {"clear": "1"}),
        ):
            response = client.post(f"{url}?key={read_only.token}", data=data, follow_redirects=False)
            assert response.status_code in (303, 404), url

        db_session.refresh(survey)
        assert (survey.theme, survey.font) == ("light", "system")

    def test_edit_key_can_change_appearance(self, client, db_session, clean_tables, media_dir):
        mine = factories.login_as(client, db_session)
        survey, _ = factories.make_survey(db_session, mine)
        editor = crud.create_key(db_session, survey, "редактор", can_edit=True)
        client.cookies.clear()

        response = client.post(
            f"/s/{survey.slug}/appearance?key={editor.token}",
            data={"theme": "night", "font": "rounded"},
        )

        assert response.status_code == 200
        db_session.refresh(survey)
        assert (survey.theme, survey.font) == ("night", "rounded")

    def test_stranger_cannot_change_appearance(self, client, db_session, clean_tables, media_dir):
        mine = factories.login_as(client, db_session)
        survey, _ = factories.make_survey(db_session, mine)
        factories.make_owner(db_session, "mallory")

        client.post("/login", data={"username": "mallory", "password": "secret1"})
        response = client.post(
            f"/s/{survey.slug}/appearance", data={"theme": "dark", "font": "mono"}
        )

        assert response.status_code == 404
        db_session.refresh(survey)
        assert survey.theme == "light"


class TestDeleteRemovesFiles:
    def test_deleting_survey_removes_cover_and_question_images(
        self, client, db_session, clean_tables, media_dir
    ):
        user = factories.login_as(client, db_session)
        survey, _ = factories.make_survey(db_session, user)
        client.post(
            f"/s/{survey.slug}/cover", files=upload("cover", PNG, "c.png", "image/png")
        )
        post_form(
            client,
            f"/s/{survey.slug}/edit",
            {**form_payload(), **upload("q0_image", PNG, "q.png", "image/png")},
        )
        assert len(list(media_dir.iterdir())) == 2

        response = client.post(f"/s/{survey.slug}/delete", follow_redirects=False)

        assert response.status_code == 303
        assert not list(media_dir.iterdir())
