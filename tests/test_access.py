"""Tester för roller/åtkomst (allowlist 1a) och grund+egna-sammanslagning (2a)."""
from fastapi.testclient import TestClient

import app.main as main_mod
from app import access, store
from app.models import GlobalSettings


def test_open_until_first_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "_access_path", lambda: tmp_path / "access.json")
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert access.access_enabled() is False
    assert access.is_allowed("anyone@example.com") is True  # öppet tills admin pekats ut


def test_allowlist_and_admin_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "_access_path", lambda: tmp_path / "access.json")
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.com")  # skiftläge ska normaliseras
    assert access.access_enabled() is True
    assert access.is_admin("boss@example.com") is True
    assert access.is_allowed("boss@example.com") is True
    assert access.is_allowed("rando@example.com") is False

    access.add_allowed("friend@example.com")
    assert access.is_allowed("friend@example.com") is True
    assert access.is_admin("friend@example.com") is False

    access.set_admin("friend@example.com", True)
    assert access.is_admin("friend@example.com") is True
    access.set_admin("friend@example.com", False)
    assert access.is_admin("friend@example.com") is False

    access.remove_allowed("friend@example.com")
    assert access.is_allowed("friend@example.com") is False

    # env-admin kan inte tas bort via datafilen
    access.remove_allowed("boss@example.com")
    assert access.is_admin("boss@example.com") is True


def test_snapshot_marks_env_admin(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "_access_path", lambda: tmp_path / "access.json")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    access.add_allowed("friend@example.com")
    snap = access.snapshot()
    assert "boss@example.com" in snap["admins"]
    assert "boss@example.com" in snap["env_admins"]
    assert "friend@example.com" in snap["allowed"]


def test_effective_puts_base_before_own(monkeypatch):
    monkeypatch.setattr(store, "load_base_settings", lambda: GlobalSettings(directives="BASREGLER"))
    monkeypatch.setattr(store, "load_global_settings", lambda uid: GlobalSettings(directives="MINA EGNA"))
    eff = store.effective_global_settings("u1")
    assert "BASREGLER" in eff.directives and "MINA EGNA" in eff.directives
    assert eff.directives.index("BASREGLER") < eff.directives.index("MINA EGNA")


def test_login_gate_and_admin_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(access, "_access_path", lambda: tmp_path / "access.json")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setattr(
        main_mod.auth_mod,
        "verify_google_id_token",
        lambda token: {"sub": token, "email": token + "@example.com", "name": token},
    )

    # ej inbjuden → nekas
    assert TestClient(main_mod.app).post("/auth/google", json={"credential": "rando"}).status_code == 403

    # admin (via env) släpps in och ser is_admin
    boss = TestClient(main_mod.app)
    assert boss.post("/auth/google", json={"credential": "boss"}).status_code == 200
    assert boss.get("/api/me").json()["is_admin"] is True
    assert boss.get("/api/admin/access").status_code == 200

    # inbjuden vanlig användare kommer in men inte åt admin
    access.add_allowed("friend@example.com")
    friend = TestClient(main_mod.app)
    assert friend.post("/auth/google", json={"credential": "friend"}).status_code == 200
    assert friend.get("/api/me").json()["is_admin"] is False
    assert friend.get("/api/admin/access").status_code == 403
