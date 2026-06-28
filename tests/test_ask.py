"""Test för AI-assistenten 'Fråga manuset' (fritextsvar)."""
from fastapi.testclient import TestClient

import app.main as main_mod

client = TestClient(main_mod.app)


def test_ask_returns_answer(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    proj = client.post("/api/projects", json={"title": "Fråga"}).json()
    client.put(f"/api/projects/{proj['id']}", json={"elements": [
        {"id": 0, "type": "scene_heading", "text": "INT. KÖK - DAG"},
        {"id": 1, "type": "action", "text": "Anna lagar mat."},
    ]})
    monkeypatch.setattr(main_mod.analyze_mod, "ask", lambda *a, **k: "Manuset har 1 scen.")

    r = client.post(f"/api/projects/{proj['id']}/ask", json={"question": "Hur många scener?"})
    assert r.status_code == 200
    assert r.json()["answer"] == "Manuset har 1 scen."

    # tom fråga avvisas
    assert client.post(f"/api/projects/{proj['id']}/ask", json={"question": "  "}).status_code == 400

    client.delete(f"/api/projects/{proj['id']}")
