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
