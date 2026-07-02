"""Generera Final Draft (.fdx) XML från den strukturerade manusrepresentationen.

Ren funktion utan AI eller externa beroenden – därför enkel att enhetstesta.
Strukturen är verifierad mot ett riktigt FDX-exempel.

Dual Dialogue: Final Draft representerar två repliker sida vid sida som en
<Paragraph Type="General"> som omsluter en <DualDialogue>-tagg med de riktiga
Character/Dialogue/Parenthetical-paragraferna nästlade inuti (bekräftat genom att
studera hur andra öppna FDX-parsrar, t.ex. Trelby, tolkar riktiga Final Draft-filer:
en kommentar i dess importkod noterar att "General" har inbäddade Dual
Dialogue-paragrafer). Går Final Draft någonsin ifrån den exakta nästlingen
importeras blocket ändå som vanlig sekventiell dialog – aldrig som trasig XML.
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
    "new_act": "New Act",
    "end_of_act": "End of Act",
}

_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
    '<FinalDraft DocumentType="Script" Template="No" Version="3">\n'
    "  <Content>\n"
)
_FOOTER = "  </Content>\n</FinalDraft>\n"


def _paragraph(par_type: str, text: str, *, number: str | None = None, indent: str = "    ") -> str:
    attr = f' Number="{escape(number)}"' if number else ""
    return f'{indent}<Paragraph Type="{par_type}"{attr}><Text>{escape(text)}</Text></Paragraph>\n'


def _paren_text(par_type: str, text: str) -> str:
    """Vi lagrar Parenthetical-text utan omslutande parenteser (redigeringsrutan
    visar dem som statisk dekoration, se app.js) – Final Draft förväntar sig dem
    dock bokstavligen i <Text>, så de läggs på här vid export."""
    if par_type == "Parenthetical" and text and not (text.startswith("(") and text.endswith(")")):
        return f"({text})"
    return text


def _dual_dialogue(group: "list[ScreenplayElement]") -> str:
    """Slå ihop en sammanhängande grupp dual=True-element till Final Drafts
    <Paragraph Type="General"><DualDialogue>...-omslag (repliker sida vid sida)."""
    inner = "".join(
        _paragraph(
            _TYPE_MAP.get(el.type, "General"),
            _paren_text(_TYPE_MAP.get(el.type, "General"), el.text),
            indent="        ",
        )
        for el in group
    )
    return (
        '    <Paragraph Type="General">\n'
        "      <DualDialogue>\n" + inner + "      </DualDialogue>\n"
        "    </Paragraph>\n"
    )


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

    `elements` är vilken sekvens som helst av objekt med attributen `type`, `text`
    och (valfritt) `is_gap`, `scene_number` och `dual`. Anges `title`/`author`/
    `contact` läggs en titelsida till.

    `scene_number` (bara på scene_heading) låser scenens nummer i exporten i
    stället för den automatiska löpande räkningen. En sammanhängande följd av
    `dual=True`-element (t.ex. karaktär+replik två gånger i rad) exporteras som
    Dual Dialogue – repliker sida vid sida.
    """
    body = []
    scene_no = 0
    els = list(elements)
    i = 0
    while i < len(els):
        el = els[i]
        if getattr(el, "dual", False):
            group = []
            while i < len(els) and getattr(els[i], "dual", False):
                group.append(els[i])
                i += 1
            body.append(_dual_dialogue(group))
            continue
        par_type = _TYPE_MAP.get(el.type, "General")
        text = el.text
        number = None
        if el.type == "scene_heading":
            scene_no += 1
            number = getattr(el, "scene_number", None) or str(scene_no)
        if getattr(el, "is_gap", False):
            # En medveten lucka renderas tydligt – aldrig bortfabulerad.
            par_type = "Action"
            text = text if text.strip().startswith("[LUCKA") else f"[LUCKA: {text}]"
        else:
            text = _paren_text(par_type, text)
        body.append(_paragraph(par_type, text, number=number))
        i += 1
    return _HEADER + "".join(body) + "  </Content>\n" + _title_page(title, author, contact) + "</FinalDraft>\n"
