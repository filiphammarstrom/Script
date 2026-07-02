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


def test_screenplay_dictation_sees_prose_synopsis(monkeypatch):
    # Manus-AI:n ska få storyline/synopsis-dokumentet som kontext.
    from app import analyze
    from app.models import Project as P
    proj = P(id="x", prose="ANNA reser till havet.")
    content = analyze._dictate_user_content(proj, "ny diktering")
    assert "ANNA reser till havet." in content
    assert "storyline/synopsis-dokument" in content
