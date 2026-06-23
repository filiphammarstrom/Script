"""AI-steget: tolka dikterad/transkriberad text till strukturerad manusrepresentation.

All "upplärning" av AI:n bor i SYSTEM_RULES nedan, plus de globala instruktionerna
(bas-AI), projektets kontext, projektets instruktioner och story-bibeln. Modellen
tvingas svara via verktyget `emit_screenplay` så att vi alltid får giltig JSON som
matchar AnalyzeResult.
"""
from __future__ import annotations

import json
import os

import anthropic

from app.models import AnalyzeResult, GlobalSettings, Project

DEFAULT_MODEL = os.environ.get("SCRIPT_MODEL", "claude-sonnet-4-6")

SYSTEM_RULES = """Du är en expert på manusförfattande som omvandlar dikterad eller transkriberad text till ett korrekt formaterat filmmanus.

Du får rå text – ibland med talar-etiketter (t.ex. "Speaker 1:"), ibland helt utan. Detektera själv vilket och anpassa dig. Returnera en strukturerad lista av manuselement enligt verktyget. Följ dessa regler strikt:

FORMAT OCH ELEMENTTYPER
- Elementtyper: scene_heading, action, character, dialogue, parenthetical, transition, general.
- Scenrubrik skrivs INT./EXT. PLATS – TID (t.ex. "INT. KÖK – DAG"). Skapa rätt scenrubrik när dikteringen antyder ny plats eller tid ("nån kommer in i huset" → "INT. HUSET – DAG" om inget annat framgår).
- character = karaktärsnamn i VERSALER på egen rad, direkt före repliken.
- dialogue = det som sägs. parenthetical = kort leveransanvisning inom parentes.

HANTVERK – VISA, BERÄTTA INTE
- Action beskriver ENDAST det som syns eller hörs. Skriv ALDRIG inre tillstånd ("hon blir glad", "han känner sig nervös"). Skriv det observerbara i stället ("hon ler", "hon skiner upp", "han trummar med fingrarna").

KONTEXT OCH ATTRIBUERING
- Avgör vem som säger vad i dialog mellan flera personer utifrån sammanhanget.
- Håll namn på karaktärer och platser KONSEKVENTA med story-bibeln du får. Använd samma stavning och samma scenrubrik-slugs som redan etablerats.

DIKTERINGSSPRÅK VS INNEHÅLL
- Känn igen metaspråk/redigeringskommandon från den som dikterar ("nej, ändra det till...", "ta bort förra repliken", "stryk det", "egentligen ska hon säga...") och tolka dem som REDIGERING – de ska aldrig hamna som repliker eller action.
- Skilj den dikterandes egna staklingar, omtagningar och utfyllnad ("öh", "alltså vänta", felstarter) – som tas bort – från när en KARAKTÄR avsiktligt stakar sig som en del av repliken (behålls).

LUCKOR – FYLL UTAN ATT FABULERA
- Fyll bara små luckor som tydligt följer av kontexten. Hitta ALDRIG på händelser, repliker eller karaktärer själv.
- När något genuint saknas: skapa ett element med is_gap=true och en kort beskrivning av vad som saknas, i stället för att gissa.

SPRÅK
- Bevara innehållets språk EXAKT som standard. Ett projekt kan vara FLERSPRÅKIGT – olika karaktärer eller repliker kan vara på olika språk samtidigt. Normalisera ALDRIG till ett enda språk på eget initiativ; behåll varje element på sitt språk.
- Upptäck när den som dikterar växlar språk mitt i (metakommandon kan vara på ett språk och dialog på ett annat) och hantera det rätt – tolka inte ett språkbyte som brus.
- ÖVERSÄTTNING: översätt inte på eget initiativ. Men OM instruktionerna eller ett dikteringskommando ber om det (t.ex. "översätt dialogen till engelska", "skriv scenanvisningarna på svenska") ska du översätta just de delar som efterfrågas och behålla resten.
- SPRÅK PER KARAKTÄR: notera i story-bibeln (Character.languages) vilka språk varje karaktär talar. Om en ny replik för en karaktär är på ett språk som krockar med vad som etablerats (t.ex. karaktären talar bara svenska men repliken är på engelska), flagga det som en clarification (möjlig feldiktering) i stället för att tyst acceptera.

STORY-BIBEL (AI:NS MINNE)
- I story_bible_updates lägger du till NYA eller ändrade karaktärer, platser och fakta du lärt dig av den här texten, så att projektet successivt blir mer konsekvent. Upprepa inte sådant som redan står i bibeln oförändrat.

OSÄKERHET
- När du är osäker: sätt confidence till "medium" eller "low" på elementet och lägg en konkret fråga i clarifications som pekar på elementets id. Gissa aldrig tyst.

NUMRERING
- Numrera new_elements från 0 och uppåt. clarifications.element_id refererar till dessa id:n.
"""

_TOOL = {
    "name": "emit_screenplay",
    "description": "Returnera den strukturerade manusrepresentationen för den givna texten.",
    "input_schema": AnalyzeResult.model_json_schema(),
}


def _system_blocks(global_settings: GlobalSettings) -> list[dict]:
    """Systemprompt = inbyggda grundregler + användarens bas-AI-regler.

    Markeras för prompt-caching så att en stor, stabil regeluppsättning blir billig
    att skicka med vid varje analys."""
    text = SYSTEM_RULES
    if global_settings.directives.strip():
        text += (
            "\n\n# ANVÄNDARENS EGNA GLOBALA REGLER (bas-AI)\n"
            + global_settings.directives.strip()
        )
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _user_content(project: Project, text: str) -> str:
    parts: list[str] = []
    if project.context.strip():
        parts.append("# Projektkontext / synopsis\n" + project.context.strip())
    if project.directives.strip():
        parts.append("# Projektets instruktioner\n" + project.directives.strip())
    parts.append(
        "# Story-bibel (håll konsekvent, bygg vidare)\n"
        + project.story_bible.model_dump_json(indent=2)
    )
    tail = project.elements[-40:]
    if tail:
        parts.append(
            "# Hittills i manuset (de senaste elementen – fortsätt härifrån)\n"
            + json.dumps(
                [e.model_dump() for e in tail], ensure_ascii=False, indent=2
            )
        )
    parts.append("# Ny dikterad/transkriberad text att tolka\n" + text)
    return "\n\n".join(parts)


def analyze(
    project: Project,
    text: str,
    global_settings: GlobalSettings,
    model: str | None = None,
) -> AnalyzeResult:
    """Kör Claude och returnera den strukturerade manusrepresentationen.

    Kräver att ANTHROPIC_API_KEY finns i miljön.
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=16000,
        system=_system_blocks(global_settings),
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "emit_screenplay"},
        messages=[{"role": "user", "content": _user_content(project, text)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return AnalyzeResult.model_validate(block.input)
    raise RuntimeError("Modellen returnerade ingen strukturerad output (inget tool_use).")
