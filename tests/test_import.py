"""Tester för import av FDX och Fountain."""
from fastapi.testclient import TestClient

import app.main as main_mod
from app import importer


def test_from_fdx_reads_body_not_titlepage():
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FinalDraft DocumentType="Script" Version="3">'
        "<TitlePage><Content>"
        '<Paragraph Type="Action"><Text>MIN TITEL</Text></Paragraph>'
        "</Content></TitlePage>"
        "<Content>"
        '<Paragraph Type="Scene Heading"><Text>INT. KÖK – DAG</Text></Paragraph>'
        '<Paragraph Type="Action"><Text>Anna kommer in.</Text></Paragraph>'
        '<Paragraph Type="Character"><Text>ANNA</Text></Paragraph>'
        '<Paragraph Type="Dialogue"><Text>Hej.</Text></Paragraph>'
        "</Content></FinalDraft>"
    )
    els = importer.from_fdx(xml)
    assert [e["type"] for e in els] == ["scene_heading", "action", "character", "dialogue"]
    assert els[0]["text"] == "INT. KÖK – DAG"
    # titelsidans text ska INTE ha kommit med
    assert all(e["text"] != "MIN TITEL" for e in els)


def test_from_fdx_strips_parens_from_parenthetical():
    # Final Draft lagrar parenteserna bokstavligen i <Text> – vi lagrar bara innehållet
    # (se app/fdx.py, som lägger dem på igen vid export) så redigeringsrutan kan visa
    # dem som statisk dekoration i stället för redigerbar text.
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FinalDraft DocumentType="Script" Version="3"><Content>'
        '<Paragraph Type="Character"><Text>ANNA</Text></Paragraph>'
        '<Paragraph Type="Parenthetical"><Text>(leende)</Text></Paragraph>'
        '<Paragraph Type="Dialogue"><Text>Hej.</Text></Paragraph>'
        "</Content></FinalDraft>"
    )
    els = importer.from_fdx(xml)
    assert els[1] == {"type": "parenthetical", "text": "leende"}


def test_from_fdx_invalid_raises():
    try:
        importer.from_fdx("inte xml <<<")
        assert False, "borde ha kastat ValueError"
    except ValueError:
        pass


def test_from_fountain_basic():
    text = (
        "INT. KÖK - DAG\n"
        "\n"
        "Anna kommer in.\n"
        "\n"
        "ANNA\n"
        "(leende)\n"
        "Hej där.\n"
        "\n"
        "CUT TO:\n"
    )
    els = importer.from_fountain(text)
    types = [e["type"] for e in els]
    assert types == ["scene_heading", "action", "character", "parenthetical", "dialogue", "transition"]
    # Parenteserna i källan ska strippas bort ur den lagrade texten (se from_fdx-testet).
    assert els[3] == {"type": "parenthetical", "text": "leende"}


def test_from_fountain_forced_markers():
    text = ".EN TVINGAD RUBRIK\n\n@kalle\nNåt jag säger\n\n> SMASH:\n"
    els = importer.from_fountain(text)
    assert els[0] == {"type": "scene_heading", "text": "EN TVINGAD RUBRIK"}
    assert els[1] == {"type": "character", "text": "kalle"}
    assert els[2]["type"] == "dialogue"
    assert els[3] == {"type": "transition", "text": "SMASH:"}


def test_import_endpoint_appends(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    client = TestClient(main_mod.app)
    pid = client.post("/api/projects", json={"title": "Import"}).json()["id"]

    fountain = "INT. PARK - DAG\n\nEn hund springer.\n"
    r = client.post(
        f"/api/projects/{pid}/import",
        files={"file": ("manus.fountain", fountain, "text/plain")},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["added"] == 2
    assert [e["type"] for e in data["project"]["elements"]] == ["scene_heading", "action"]

    # tom/oläsbar fil → 400
    bad = client.post(
        f"/api/projects/{pid}/import",
        files={"file": ("tom.fountain", "   \n\n", "text/plain")},
    )
    assert bad.status_code == 400

    client.delete(f"/api/projects/{pid}")
