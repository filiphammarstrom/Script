"""Tester för skrivskyddad delning (delningslänk) + tittarkommentarer."""
from fastapi.testclient import TestClient

import app.main as main_mod

client = TestClient(main_mod.app)


def test_share_create_resolve_and_comment(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    proj = client.post("/api/projects", json={"title": "Delat manus"}).json()
    pid = proj["id"]
    client.put(f"/api/projects/{pid}", json={"elements": [
        {"id": 0, "type": "scene_heading", "text": "INT. KÖK – DAG"},
        {"id": 1, "type": "action", "text": "Anna kommer in."},
    ]})

    # ingen delning från start
    assert client.get(f"/api/projects/{pid}/share").json()["token"] is None

    # skapa delning – samma token återanvänds
    token = client.post(f"/api/projects/{pid}/share").json()["token"]
    assert token
    assert client.post(f"/api/projects/{pid}/share").json()["token"] == token
    assert client.get(f"/api/projects/{pid}/share").json()["token"] == token

    # öppna delad vy utan inloggning
    shared = client.get(f"/api/shared/{token}").json()
    assert shared["title"] == "Delat manus"
    assert len(shared["elements"]) == 2
    assert shared["comments"] == []

    # en tittare lämnar en kommentar
    r = client.post(
        f"/api/shared/{token}/comments",
        json={"author": "Regissören", "text": "Bra scen!", "scene": 1},
    ).json()
    assert len(r["comments"]) == 1 and r["comments"][0]["author"] == "Regissören"

    # tom kommentar -> 400
    assert client.post(f"/api/shared/{token}/comments", json={"text": "  "}).status_code == 400

    # gäst utan namn -> "Gäst"
    r2 = client.post(f"/api/shared/{token}/comments", json={"text": "Anonymt"}).json()
    assert any(c["author"] == "Gäst" for c in r2["comments"])

    # ägaren ser tittarnas kommentarer i sin egen lista
    owner_comments = client.get(f"/api/projects/{pid}/comments").json()["comments"]
    assert len(owner_comments) == 2

    # återkalla → länken slutar fungera, ny token vid ny delning
    client.delete(f"/api/projects/{pid}/share")
    assert client.get(f"/api/projects/{pid}/share").json()["token"] is None
    assert client.get(f"/api/shared/{token}").status_code == 404
    assert client.post(f"/api/shared/{token}/comments", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/projects/{pid}/share").json()["token"] != token

    client.delete(f"/api/projects/{pid}")


def test_shared_unknown_token_404(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    assert client.get("/api/shared/finnsinte").status_code == 404
    assert client.get("/api/shared/finnsinte/comments").status_code == 404
    assert client.post("/api/shared/finnsinte/comments", json={"text": "hej"}).status_code == 404
