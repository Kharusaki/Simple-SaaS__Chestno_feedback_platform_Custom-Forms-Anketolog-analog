"""Ключи доступа: выпуск, отзыв, вход по ссылке `/k/{token}`.

Разделение полномочий проверяется жёстко: обычный ключ «для коллеги» даёт
только чтение статистики, иначе ссылка, которой поделились ради графиков,
позволила бы ещё и переписать анкету.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from auth import keys as keys_auth
from core.models import SurveyKey, User
from surveys import crud
from tests import factories
from tests.test_surveys_crud import form_payload


def user_count(session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0


@pytest.fixture
def owned(client, db_session, clean_tables):
    owner = factories.login_as(client, db_session)
    survey, primary = factories.make_survey(db_session, owner, title="Анкета с ключами")
    return survey, primary


def keys_of(session, survey) -> list[SurveyKey]:
    return list(
        session.scalars(
            select(SurveyKey).where(SurveyKey.survey_id == survey.id).order_by(SurveyKey.id)
        )
    )


def issue(client, slug: str, label: str, can_edit: bool = False):
    data = {"label": label}
    if can_edit:
        data["can_edit"] = "1"
    return client.post(f"/s/{slug}/keys", data=data, follow_redirects=False)


def revoke(client, slug: str, key_id: int):
    return client.post(f"/s/{slug}/keys/{key_id}/revoke", follow_redirects=False)


class TestIssueKey:
    def test_owner_creates_read_only_key(self, client, db_session, owned):
        survey, _primary = owned

        response = issue(client, survey.slug, "для отдела продаж")

        assert response.status_code == 303
        labels = {key.label: key for key in keys_of(db_session, survey)}
        assert "для отдела продаж" in labels
        assert labels["для отдела продаж"].can_edit is False

    def test_owner_creates_edit_key(self, client, db_session, owned):
        survey, _primary = owned

        issue(client, survey.slug, "редактор", can_edit=True)

        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        assert key.can_edit is True

    def test_new_key_has_own_token(self, client, db_session, owned):
        survey, primary = owned

        issue(client, survey.slug, "коллега")

        new_key = next(k for k in keys_of(db_session, survey) if k.label == "коллега")
        assert new_key.token != primary.token
        assert len(new_key.token) == 48

    def test_duplicate_label_is_rejected(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")

        response = issue(client, survey.slug, "коллега")

        assert response.status_code == 400
        assert "уже есть" in response.text
        assert len(keys_of(db_session, survey)) == 2

    def test_blank_label_is_rejected(self, client, db_session, owned):
        survey, _primary = owned

        response = client.post(f"/s/{survey.slug}/keys", data={"label": "   "})

        assert response.status_code == 400
        assert len(keys_of(db_session, survey)) == 1

    def test_label_whitespace_is_collapsed(self, client, db_session, owned):
        survey, _primary = owned

        issue(client, survey.slug, "  для   отдела   продаж  ")

        assert any(k.label == "для отдела продаж" for k in keys_of(db_session, survey))

    def test_reserved_label_is_rejected(self, client, db_session, owned):
        survey, _primary = owned

        response = issue(client, survey.slug, crud.PRIMARY_KEY_LABEL)

        assert response.status_code == 400
        assert "занято основным ключом" in response.text
        assert len(keys_of(db_session, survey)) == 1

    def test_reserved_label_rejected_regardless_of_case(self, client, db_session, owned):
        survey, _primary = owned

        response = issue(client, survey.slug, "  ОСНОВНОЙ ДОСТУП  ")

        assert response.status_code == 400
        assert len(keys_of(db_session, survey)) == 1

    def test_crud_rejects_reserved_label(self, db_session, owned):
        survey, _primary = owned

        with pytest.raises(ValueError):
            crud.create_key(db_session, survey, crud.PRIMARY_KEY_LABEL)

    def test_stranger_cannot_issue_key(self, client, db_session, owned):
        survey, _primary = owned
        factories.login_as(client, db_session, "mallory")

        response = issue(client, survey.slug, "вор")

        assert response.status_code == 404
        assert len(keys_of(db_session, survey)) == 1


class TestRevokeKey:
    def test_owner_revokes_extra_key(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        extra = next(k for k in keys_of(db_session, survey) if k.label == "коллега")

        response = revoke(client, survey.slug, extra.id)

        assert response.status_code == 303
        assert "коллега" not in {k.label for k in keys_of(db_session, survey)}

    def test_revoked_key_stops_working(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        extra = next(k for k in keys_of(db_session, survey) if k.label == "коллега")

        assert revoke(client, survey.slug, extra.id).status_code == 303

        client.cookies.clear()
        assert client.get(f"/s/{survey.slug}/stats?key={extra.token}").status_code == 403
        assert client.get(f"/k/{extra.token}").status_code == 404

    def test_edit_key_can_be_revoked(self, client, db_session, owned):
        """Ключ с правом правки отзывается так же, как обычный."""
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        editor = next(k for k in keys_of(db_session, survey) if k.label == "редактор")

        response = revoke(client, survey.slug, editor.id)

        assert response.status_code == 303
        assert "редактор" not in {k.label for k in keys_of(db_session, survey)}

    def test_revoked_edit_key_loses_access(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        editor = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        revoke(client, survey.slug, editor.id)
        client.cookies.clear()

        assert client.get(f"/s/{survey.slug}/edit?key={editor.token}").status_code == 404
        assert client.get(f"/k/{editor.token}").status_code == 404

    def test_primary_key_cannot_be_revoked(self, client, db_session, owned):
        survey, primary = owned

        response = revoke(client, survey.slug, primary.id)

        assert response.status_code == 404
        assert any(k.id == primary.id for k in keys_of(db_session, survey))

    def test_primary_key_survives_revoking_edit_key(self, db_session, owned):
        survey, primary = owned
        editor = crud.create_key(db_session, survey, "редактор", can_edit=True)

        assert crud.revoke_key(db_session, survey, editor.id) is True

        assert crud.get_primary_key(db_session, survey).id == primary.id

    def test_cannot_revoke_key_of_another_survey(self, client, db_session, owned):
        """Ключ выдаётся владельцем другой анкеты, поэтому отозвать его
        из чужой анкеты нельзя: подсказки не подтверждаем."""
        survey, _primary = owned
        other_owner = factories.unique_owner(db_session, "stranger")
        other, _other_key = factories.make_survey(db_session, other_owner, title="Чужая")
        factories.login_as(client, db_session, other_owner.username)
        issue(client, other.slug, "чужой ключ")
        foreign = next(k for k in keys_of(db_session, other) if k.label == "чужой ключ")

        factories.login_as(client, db_session, "back-again")
        response = revoke(client, survey.slug, foreign.id)

        assert response.status_code == 404
        assert any(k.id == foreign.id for k in keys_of(db_session, other))

    def test_guest_is_asked_to_log_in(self, client, db_session, owned):
        survey, _primary = owned
        client.cookies.clear()

        response = client.post(
            f"/s/{survey.slug}/keys",
            data={"label": "без входа"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")
        assert len(keys_of(db_session, survey)) == 1


class TestEnterByKeyLink:
    def test_edit_key_leads_to_settings(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.get(f"/k/{key.token}", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == f"/s/{survey.slug}/settings?key={key.token}"

    def test_read_only_key_leads_to_stats(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        key = next(k for k in keys_of(db_session, survey) if k.label == "коллега")
        client.cookies.clear()

        response = client.get(f"/k/{key.token}", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == f"/s/{survey.slug}/stats?key={key.token}"

    def test_unknown_key_shows_error_page(self, client, owned):
        response = client.get("/k/" + "z" * 48)

        assert response.status_code == 404
        assert "Ключ не найден" in response.text

    def test_key_link_creates_no_user(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        key = next(k for k in keys_of(db_session, survey) if k.label == "коллега")
        client.cookies.clear()
        before = user_count(db_session)

        client.get(f"/k/{key.token}")
        client.get(f"/s/{survey.slug}/settings?key={key.token}")

        assert user_count(db_session) == before

    def test_edit_key_saves_without_creating_user(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()
        before = user_count(db_session)

        form = form_payload(title="Без нового пользователя")
        form["key"] = [key.token]
        client.post(f"/s/{survey.slug}/edit", data=form)

        assert user_count(db_session) == before


class TestEditByKey:
    def test_edit_key_opens_edit_page(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.get(f"/s/{survey.slug}/edit?key={key.token}")

        assert response.status_code == 200
        assert "Редактирование анкеты" in response.text

    def test_edit_key_saves_changes(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        form = form_payload(title="Переписано по ключу")
        form["key"] = [key.token]
        response = client.post(f"/s/{survey.slug}/edit", data=form, follow_redirects=False)

        assert response.status_code == 303
        # Ключ с правом правки возвращается в редактор: оформление анкеты
        # теперь тоже там, а дашборд ему не показывают.
        assert f"/s/{survey.slug}/edit" in response.headers["location"]
        assert f"key={key.token}" in response.headers["location"]
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, survey.slug).title == "Переписано по ключу"

    def test_edit_key_is_carried_in_the_form(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.get(f"/s/{survey.slug}/edit?key={key.token}")

        assert f'name="key" value="{key.token}"' in response.text

    def test_edit_key_cannot_pause_through_edit_form(self, client, db_session, owned):
        """Ключ с правом правки не может остановить приём ответов."""
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        form = form_payload(title="Правка без остановки")
        form["key"] = [key.token]
        form["is_open"] = []
        response = client.post(f"/s/{survey.slug}/edit", data=form, follow_redirects=False)

        assert response.status_code == 303
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, survey.slug).is_open is True

    def test_edit_key_cannot_resume_through_edit_form(self, client, db_session, owned):
        survey, _primary = owned
        crud.set_open(db_session, survey, False)
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        form = form_payload(title="Правка при остановленном приёме")
        form["key"] = [key.token]
        form["is_open"] = ["1"]
        client.post(f"/s/{survey.slug}/edit", data=form, follow_redirects=False)

        db_session.expire_all()
        assert factories.survey_by_slug(db_session, survey.slug).is_open is False

    def test_edit_key_page_has_no_status_checkbox(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.get(f"/s/{survey.slug}/edit?key={key.token}")

        assert 'name="is_open"' not in response.text
        assert "только владелец" in response.text

    def test_owner_still_controls_status_in_edit_form(self, client, db_session, owned):
        survey, _primary = owned

        form = form_payload(title="Владелец остановил")
        form["is_open"] = []
        client.post(f"/s/{survey.slug}/edit", data=form, follow_redirects=False)

        db_session.expire_all()
        assert factories.survey_by_slug(db_session, survey.slug).is_open is False

    def test_read_only_key_cannot_open_edit(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        key = next(k for k in keys_of(db_session, survey) if k.label == "коллега")
        client.cookies.clear()

        assert client.get(f"/s/{survey.slug}/edit?key={key.token}").status_code == 404

    def test_read_only_key_cannot_save(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        key = next(k for k in keys_of(db_session, survey) if k.label == "коллега")
        client.cookies.clear()

        form = form_payload(title="Взломано")
        form["key"] = [key.token]
        response = client.post(f"/s/{survey.slug}/edit", data=form)

        assert response.status_code == 404
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, survey.slug).title == "Анкета с ключами"

    def test_wrong_token_gives_404(self, client, owned):
        survey, _primary = owned
        client.cookies.clear()

        assert client.get(f"/s/{survey.slug}/edit?key={'q' * 48}").status_code == 404

    def test_owner_ignores_wrong_token(self, client, owned):
        """Владелец по cookie проходит, даже если ключ в ссылке чужой."""
        survey, _primary = owned

        assert client.get(f"/s/{survey.slug}/edit?key={'q' * 48}").status_code == 200

    def test_edit_key_cannot_delete_survey(self, client, db_session, owned):
        """Ключ с правом правки анкету не удаляет: на `/delete` нужен вход
        именно владельца, ключ его не заменяет."""
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        for method, path in (("GET", "delete"), ("POST", "delete")):
            response = client.request(
                method, f"/s/{survey.slug}/{path}?key={key.token}", follow_redirects=False
            )
            assert response.status_code == 303
            assert "/login?next=" in response.headers["location"]

    def test_edit_key_cannot_pause_survey(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.post(f"/s/{survey.slug}/open?key={key.token}", follow_redirects=False)
        assert response.status_code == 303
        db_session.expire_all()
        assert factories.survey_by_slug(db_session, survey.slug).is_open is True


class TestSettingsPage:
    def test_owner_sees_all_links(self, client, db_session, owned):
        survey, primary = owned

        response = client.get(f"/s/{survey.slug}/settings")

        assert response.status_code == 200
        assert f"/s/{survey.slug}" in response.text
        assert primary.token in response.text

    def test_owner_sees_primary_and_can_revoke_extra(self, client, db_session, owned):
        survey, primary = owned
        issue(client, survey.slug, "коллега")
        extra = next(k for k in keys_of(db_session, survey) if k.label == "коллега")

        response = client.get(f"/s/{survey.slug}/settings")

        assert f"/s/{survey.slug}/keys/{extra.id}/revoke" in response.text
        assert f"/s/{survey.slug}/keys/{primary.id}/revoke" not in response.text

    def test_key_holder_does_not_see_key_list(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.get(f"/s/{survey.slug}/settings?key={key.token}")

        assert response.status_code == 200
        assert "Управление ключами доступно только владельцу" in response.text
        assert "/revoke" not in response.text
        assert "Создать ключ" not in response.text

    def test_key_holder_cannot_issue_key(self, client, db_session, owned):
        """Ключ выдавать ключи не позволяет: список ключей доступен только
        вошедшему владельцу."""
        survey, _primary = owned
        issue(client, survey.slug, "редактор", can_edit=True)
        key = next(k for k in keys_of(db_session, survey) if k.label == "редактор")
        client.cookies.clear()

        response = client.post(
            f"/s/{survey.slug}/keys?key={key.token}",
            data={"label": "ещё один"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "/login?next=" in response.headers["location"]
        assert len(keys_of(db_session, survey)) == 2

    def test_read_only_key_cannot_open_settings(self, client, db_session, owned):
        survey, _primary = owned
        issue(client, survey.slug, "коллега")
        key = next(k for k in keys_of(db_session, survey) if k.label == "коллега")
        client.cookies.clear()

        assert client.get(f"/s/{survey.slug}/settings?key={key.token}").status_code == 404

    def test_stranger_gets_404(self, client, owned):
        survey, _primary = owned
        client.cookies.clear()

        assert client.get(f"/s/{survey.slug}/settings").status_code == 404

    def test_menu_links_to_settings(self, client, owned):
        survey, _primary = owned

        response = client.get("/dashboard")

        assert f"/s/{survey.slug}/settings" in response.text


class TestKeyLogic:
    def test_can_edit_survey_rejects_read_only_key(self, db_session, owned):
        survey, _primary = owned
        extra = crud.create_key(db_session, survey, "только чтение")

        assert keys_auth.can_edit_survey(db_session, survey, None, extra.token) is False

    def test_can_edit_survey_accepts_edit_key(self, db_session, owned):
        survey, primary = owned

        assert keys_auth.can_edit_survey(db_session, survey, None, primary.token) is True

    def test_is_owner_true_for_owner(self, db_session, owned):
        survey, _primary = owned
        owner = db_session.get(User, survey.owner_id)

        assert keys_auth.is_owner(survey, owner) is True
        assert keys_auth.is_owner(survey, None) is False

    def test_revoke_refuses_foreign_key_id(self, db_session, owned):
        survey, _primary = owned

        assert crud.revoke_key(db_session, survey, 99999) is False

    def test_keys_limit_flag(self, db_session, owned):
        survey, _primary = owned

        assert crud.keys_limit_reached(db_session, survey) is False
        for index in range(crud.MAX_KEYS_PER_SURVEY):
            crud.create_key(db_session, survey, f"ключ {index}")
        assert crud.keys_limit_reached(db_session, survey) is True


class TestLogout:
    def test_logout_clears_owner_session(self, client, db_session, owned):
        survey, _primary = owned
        assert survey.title in client.get("/dashboard").text

        response = client.post("/logout", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert survey.title not in client.get("/dashboard").text

    def test_survey_survives_logout(self, client, db_session, owned):
        survey, _primary = owned
        client.post("/logout")

        assert client.get(f"/s/{survey.slug}").status_code == 200
