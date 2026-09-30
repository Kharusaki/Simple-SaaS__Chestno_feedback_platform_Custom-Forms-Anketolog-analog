"""Личный кабинет: смена пароля, удаление аккаунта, отзыв ответов, удаление анкет."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.models import Survey, User
from tests import factories


def test_account_page_requires_login(client: TestClient) -> None:
    response = client.get("/account", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_account_page_shows_info(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    user = factories.login_as(client, db_session, "anna")
    factories.make_survey(db_session, user)

    response = client.get("/account")
    assert response.status_code == 200
    assert "anna" in response.text
    assert "Личный кабинет" in response.text


def test_revoke_all_responses(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    user = factories.login_as(client, db_session, "anna")
    survey, _key = factories.make_survey(db_session, user)

    from surveys import taking

    taking.submit_response(
        db_session,
        survey,
        form={},
        respondent_hash="hash1",
    )
    db_session.commit()

    response = client.post("/account/revoke-responses", follow_redirects=False)
    assert response.status_code == 303
    assert "/account" in response.headers["location"]

    db_session.expire_all()
    survey = db_session.get(Survey, survey.id)
    assert survey is not None
    assert len(survey.responses) == 0


def test_delete_all_surveys(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    user = factories.login_as(client, db_session, "anna")
    factories.make_survey(db_session, user)
    factories.make_survey(db_session, user, title="Вторая анкета")

    response = client.post("/account/delete-surveys", follow_redirects=False)
    assert response.status_code == 303
    assert "/account" in response.headers["location"]

    db_session.expire_all()
    remaining = db_session.query(Survey).filter(Survey.owner_id == user.id).count()
    assert remaining == 0


def test_delete_account_requires_password(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    user = factories.login_as(client, db_session, "anna")
    factories.make_survey(db_session, user)
    user_id = user.id

    response = client.post(
        "/account/delete",
        data={"password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "Неверный пароль" in response.text

    db_session.expire_all()
    assert db_session.query(User).filter(User.id == user_id).first() is not None


def test_delete_account_with_correct_password(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    user = factories.login_as(client, db_session, "anna")
    factories.make_survey(db_session, user)
    # `user` удаляется по HTTP, и после этого обращение к его `id` заставит
    # ORM перезагрузить уже стёртую строку. Поэтому номер запоминаем заранее.
    user_id = user.id

    response = client.post(
        "/account/delete",
        data={"password": "secret1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    db_session.expire_all()
    assert db_session.query(User).filter(User.id == user_id).first() is None
    assert db_session.query(Survey).filter(Survey.owner_id == user_id).count() == 0


def test_delete_account_clears_session(
    client: TestClient, db_session: Session, clean_tables: None
) -> None:
    user = factories.login_as(client, db_session, "anna")

    client.post(
        "/account/delete",
        data={"password": "secret1"},
        follow_redirects=False,
    )

    response = client.get("/account", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_revoke_responses_form_requires_login(client: TestClient) -> None:
    response = client.get("/account/revoke-responses", follow_redirects=False)
    assert response.status_code == 303


def test_delete_surveys_form_requires_login(client: TestClient) -> None:
    response = client.get("/account/delete-surveys", follow_redirects=False)
    assert response.status_code == 303


def test_delete_account_form_requires_login(client: TestClient) -> None:
    response = client.get("/account/delete", follow_redirects=False)
    assert response.status_code == 303
