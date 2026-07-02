"""Tester för projekttyper + prosa-diktering (storyline/synopsis, bok, tal ...)."""
from fastapi.testclient import TestClient

import app.main as main_mod
from app import prose
from app.models import Project, ProseResult

client = TestClient(main_mod.app)


# ---- AI-guiderna ----

def test_every_kind_resolves_to_a_guide():
    for kind in prose.KINDS:
        guide = prose.guide_for(kind)
        assert guide and "TEXTTYP" in guide, kind


def test_screenplay_prose_uses_synopsis_guide():
    # Manusprojektets prosadokument ÄR dess storyline/synopsis.
    assert prose.guide_for("screenplay") == prose.KIND_GUIDES["synopsis"]


def test_unknown_kind_falls_back_to_freetext():
    assert prose.guide_for("nagot-nytt") == prose.KIND_GUIDES["freetext"]


def test_system_text_appends_global_directives():
    from app.models import GlobalSettings
    text = prose._system_text("book", GlobalSettings(directives="Skriv rikssvenska."))
    assert "ROMANPROSA" in text
    assert "Skriv rikssvenska." in text


# ---- apply_prose ----

def test_append_to_empty_document():
    assert prose.apply_prose("", ProseResult(mode="append", text="Hej.")) == "Hej."


def test_append_joins_with_blank_line():
    assert prose.apply_prose("Första stycket.", ProseResult(mode="append", text="Andra stycket.")) == (
        "Första stycket.\n\nAndra stycket."
    )


def test_replace_all_replaces_document():
    assert prose.apply_prose("Gammalt.", ProseResult(mode="replace_all", text="Nytt.")) == "Nytt."


def test_empty_append_leaves_document_untouched():
    assert prose.apply_prose("Kvar.", ProseResult(mode="append", text="  ")) == "Kvar."


def test_empty_replace_all_keeps_document():
    # Trunkerat/tomt AI-svar får ALDRIG nollställa hela dokumentet.
    assert prose.apply_prose("Hela boken.", ProseResult(mode="replace_all", text="")) == "Hela boken."


# ---- modellen ----

def test_project_defaults_backward_compatible():
    # Gamla projektfiler saknar kind/prose – ska få defaultvärden.
    p = Project.model_validate({"id": "x", "title": "Gammalt"})
    assert p.kind == "screenplay"
    assert p.prose == ""


# ---- API ----

def test_create_project_with_kind_and_reject_unknown(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Boken", "kind": "book"}).json()
    assert p["kind"] == "book"
    listed = client.get("/api/projects").json()
    mine = next(x for x in listed if x["id"] == p["id"])
    assert mine["kind"] == "book" and mine["words"] == 0
    assert client.post("/api/projects", json={"title": "X", "kind": "hologram"}).status_code == 400
    client.delete(f"/api/projects/{p['id']}")


def test_prose_saved_via_put(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Talet", "kind": "speech"}).json()
    r = client.put(f"/api/projects/{p['id']}", json={"prose": "Kära bröllopsgäster."}).json()
    assert r["prose"] == "Kära bröllopsgäster."
    client.delete(f"/api/projects/{p['id']}")


def test_dictate_prose_endpoint_appends_then_replaces(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Syn", "kind": "synopsis"}).json()

    monkeypatch.setattr(
        main_mod.prose_mod, "dictate_prose",
        lambda *a, **k: ProseResult(mode="append", text="ANNA kliver av tåget.", summary="La till ett stycke"),
    )
    r = client.post(f"/api/projects/{p['id']}/dictate-prose", json={"text": "..."}).json()
    assert r["project"]["prose"] == "ANNA kliver av tåget."
    assert r["summary"] == "La till ett stycke"

    monkeypatch.setattr(
        main_mod.prose_mod, "dictate_prose",
        lambda *a, **k: ProseResult(mode="append", text="Hon ser sig omkring.", summary=""),
    )
    r = client.post(f"/api/projects/{p['id']}/dictate-prose", json={"text": "..."}).json()
    assert r["project"]["prose"] == "ANNA kliver av tåget.\n\nHon ser sig omkring."

    monkeypatch.setattr(
        main_mod.prose_mod, "dictate_prose",
        lambda *a, **k: ProseResult(mode="replace_all", text="Allt nytt.", summary="Skrev om"),
    )
    r = client.post(f"/api/projects/{p['id']}/dictate-prose", json={"text": "..."}).json()
    assert r["project"]["prose"] == "Allt nytt."
    client.delete(f"/api/projects/{p['id']}")


def test_dictate_prose_snapshots_version_and_restore_recovers(monkeypatch):
    # Ett replace_all som skriver om hela dokumentet ska gå att återställa via versioner.
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Boken", "kind": "book"}).json()
    client.put(f"/api/projects/{p['id']}", json={"prose": "Originaltexten."})

    monkeypatch.setattr(
        main_mod.prose_mod, "dictate_prose",
        lambda *a, **k: ProseResult(mode="replace_all", text="Allt omskrivet.", summary=""),
    )
    r = client.post(f"/api/projects/{p['id']}/dictate-prose", json={"text": "..."}).json()
    assert r["project"]["prose"] == "Allt omskrivet."

    versions = client.get(f"/api/projects/{p['id']}/versions").json()["versions"]
    assert versions, "en auto-version ska ha sparats före omskrivningen"
    r2 = client.post(f"/api/projects/{p['id']}/versions/{versions[0]['id']}/restore").json()
    assert r2["project"]["prose"] == "Originaltexten."
    client.delete(f"/api/projects/{p['id']}")


def test_prose_project_export_is_plain_text(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Talet", "kind": "speech"}).json()
    client.put(f"/api/projects/{p['id']}", json={"prose": "Kära vänner."})
    res = client.post(f"/api/projects/{p['id']}/export")
    assert res.status_code == 200
    assert "text/plain" in res.headers["content-type"]
    assert ".txt" in res.headers["content-disposition"]
    assert "Kära vänner." in res.text
    client.delete(f"/api/projects/{p['id']}")


def test_import_rejected_for_prose_project(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Boken", "kind": "book"}).json()
    res = client.post(
        f"/api/projects/{p['id']}/import",
        files={"file": ("test.fountain", b"INT. RUM - DAG\n\nText.", "text/plain")},
    )
    assert res.status_code == 400
    client.delete(f"/api/projects/{p['id']}")


def test_shared_view_includes_kind_and_prose(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    p = client.post("/api/projects", json={"title": "Pitchen", "kind": "pitch"}).json()
    client.put(f"/api/projects/{p['id']}", json={"prose": "En hisspresentation."})
    token = client.post(f"/api/projects/{p['id']}/share").json()["token"]
    shared = client.get(f"/api/shared/{token}").json()
    assert shared["kind"] == "pitch"
    assert shared["prose"] == "En hisspresentation."
    client.delete(f"/api/projects/{p['id']}")


def test_delete_project_removes_versions_comments_and_share(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    from app import store
    p = client.post("/api/projects", json={"title": "Hemligt"}).json()
    client.post(f"/api/projects/{p['id']}/versions", json={"label": "v1"})
    client.post(f"/api/projects/{p['id']}/comments", json={"text": "anteckning"})
    token = client.post(f"/api/projects/{p['id']}/share").json()["token"]
    client.delete(f"/api/projects/{p['id']}")
    assert store.list_versions("local", p["id"]) == []
    assert store.list_comments("local", p["id"]) == []
    assert store.resolve_share(token) is None


def test_screenplay_dictation_sees_prose_synopsis(monkeypatch):
    # Manus-AI:n ska få storyline/synopsis-dokumentet som kontext.
    from app import analyze
    from app.models import Project as P
    proj = P(id="x", prose="ANNA reser till havet.")
    content = analyze._dictate_user_content(proj, "ny diktering")
    assert "ANNA reser till havet." in content
    assert "storyline/synopsis-dokument" in content
