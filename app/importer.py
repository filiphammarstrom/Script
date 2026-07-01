"""Importera befintliga manus → vår strukturerade representation (list[dict]).

Stödjer Final Draft (FDX, XML) och Fountain (.fountain/.txt). Returnerar en lista
med {"type", "text"} som main.py numrerar och lägger in i projektet. Ren parsning,
ingen AI – enhetstestbar utan nätverk.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# FDX Paragraph Type → vår elementtyp (se app/fdx.py för motsatt riktning).
FDX_TO_TYPE = {
    "Scene Heading": "scene_heading",
    "Action": "action",
    "Character": "character",
    "Dialogue": "dialogue",
    "Parenthetical": "parenthetical",
    "Transition": "transition",
    "General": "general",
    "New Act": "new_act",
    "End of Act": "end_of_act",
}

_SCENE_RE = re.compile(r"^(INT|EXT|EST|INT\.?/EXT|EXT\.?/INT|I/E)[.\s]", re.IGNORECASE)


def _paragraph_to_item(para) -> dict | None:
    """En enda <Paragraph> → vårt element. Bara DIREKTA <Text>-barn läses (inte
    t.ex. nästlade ScriptNote-paragrafer), annars skulle deras text hänga med."""
    ptype = para.get("Type", "Action")
    text = "".join(node.text or "" for node in para.findall("Text")).strip()
    if not text:
        return None
    item = {"type": FDX_TO_TYPE.get(ptype, "action"), "text": text}
    number = para.get("Number")
    if number and ptype == "Scene Heading":
        item["scene_number"] = number
    return item


def from_fdx(xml_text: str) -> list[dict]:
    """Final Draft XML → element. Läser bara manuskroppen (<Content>), inte titelsidan.

    Dual Dialogue (<Paragraph Type="General"><DualDialogue>...) packas upp: de
    nästlade Character/Dialogue-paragraferna blir vanliga element märkta dual=True,
    i stället för att omslagets egen (tomma) text skulle läsas som ett eget element.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Ogiltig FDX-fil: {exc}")
    content = root.find("Content")
    top_level = list(content) if content is not None else list(root)
    out: list[dict] = []
    for para in top_level:
        if para.tag != "Paragraph":
            continue
        dual_wrap = para.find("DualDialogue")
        if dual_wrap is not None:
            for inner in dual_wrap.findall("Paragraph"):
                item = _paragraph_to_item(inner)
                if item:
                    item["dual"] = True
                    out.append(item)
            continue
        item = _paragraph_to_item(para)
        if item:
            out.append(item)
    return out


def _is_scene(s: str) -> bool:
    return bool(_SCENE_RE.match(s))


def from_fountain(text: str) -> list[dict]:
    """Fountain (plain text) → element. Praktisk delmängd av Fountain-syntaxen."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)
    out: list[dict] = []
    in_dialogue = False
    i = 0
    while i < n:
        s = lines[i].strip()
        if not s:
            in_dialogue = False
            i += 1
            continue
        prev_blank = i == 0 or lines[i - 1].strip() == ""
        next_blank = i + 1 >= n or lines[i + 1].strip() == ""

        # Tvingade markörer (Fountain): . scenrubrik, > övergång, @ karaktär, ! action
        if s.startswith(".") and not s.startswith(".."):
            out.append({"type": "scene_heading", "text": s[1:].strip()}); in_dialogue = False; i += 1; continue
        if s.startswith(">") and s.endswith("<"):
            out.append({"type": "action", "text": s[1:-1].strip()}); in_dialogue = False; i += 1; continue
        if s.startswith(">"):
            out.append({"type": "transition", "text": s[1:].strip()}); in_dialogue = False; i += 1; continue
        if s.startswith("@"):
            out.append({"type": "character", "text": s[1:].strip()}); in_dialogue = True; i += 1; continue
        if s.startswith("!"):
            out.append({"type": "action", "text": s[1:].strip()}); in_dialogue = False; i += 1; continue

        if _is_scene(s):
            out.append({"type": "scene_heading", "text": s}); in_dialogue = False; i += 1; continue
        if s == s.upper() and s.endswith("TO:"):
            out.append({"type": "transition", "text": s}); in_dialogue = False; i += 1; continue
        if in_dialogue and s.startswith("(") and s.endswith(")"):
            out.append({"type": "parenthetical", "text": s}); i += 1; continue
        # Karaktärsreplik: VERSALER, föregås av tomrad, följs av text (inte tomrad).
        if prev_blank and not next_blank and s == s.upper() and re.search(r"[A-ZÅÄÖ]", s) and not s.endswith(":"):
            out.append({"type": "character", "text": s}); in_dialogue = True; i += 1; continue
        if in_dialogue:
            out.append({"type": "dialogue", "text": s}); i += 1; continue
        out.append({"type": "action", "text": s}); i += 1
    return out


def parse_screenplay(filename: str, text: str) -> list[dict]:
    """Välj parser efter filändelse: .fdx → Final Draft, annars Fountain/plain text."""
    if (filename or "").lower().endswith(".fdx"):
        return from_fdx(text)
    return from_fountain(text)
