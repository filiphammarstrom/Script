"""Enhetstest för FDX-generatorn. Kräver ingen API-nyckel.

Använder en lättviktig dataklass i stället för Pydantic-modellen så att testet kan
köras fristående och bara verifierar FDX-utdata.
"""
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from app.fdx import to_fdx


@dataclass
class E:
    type: str
    text: str
    is_gap: bool = False
    scene_number: str | None = None
    dual: bool = False


def test_basic_structure_and_types():
    elements = [
        E("scene_heading", "INT. KÖK – DAG"),
        E("action", "Anna kommer in."),
        E("character", "ANNA"),
        E("parenthetical", "(leende)"),
        E("dialogue", "Hej & välkommen <hem>!"),
        E("transition", "CUT TO:"),
    ]
    xml = to_fdx(elements)

    assert xml.startswith('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>')
    root = ET.fromstring(xml)
    assert root.tag == "FinalDraft"
    assert root.attrib["DocumentType"] == "Script"

    paragraphs = root.find("Content").findall("Paragraph")
    assert [p.attrib["Type"] for p in paragraphs] == [
        "Scene Heading",
        "Action",
        "Character",
        "Parenthetical",
        "Dialogue",
        "Transition",
    ]
    # Text med specialtecken ska escapas i XML men parsas tillbaka korrekt.
    assert paragraphs[4].find("Text").text == "Hej & välkommen <hem>!"


def test_gap_renders_as_marked_action():
    root = ET.fromstring(to_fdx([E("action", "övergång saknas här", is_gap=True)]))
    para = root.find("Content").find("Paragraph")
    assert para.attrib["Type"] == "Action"
    assert para.find("Text").text.startswith("[LUCKA")


def test_unknown_type_falls_back_to_general():
    root = ET.fromstring(to_fdx([E("weird", "x")]))
    assert root.find("Content").find("Paragraph").attrib["Type"] == "General"


def test_empty_screenplay_is_valid_xml():
    root = ET.fromstring(to_fdx([]))
    assert root.find("Content") is not None
    assert root.find("Content").findall("Paragraph") == []


def test_title_page_is_added_when_metadata_given():
    xml = to_fdx([E("action", "x")], title="Mitt Manus", author="Anna A", contact="anna@x.se")
    root = ET.fromstring(xml)
    tp = root.find("TitlePage")
    assert tp is not None
    texts = [p.find("Text").text or "" for p in tp.find("Content").findall("Paragraph")]
    assert "MITT MANUS" in texts
    assert "Written by" in texts
    assert "Anna A" in texts
    assert "anna@x.se" in texts


def test_no_title_page_without_metadata():
    root = ET.fromstring(to_fdx([E("action", "x")]))
    assert root.find("TitlePage") is None


def test_scenes_auto_number_sequentially():
    elements = [
        E("scene_heading", "INT. KÖK – DAG"),
        E("action", "x"),
        E("scene_heading", "EXT. GATA – NATT"),
    ]
    paragraphs = ET.fromstring(to_fdx(elements)).find("Content").findall("Paragraph")
    scene_paras = [p for p in paragraphs if p.attrib["Type"] == "Scene Heading"]
    assert [p.attrib["Number"] for p in scene_paras] == ["1", "2"]


def test_locked_scene_number_overrides_auto_numbering():
    elements = [
        E("scene_heading", "INT. KÖK – DAG"),
        E("scene_heading", "INT. HALL – DAG", scene_number="12A"),
        E("scene_heading", "EXT. GATA – NATT"),
    ]
    paragraphs = ET.fromstring(to_fdx(elements)).find("Content").findall("Paragraph")
    scene_paras = [p for p in paragraphs if p.attrib["Type"] == "Scene Heading"]
    # Det låsta numret ersätter bara sin egen scen; övriga fortsätter räkna som vanligt.
    assert [p.attrib["Number"] for p in scene_paras] == ["1", "12A", "3"]


def test_new_act_and_end_of_act_paragraph_types():
    elements = [E("new_act", "AKT ETT"), E("action", "x"), E("end_of_act", "SLUT AKT ETT")]
    types = [p.attrib["Type"] for p in ET.fromstring(to_fdx(elements)).find("Content").findall("Paragraph")]
    assert types == ["New Act", "Action", "End of Act"]


def test_dual_dialogue_wraps_consecutive_dual_elements():
    elements = [
        E("scene_heading", "INT. KÖK – DAG"),
        E("character", "ANNA", dual=True),
        E("dialogue", "Hej!", dual=True),
        E("character", "BEA", dual=True),
        E("dialogue", "Hej själv!", dual=True),
        E("action", "De ler mot varandra."),
    ]
    root = ET.fromstring(to_fdx(elements))
    top = root.find("Content").findall("Paragraph")
    # Scenrubrik, ett hopslaget Dual Dialogue-omslag, sen action – inte fyra separata paragrafer.
    assert [p.attrib["Type"] for p in top] == ["Scene Heading", "General", "Action"]
    dual_wrap = top[1]
    dd = dual_wrap.find("DualDialogue")
    assert dd is not None
    inner_types = [p.attrib["Type"] for p in dd.findall("Paragraph")]
    assert inner_types == ["Character", "Dialogue", "Character", "Dialogue"]
    inner_texts = [p.find("Text").text for p in dd.findall("Paragraph")]
    assert inner_texts == ["ANNA", "Hej!", "BEA", "Hej själv!"]


def test_dual_dialogue_does_not_get_a_locked_scene_number_attribute():
    # Dual dialogue-omslaget är typ "General", inte "Scene Heading" – ska aldrig få Number.
    elements = [E("character", "A", dual=True), E("dialogue", "x", dual=True)]
    para = ET.fromstring(to_fdx(elements)).find("Content").find("Paragraph")
    assert "Number" not in para.attrib
