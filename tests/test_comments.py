"""Tester för kommentarer (lägg till, lista, ta bort)."""
from fastapi.testclient import TestClient

import app.main as main_mod

client = TestClient(main_mod.app)


def test_add_list_delete_comment(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    proj = client.post("/api/projects", json={"title": "Kom"}).json()
    pid = proj["id"]

    assert client.get(f"/api/projects/{pid}/comments").json()["comments"] == []

    r = client.post(f"/api/projects/{pid}/comments", json={"text": "Fixa scen 2", "scene": 2}).json()
    assert len(r["comments"]) == 1
    c = r["comments"][0]
    assert c["text"] == "Fixa scen 2" and c["scene"] == 2 and c["author"]

    # tom kommentar → 400
    assert client.post(f"/api/projects/{pid}/comments", json={"text": "  "}).status_code == 400

    after = client.delete(f"/api/projects/{pid}/comments/{c['id']}").json()
    assert after["comments"] == []

    client.delete(f"/api/projects/{pid}")
