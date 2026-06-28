"""Generera Final Draft (.fdx) XML från den strukturerade manusrepresentationen.

Ren funktion utan AI eller externa beroenden – därför enkel att enhetstesta.
Strukturen är verifierad mot ett riktigt FDX-exempel.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable
from xml.sax.saxutils import escape

if TYPE_CHECKING:  # undvik hård import så modulen kan testas fristående
    from app.models import ScreenplayElement

# Våra elementtyper -> FDX Paragraph Type.
_TYPE_MAP = {
    "scene_heading": "Scene Heading",
    "action": "Action",
    "character": "Character",
    "dialogue": "Dialogue",
    "parenthetical": "Parenthetical",
    "transition": "Transition",
    "general": "General",
}

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
    '<FinalDraft DocumentType="Script" Template="No" Version="3">\n'
    "  <Content>\n"
)
_FOOTER = "  </Content>\n</FinalDraft>\n"


def _paragraph(par_type: str, text: str) -> str:
    return f'    <Paragraph Type="{par_type}"><Text>{escape(text)}</Text></Paragraph>\n'


def _centered(text: str) -> str:
    return f'      <Paragraph Alignment="Center"><Text>{escape(text)}</Text></Paragraph>\n'


def _title_page(title: str, author: str, contact: str) -> str:
    """Final Drafts <TitlePage>: titel centrerad, 'Written by' + namn, kontakt nederst."""
    if not (title.strip() or author.strip() or contact.strip()):
        return ""
    paras = [_centered("") for _ in range(8)]
    if title.strip():
        paras.append(_centered(title.strip().upper()))
    if author.strip():
        paras.append(_centered(""))
        paras.append(_centered("Written by"))
        paras.append(_centered(""))
        paras.append(_centered(author.strip()))
    if contact.strip():
        paras.append(_centered(""))
        for line in contact.strip().splitlines():
            paras.append(f'      <Paragraph Alignment="Left"><Text>{escape(line)}</Text></Paragraph>\n')
    return "  <TitlePage>\n    <Content>\n" + "".join(paras) + "    </Content>\n  </TitlePage>\n"


def to_fdx(
    elements: "Iterable[ScreenplayElement]",
    *,
    title: str = "",
    author: str = "",
    contact: str = "",
) -> str:
    """Returnera ett komplett FDX-dokument som sträng.

    `elements` är vilken sekvens som helst av objekt med attributen
    `type`, `text` och (valfritt) `is_gap`. Anges `title`/`author`/`contact`
    läggs en titelsida till.
    """
    body = []
    for el in elements:
        par_type = _TYPE_MAP.get(el.type, "General")
        text = el.text
        if getattr(el, "is_gap", False):
            # En medveten lucka renderas tydligt – aldrig bortfabulerad.
            par_type = "Action"
            text = text if text.strip().startswith("[LUCKA") else f"[LUCKA: {text}]"
        body.append(_paragraph(par_type, text))
    return _HEADER + "".join(body) + "  </Content>\n" + _title_page(title, author, contact) + "</FinalDraft>\n"
