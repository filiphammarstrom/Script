"""Tester för revideringsläget (förslag på ändringar av befintligt manus)."""
from fastapi.testclient import TestClient

from app import main as main_mod
from app.main import app
from app.models import EditOp, ReviseResult

client = TestClient(app)


def test_revise_models_validate():
    r = ReviseResult.model_validate(
        {
            "operations": [
                {"op": "replace", "target_id": 3, "text": "ny replik", "reason": "bytte"},
                {"op": "delete", "target_id": 5},
                {"op": "insert_after", "target_id": 2, "type": "action", "text": "Hon reser sig."},
            ],
            "summary": "tre ändringar",
        }
    )
    assert [o.op for o in r.operations] == ["replace", "delete", "insert_after"]
    assert r.operations[2].type == "action"


def test_revise_missing_project_404():
    res = client.post("/api/projects/finns-inte/revise", json={"instruction": "x"})
    assert res.status_code == 404


def test_revise_endpoint_returns_proposed_ops(monkeypatch):
    """Endpoint:en returnerar förslagen (utan att tillämpa) – AI-anropet stubbas."""
    p = client.post("/api/projects", json={"title": "Rev"}).json()
    canned = ReviseResult(
        operations=[EditOp(op="delete", target_id=0, reason="tas bort")],
        summary="tar bort en rad",
    )
    monkeypatch.setattr(main_mod.analyze_mod, "revise", lambda *a, **k: canned)
    res = client.post(f"/api/projects/{p['id']}/revise", json={"instruction": "ta bort första"})
    assert res.status_code == 200
    body = res.json()
    assert body["summary"] == "tar bort en rad"
    assert body["operations"][0]["op"] == "delete"
    # projektet ska vara oförändrat (revise tillämpar inget på servern)
    proj = client.get(f"/api/projects/{p['id']}").json()
    assert proj["elements"] == []
