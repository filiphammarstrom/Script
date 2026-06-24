"""Tester för inloggning, lokalt läge och nyckel-maskning."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_local_mode_needs_no_login(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    cfg = client.get("/api/config").json()
    assert cfg["auth_enabled"] is False
    assert client.get("/api/me").json()["id"] == "local"
    assert client.get("/api/projects").status_code == 200


def test_auth_required_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    assert client.get("/api/config").json()["auth_enabled"] is True
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/projects").status_code == 401


def test_secrets_are_masked(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    client.put("/api/secrets", json={"anthropic_key": "sk-ant-very-secret"})
    s = client.get("/api/secrets").json()
    assert s["anthropic"] is True
    assert s["openai"] is False
    assert "sk-ant-very-secret" not in str(s)  # nyckeln läcker aldrig ut


def test_local_user_data_is_isolated_path(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Privat"}).json()
    # projektet ska gå att läsa tillbaka i samma (lokala) namespace
    assert client.get(f"/api/projects/{p['id']}").json()["title"] == "Privat"
    # och gå att radera
    assert client.delete(f"/api/projects/{p['id']}").status_code == 200
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
