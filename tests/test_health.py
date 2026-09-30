from __future__ import annotations


def test_health_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"


def test_index_renders(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "ЧЕСТНО" in response.text
    assert "Простые формы для честной обратной связи" in response.text
    assert "Создать анкету" in response.text


def test_static_css_served(client):
    response = client.get("/static/css/style.css")

    assert response.status_code == 200
    assert "--primary" in response.text


def test_unknown_path_returns_404_page(client):
    response = client.get("/no-such-page")

    assert response.status_code == 404
    assert "Страница не найдена" in response.text


def test_public_openapi_is_closed(client):
    """Схема API описывает закрытые процессы, поэтому наружу она не выдаётся."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path


def test_admin_openapi_available(client, as_admin):
    response = client.get("/admin/openapi.json")

    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
