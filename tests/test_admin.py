"""Админ-зона: вход по учётной записи, загрузка файлов, отдача картинок.

Проверяем и границы доступа (создателю раздела нет, гостю его тоже нет), и
саму обработку файла: тип по содержимому, лимит, безопасное имя.

Раньше здесь был раздел по `MODERATOR_TOKEN` из `.env`, и одинаковый токен
по умолчанию был у всех. Теперь доступ даёт аккаунт с ролью `admin`.
"""

from __future__ import annotations

import pytest

from tests import factories

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 56
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

ADMIN_PAGES = ("", "/appearance", "/instructions")


def _login(client, username: str = "admin", password: str = "admin"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def _upload(
    client,
    url: str,
    field: str,
    payload: bytes,
    filename: str,
    *,
    follow_redirects: bool = True,
    **extra,
):
    return client.post(
        url,
        files={field: (filename, payload, "application/octet-stream")},
        data=extra,
        follow_redirects=follow_redirects,
    )


class TestSectionAccess:
    def test_admin_sees_every_page(self, client, as_admin, clean_tables):
        for path in ADMIN_PAGES:
            assert client.get(f"/admin{path}").status_code == 200, path

    def test_guest_is_sent_to_login(self, client, clean_tables):
        for path in ADMIN_PAGES:
            response = client.get(f"/admin{path}", follow_redirects=False)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/login?next=")

    def test_creator_does_not_see_section(self, client, as_creator, clean_tables):
        """Не 403, а 404: существование раздела постороннему не подтверждаем."""
        for path in ADMIN_PAGES:
            assert client.get(f"/admin{path}").status_code == 404, path

    def test_creator_upload_is_404(self, client, as_creator, media_dir, clean_tables):
        response = _upload(client, "/admin/appearance", "logo", PNG, "logo.png")

        assert response.status_code == 404
        assert not list(media_dir.iterdir())

    def test_guest_upload_is_redirected(self, client, media_dir, clean_tables):
        response = _upload(
            client,
            "/admin/appearance",
            "logo",
            PNG,
            "logo.png",
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert not list(media_dir.iterdir())

    def test_api_schema_is_closed(self, client, as_creator, clean_tables):
        assert client.get("/admin/openapi.json").status_code == 404

    def test_public_docs_are_disabled(self, client, as_admin, clean_tables):
        """Документация не должна быть видна даже администратору вне раздела."""
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404

    def test_admin_api_schema_is_available(self, client, as_admin, clean_tables):
        response = client.get("/admin/openapi.json")

        assert response.status_code == 200
        assert "openapi" in response.json()

    def test_api_page_is_gone(self, client, as_admin, clean_tables):
        """Страница со схемой убрана из интерфейса: ссылка на JSON живёт в README."""
        assert client.get("/admin/api").status_code == 404
        assert "/admin/api" not in client.get("/admin").text
        assert "/admin/api" not in client.get("/admin/instructions").text


class TestImageValidation:
    def test_accepts_supported_formats(self, client, as_admin, media_dir, clean_tables):
        for payload in (PNG, JPEG, GIF, WEBP):
            response = _upload(client, "/admin/appearance", "logo", payload, "logo.png")
            assert response.status_code == 200, payload[:4]

    def test_rejects_non_image(self, client, as_admin, media_dir, clean_tables):
        response = _upload(
            client, "/admin/appearance", "logo", b"<?php echo 1; ?>", "logo.png"
        )

        assert response.status_code == 400
        assert "не поддерживаемая картинка" in response.text

    def test_rejects_svg(self, client, as_admin, media_dir, clean_tables):
        """SVG исполняет JavaScript, а картинки живут на том же origin."""
        response = _upload(client, "/admin/appearance", "logo", SVG, "logo.svg")

        assert response.status_code == 400

    def test_rejects_oversized_file(
        self, client, as_admin, media_dir, monkeypatch, clean_tables
    ):
        from core.config import settings

        monkeypatch.setattr(settings, "max_upload_bytes", 32)

        response = _upload(client, "/admin/appearance", "logo", PNG, "logo.png")

        assert response.status_code == 400
        assert "МБ" in response.text

    def test_rejects_empty_file(self, client, as_admin, media_dir, clean_tables):
        response = _upload(client, "/admin/appearance", "logo", b"", "logo.png")

        assert response.status_code == 400
        assert "пустой" in response.text

    def test_type_is_decided_by_content_not_extension(
        self, client, as_admin, media_dir, clean_tables
    ):
        """Файл с именем .png, но с содержимым GIF должен стать .gif."""
        _upload(client, "/admin/appearance", "logo", GIF, "подделка.png")

        saved = list(media_dir.iterdir())
        assert len(saved) == 1
        assert saved[0].suffix == ".gif"

    def test_stored_name_is_generated(self, client, as_admin, media_dir, clean_tables):
        _upload(client, "/admin/appearance", "logo", PNG, "../../../evil.png")

        saved = list(media_dir.iterdir())
        assert len(saved) == 1
        assert ".." not in saved[0].name


class TestSiteBranding:
    def test_background_and_logo_are_saved_and_rendered(
        self, client, as_admin, media_dir, clean_tables
    ):
        assert _upload(
            client, "/admin/appearance", "background", PNG, "bg.png"
        ).status_code == 200
        assert _upload(
            client, "/admin/appearance", "logo", JPEG, "logo.jpg"
        ).status_code == 200

        from database.repositories import site_settings as settings_repo
        from database.session import SessionLocal

        with SessionLocal() as session:
            row = settings_repo.get(session)
            assert row.background_image and row.background_image.endswith(".png")
            assert row.logo_image and row.logo_image.endswith(".jpg")

        index = client.get("/")
        assert "/media/" in index.text

    def test_cover_upload_sets_survey_image(
        self, client, admin, media_dir, db_session, clean_tables
    ):
        owner = factories.unique_owner(db_session, "owner")
        survey, _key = factories.make_survey(db_session, owner)
        factories.login_as(client, db_session, owner.username)

        response = _upload(
            client,
            f"/s/{survey.slug}/cover",
            "cover",
            PNG,
            "cover.png",
        )

        assert response.status_code == 200
        db_session.expire_all()
        assert survey.cover_image is not None

        page = client.get(f"/s/{survey.slug}")
        assert f'src="/media/{survey.cover_image}"' in page.text

    def test_cover_can_be_cleared(
        self, client, admin, media_dir, db_session, clean_tables
    ):
        owner = factories.unique_owner(db_session, "owner")
        survey, _key = factories.make_survey(db_session, owner)

        factories.login_as(client, db_session, owner.username)
        _upload(
            client,
            f"/s/{survey.slug}/cover",
            "cover",
            PNG,
            "cover.png",
        )
        stored = list(media_dir.iterdir())

        response = client.post(f"/s/{survey.slug}/cover", data={"clear": "1"})

        assert response.status_code == 200
        db_session.expire_all()
        assert survey.cover_image is None
        assert not [p for p in media_dir.iterdir() if p in stored]

    def test_admin_appearance_has_no_user_survey_covers(
        self, client, admin, db_session, clean_tables
    ):
        """Оформление сайта — только фон и логотип, обложки анкет не его дело."""
        owner = factories.unique_owner(db_session, "owner")
        survey, _key = factories.make_survey(db_session, owner)
        _login(client, "admin", "admin")

        page = client.get("/admin/appearance")

        assert page.status_code == 200
        assert "Обложки анкет" not in page.text
        assert survey.title not in page.text
        assert f"/admin/surveys/{survey.slug}/cover" not in page.text

    def test_replacing_image_deletes_previous_file(
        self, client, as_admin, media_dir, clean_tables
    ):
        _upload(client, "/admin/appearance", "logo", PNG, "one.png")
        first = list(media_dir.iterdir())[0]

        _upload(client, "/admin/appearance", "logo", JPEG, "two.jpg")

        assert len(list(media_dir.iterdir())) == 1
        assert not first.exists()

    def test_empty_file_input_keeps_current_image(
        self, client, as_admin, media_dir, clean_tables
    ):
        """Пустой input не должен стирать уже загруженный логотип."""
        _upload(client, "/admin/appearance", "logo", PNG, "logo.png")

        response = _upload(client, "/admin/appearance", "background", PNG, "bg.png")

        assert response.status_code == 200
        assert len(list(media_dir.iterdir())) == 2

    def test_logo_can_be_cleared(self, client, as_admin, media_dir, clean_tables):
        _upload(client, "/admin/appearance", "logo", PNG, "logo.png")
        stored = list(media_dir.iterdir())

        response = client.post(
            "/admin/appearance", data={"clear_logo": "1"}
        )

        assert response.status_code == 200
        assert not [p for p in media_dir.iterdir() if p in stored]


class TestMediaRoute:
    def test_served_file_is_available(self, client, as_admin, media_dir, clean_tables):
        _upload(client, "/admin/appearance", "logo", PNG, "logo.png")
        name = list(media_dir.iterdir())[0].name

        response = client.get(f"/media/{name}")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("image/png")
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_media_is_public(self, client, as_admin, media_dir, clean_tables):
        """Картинка оформления показывается всем, в том числе респондентам."""
        _upload(client, "/admin/appearance", "logo", PNG, "logo.png")
        name = list(media_dir.iterdir())[0].name
        client.cookies.clear()

        assert client.get(f"/media/{name}").status_code == 200

    @pytest.mark.parametrize(
        "filename",
        ["../config.py", "..%2Fconfig.py", "subdir/file.png", ".hidden.png", "nope.png"],
    )
    def test_rejects_unsafe_names(
        self, client, as_admin, media_dir, clean_tables, filename
    ):
        assert client.get(f"/media/{filename}").status_code == 404

    def test_does_not_serve_arbitrary_extensions(
        self, client, as_admin, media_dir, clean_tables
    ):
        (media_dir / "secret.py").write_text("x", encoding="utf-8")

        assert client.get("/media/secret.py").status_code == 404


class TestDiscoverability:
    """Раздел должен находиться без угадывания URL.

    Пользователь однажды не смог найти, где менять оформление: ссылка была
    только в подвале и только после входа. Проверяем, что путь виден и до
    входа, и что в навигации нет служебной ссылки на Swagger.
    """

    def test_login_link_visible_before_login(self, client, clean_tables):
        home = client.get("/")

        assert home.status_code == 200
        assert '"/login"' in home.text

    def test_login_link_in_footer_of_every_page(self, client, clean_tables):
        assert '"/login"' in client.get("/my").text

    def test_creator_has_no_admin_link(self, client, as_creator, clean_tables):
        page = client.get("/dashboard")

        assert "/admin" not in page.text

    def test_admin_sees_admin_entry(self, client, as_admin, clean_tables):
        page = client.get("/admin")

        assert page.status_code == 200
        assert "/admin/appearance" in page.text
        assert "/admin/instructions" in page.text

    def test_nav_has_no_swagger_link_for_regular_user(self, client, clean_tables):
        home = client.get("/")
        nav = home.text.split('class="site-nav"')[1].split("</nav>")[0]

        assert ">API<" not in nav
        assert "/docs" not in nav

    def test_no_admin_links_for_guests(self, client, clean_tables):
        home = client.get("/")

        assert "/admin" not in home.text


class TestDeletedSurveyCleansUpCover:
    def test_orphan_cover_is_removed(self, client, admin, media_dir, db_session, clean_tables):
        owner = factories.unique_owner(db_session, "owner")
        survey, _key = factories.make_survey(db_session, owner)
        factories.login_as(client, db_session, owner.username)
        _upload(
            client,
            f"/s/{survey.slug}/cover",
            "cover",
            PNG,
            "cover.png",
        )
        stored = list(media_dir.iterdir())
        assert stored

        factories.login_as(client, db_session, owner.username)
        response = client.post(f"/s/{survey.slug}/delete", follow_redirects=False)
        assert response.status_code == 303

        assert not [p for p in media_dir.iterdir() if p in stored]
