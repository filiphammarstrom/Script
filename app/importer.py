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
}

_SCENE_RE = re.compile(r"^(INT|EXT|EST|INT\.?/EXT|EXT\.?/INT|I/E)[.\s]", re.IGNORECASE)


def from_fdx(xml_text: str) -> list[dict]:
    """Final Draft XML → element. Läser bara manuskroppen (<Content>), inte titelsidan."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Ogiltig FDX-fil: {exc}")
    content = root.find("Content")
    paras = content.iter("Paragraph") if content is not None else root.iter("Paragraph")
    out: list[dict] = []
    for para in paras:
        ptype = para.get("Type", "Action")
        text = "".join(node.text or "" for node in para.iter("Text")).strip()
        if text:
            out.append({"type": FDX_TO_TYPE.get(ptype, "action"), "text": text})
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
