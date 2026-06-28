"""Tester för versionshistorik (spara, lista, återställ)."""
from fastapi.testclient import TestClient

import app.main as main_mod

client = TestClient(main_mod.app)


def _elements(text):
    return [{"id": 0, "type": "scene_heading", "text": text}]


def test_save_list_and_restore_version(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    proj = client.post("/api/projects", json={"title": "Ver"}).json()
    pid = proj["id"]

    client.put(f"/api/projects/{pid}", json={"elements": _elements("INT. KÖK - DAG")})
    r = client.post(f"/api/projects/{pid}/versions", json={"label": "Första"}).json()
    assert r["version"]["label"] == "Första"
    assert any(v["label"] == "Första" for v in r["versions"])
    vid = r["version"]["id"]

    # ändra manuset, återställ sedan till den sparade versionen
    client.put(f"/api/projects/{pid}", json={"elements": _elements("EXT. GATA - NATT")})
    restored = client.post(f"/api/projects/{pid}/versions/{vid}/restore", json={}).json()
    assert restored["project"]["elements"][0]["text"] == "INT. KÖK - DAG"
    # en "Före återställning"-version ska ha skapats (ångerbar)
    assert any(v["label"] == "Före återställning" for v in restored["versions"])

    # okänd version → 404
    assert client.post(f"/api/projects/{pid}/versions/000/restore", json={}).status_code == 404

    client.delete(f"/api/projects/{pid}")
