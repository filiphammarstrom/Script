"""Tester för dikteringsläget: en ruta som lägger till, infogar, ändrar och tar bort."""
from fastapi.testclient import TestClient

import app.main as main_mod
from app import store
from app.models import DictateOp, DictateResult, NewElement, Project, ScreenplayElement

client = TestClient(main_mod.app)


def _elements():
    return [
        {"id": 0, "type": "scene_heading", "text": "INT. KÖK - DAG"},
        {"id": 1, "type": "action", "text": "Bobo lagar mat."},
        {"id": 2, "type": "scene_heading", "text": "EXT. GATA - DAG"},
        {"id": 3, "type": "dialogue", "text": "Hej."},
    ]


def _project():
    return Project(id="x", elements=[ScreenplayElement(**e) for e in _elements()])


def test_append_adds_at_end_with_fresh_id():
    p = _project()
    res = DictateResult(operations=[DictateOp(op="append", elements=[NewElement(type="action", text="Slut.")])])
    p, pending = store.apply_dictation(p, res)
    assert p.elements[-1].text == "Slut."
    assert p.elements[-1].id == 4
    assert pending == []


def test_insert_after_scene_places_between_scenes():
    p = _project()
    res = DictateResult(operations=[
        DictateOp(op="insert_after_scene", after_scene=1,
                  elements=[NewElement(type="scene_heading", text="INT. HALL - DAG")]),
    ])
    p, _ = store.apply_dictation(p, res)
    texts = [e.text for e in p.elements]
    # Infogad efter scen 1:s sista element, före scen 2:s rubrik
    assert texts == ["INT. KÖK - DAG", "Bobo lagar mat.", "INT. HALL - DAG", "EXT. GATA - DAG", "Hej."]


def test_replace_is_pending_until_approved():
    p = _project()
    res = DictateResult(operations=[DictateOp(op="replace", target_id=3, text="Hejsan.")])
    p, pending = store.apply_dictation(p, res)
    assert len(pending) == 1                 # modifierande -> väntar
    assert p.elements[3].text == "Hej."      # inte ändrad än
    store.apply_edits(p, pending)
    assert p.elements[3].text == "Hejsan."


def test_delete_is_pending_then_removes():
    p = _project()
    p, pending = store.apply_dictation(p, DictateResult(operations=[DictateOp(op="delete", target_id=1)]))
    assert len(pending) == 1
    store.apply_edits(p, pending)
    assert [e.id for e in p.elements] == [0, 2, 3]


def test_dictate_endpoint_applies_additive_returns_pending(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    proj = client.post("/api/projects", json={"title": "Dikt"}).json()
    client.put(f"/api/projects/{proj['id']}", json={"elements": _elements()})

    canned = DictateResult(
        operations=[
            DictateOp(op="append", elements=[NewElement(type="action", text="Hon går ut.")]),
            DictateOp(op="replace", target_id=3, text="Hejsan."),
        ],
        summary="La till en rad och föreslog en ändring.",
    )
    monkeypatch.setattr(main_mod.analyze_mod, "dictate", lambda *a, **k: canned)

    r = client.post(f"/api/projects/{proj['id']}/dictate", json={"text": "..."}).json()
    assert any(e["text"] == "Hon går ut." for e in r["project"]["elements"])  # additivt tillämpat
    assert len(r["pending_ops"]) == 1                                          # ändring väntar
    assert r["summary"]

    r2 = client.post(f"/api/projects/{proj['id']}/apply-edits",
                     json={"operations": r["pending_ops"]}).json()
    assert any(e["text"] == "Hejsan." for e in r2["project"]["elements"])      # ändring godkänd

    client.delete(f"/api/projects/{proj['id']}")
