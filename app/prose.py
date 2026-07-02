"""Prosa-diktering: ljud/text -> löpande text i stället för manusformat.

Samma sekreterarprincip som manusdikteringen (app/analyze.py) men för LÖPANDE
TEXT: storyline/synopsis, bok, tal, pitch, artikel, sångtext eller fri text.
Varje projekttyp har en egen AI-guide (systemprompt-tillägg) som styr formen –
innehållet är alltid användarens.

Ett screenplay-projekt har OCKSÅ ett prosadokument (sitt storyline/synopsis,
se Project.prose) – där används synopsis-guiden. Modellen tvingas svara via
verktyget `emit_prose` så att vi alltid får giltig JSON (ProseResult).
"""
from __future__ import annotations

import anthropic

from app.analyze import (
    DEFAULT_MODEL,
    OPENAI_ANALYZE_MODEL,
    _numbered_manuscript,
    _openai_client,
    _openai_function_call,
)
from app.models import GlobalSettings, Project, ProseResult

# Projekttyperna som kan väljas vid "Nytt projekt". label/icon speglas i
# frontend (KIND_INFO i app.js) – håll dem i synk. "screenplay" är manus-
# editorn som förut; övriga är prosadokument med varsin AI-guide nedan.
KINDS: dict[str, dict] = {
    "screenplay": {"label": "Filmmanus (screenplay)", "icon": "🎬"},
    "synopsis": {"label": "Storyline / Synopsis", "icon": "📖"},
    "book": {"label": "Bok / Roman", "icon": "📕"},
    "speech": {"label": "Tal", "icon": "🎤"},
    "pitch": {"label": "Pitch", "icon": "🪧"},
    "article": {"label": "Artikel / Blogg", "icon": "📰"},
    "lyrics": {"label": "Sångtext / Poesi", "icon": "🎵"},
    "freetext": {"label": "Fri text", "icon": "📄"},
}

# Gemensam grund för ALL prosa-diktering – samma anda som SYSTEM_RULES i
# analyze.py (sekreterare, inte författare), men för löpande text.
PROSE_RULES = """Du är DIKTERINGSSEKRETERARE för löpande text – inte författare, redaktör eller medförfattare. Din uppgift är att skriva ut användarens diktering som ren, välformaterad prosa, så nära dikteringen som möjligt, UTAN att förändra innehållet.

HUVUDREGEL
- Skriv ENDAST det användaren uttryckligen säger. Vid tvekan mellan (A) det användaren sa och (B) det som verkar bättre skrivet: välj alltid A.
- Hitta ALDRIG på innehåll: inga egna meningar, exempel, tolkningar, slutsatser, rubriker eller övergångar som inte dikterats.
- Ta bort staklingar, omtagningar, "öh"/"alltså vänta" och falska starter – det är diktering, inte innehåll.
- DIKTERINGSKOMMANDON ("nej stryk det", "nytt stycke", "ny rad", "vi tar om det", "punkt") är instruktioner till dig – utför dem, skriv aldrig ut dem.
- Rätta uppenbara taligenkänningsfel utifrån sammanhanget, men ändra aldrig innebörd, ordval eller ton i övrigt.
- Behåll språket EXAKT (svenska förblir svenska, engelska förblir engelska). Översätt aldrig oombett.
- Dela upp i stycken där talet naturligt byter ämne eller där användaren anger det.

OPERATIONER (fältet `mode`)
- Normalfall: mode="append" – returnera BARA den nya texten; den läggs till sist i dokumentet. Återge ALDRIG befintlig text i append-läge.
- mode="replace_all": ENDAST när användaren uttryckligen dikterat en ändring av BEFINTLIG text ("ändra stycket om X", "skriv om inledningen", "ta bort sista stycket"). Returnera då HELA det uppdaterade dokumentet med den begärda ändringen gjord och ALLT annat ordagrant bevarat.
- Är du det minsta osäker på om något är en ändring eller ny text: behandla det som NY text (append).
- summary: en kort mening på svenska om vad du gjorde, t.ex. "La till två stycken" eller "Skrev om inledningen"."""

# Typspecifika AI-guider ("initial AI guide" per projekttyp). Läggs efter
# PROSE_RULES i systemprompten. Screenplay-projektens prosadokument använder
# synopsis-guiden (dokumentet ÄR manusets storyline/synopsis).
KIND_GUIDES: dict[str, str] = {
    "synopsis": """# TEXTTYP: STORYLINE / SYNOPSIS (film/serie)
- Berättande prosa i PRESENS, tredje person, kronologiskt – så som handlingen utspelar sig på duken.
- Inget manusformat: inga scenrubriker (INT./EXT.), inga repliknamn, inga övergångar. Dikterad dialog vävs in som indirekt tal, eller kort citat inom citattecken när ordalydelsen är själva poängen.
- KARAKTÄRSNAMN skrivs i VERSALER första gången de nämns (branschkonvention), därefter normalt.
- Fokus på vad som HÄNDER: handling, mål, hinder, vändpunkter – i den ordning användaren berättar dem. Lägg inte till egna tolkningar, teman eller dramaturgisk analys.
- Har projektet ett manus och användaren uttryckligen ber dig sammanfatta det ("gör ett synopsis av manuset", "sammanfatta akt 1") får du skriva utifrån manusinnehållet – fortfarande utan egna påhitt.""",
    "book": """# TEXTTYP: BOK / ROMANPROSA
- Skönlitterär löpande prosa med styckeindelning.
- Behåll användarens berättarperspektiv och tempus EXAKT som dikterat (jag/tredje person, presens/preteritum). Byt aldrig på eget initiativ.
- Dialog sätts med talstreck (–) på egen rad enligt svensk romankonvention – om inte användaren själv använder citattecken eller skriver på ett språk med annan konvention.
- Anföringsverb ("sa hon", "viskade han") bara när användaren dikterar dem.
- Kapitelrubriker och avsnittsbrytningar bara när användaren anger dem ("nytt kapitel", "kapitel tre: Havet").
- Att gestalta ("show, don't tell") på eget initiativ är FÖRBJUDET: säger användaren "hon blir arg" skriver du det – hitta inte på hur ilskan syns.""",
    "speech": """# TEXTTYP: TAL
- Text som ska FRAMFÖRAS MUNTLIGT: skriv i talarens röst ("jag", direkt tilltal till publiken) precis som dikterat.
- Talets rytm skapas med styckeindelning och interpunktion – ändra aldrig användarens formuleringar för att "förbättra flytet".
- Markera naturliga pauser med styckebrytning där användaren pausar eller anger det.
- Retoriska grepp (upprepningar, treled, retoriska frågor) BEHÅLLS exakt som dikterade – "städa" aldrig bort en avsiktlig upprepning.
- Hälsningsfras, tack och avslutning bara om användaren dikterar dem.""",
    "pitch": """# TEXTTYP: PITCH
- Kort, säljande text om ett projekt (film, serie, bok, produkt, företag). Varje mening ska bära – men det är ANVÄNDARENS meningar som ska bära, inte dina.
- Strukturera under korta VERSAL-rubriker (t.ex. LOGLINE, HANDLING, MÅLGRUPP, TON, VARFÖR NU) endast när användaren dikterar innehåll som uttryckligen hör till en sådan del eller ber om strukturen – annars ett eller ett par tighta stycken.
- Skärp ALDRIG formuleringar på eget initiativ och komprimera inte bort innehåll.
- Jämförelser ("X möter Y") och superlativ bara om de dikterats.""",
    "article": """# TEXTTYP: ARTIKEL / BLOGG
- Redaktionell löpande text med tydlig styckeindelning.
- Rubrik och ingress bara om användaren dikterar dem eller uttryckligen ber om dem.
- Mellanrubriker vid ämnesbyten ENDAST när användaren anger dem.
- Citat sätts inom citattecken med attribution exakt som dikterat ("enligt X", "säger Y").
- Behåll användarens ton – personlig blogg förblir personlig, nyhetstext förblir neutral. Normalisera inte.""",
    "lyrics": """# TEXTTYP: SÅNGTEXT / POESI
- RADBRYTNINGAR ÄR INNEHÅLL: bryt rad där användaren anger det ("ny rad") eller där versens rytm uppenbart kräver det, och gruppera i strofer med blankrad emellan.
- Märk delar (VERS 1, REFRÄNG, BRYGGA) bara om användaren benämner dem.
- Rim, upprepningar, ofullständiga meningar och "fel" grammatik är AVSIKTLIGA – bevara dem exakt.
- Ingen interpunktions-städning: skiljetecken bara där de dikterats eller är uppenbart avsedda.
- Versaler/gemener: följ användarens uttalade önskemål; annars normal skrivning.""",
    "freetext": """# TEXTTYP: FRI TEXT
- Minsta möjliga inblandning: gör dikteringen till ren, läsbar text med skiljetecken och stycken. Inget annat.
- Ingen struktur, inga rubriker, ingen formatering utöver stycken – om inte användaren uttryckligen dikterar det.""",
}


def guide_for(kind: str) -> str:
    """AI-guiden för en projekttyp. Screenplay-projektens prosadokument är dess
    storyline/synopsis, så de får synopsis-guiden. Okänd typ -> fri text."""
    if kind == "screenplay":
        return KIND_GUIDES["synopsis"]
    return KIND_GUIDES.get(kind, KIND_GUIDES["freetext"])


def _system_text(kind: str, global_settings: GlobalSettings) -> str:
    text = PROSE_RULES + "\n\n" + guide_for(kind)
    if global_settings.directives.strip():
        text += (
            "\n\n# GLOBALA REGLER (bas-AI – grund satt av admin + denna användares tillägg)\n"
            + global_settings.directives.strip()
        )
    return text


_TOOL = {
    "name": "emit_prose",
    "description": "Returnera dikteringen som löpande text (append eller replace_all).",
    "input_schema": ProseResult.model_json_schema(),
}


def _user_content(project: Project, text: str) -> str:
    parts: list[str] = []
    if project.context.strip():
        parts.append("# Projektkontext\n" + project.context.strip())
    if project.directives.strip():
        parts.append("# Projektets instruktioner\n" + project.directives.strip())
    bible = project.story_bible
    if bible.characters or bible.locations or bible.notes:
        parts.append("# Story-bibel (håll namn och fakta konsekventa)\n" + bible.model_dump_json(indent=2))
    # I ett screenplay-projekt är prosadokumentet manusets synopsis – ge AI:n
    # själva manuset som referens (t.ex. "sammanfatta akt 1 till synopsiset").
    if project.kind == "screenplay" and project.elements:
        parts.append("# Projektets manus (referens – återge inte, hitta inte på utöver)\n" + _numbered_manuscript(project))
    parts.append(
        "# Nuvarande dokument\n"
        + (project.prose.strip() or "(tomt – detta blir dokumentets första innehåll)")
    )
    parts.append("# Ny diktering att tolka\n" + text)
    return "\n\n".join(parts)


def dictate_prose(
    project: Project,
    text: str,
    global_settings: GlobalSettings,
    model: str | None = None,
    api_key: str | None = None,
    provider: str = "anthropic",
) -> ProseResult:
    """Tolka en diktering mot prosadokumentet och returnera ProseResult.
    Anroparen tillämpar resultatet med apply_prose() och sparar."""
    system_text = _system_text(project.kind, global_settings)
    user = _user_content(project, text)
    if (provider or "anthropic").lower() == "openai":
        client = _openai_client(api_key)
        args = _openai_function_call(
            client, model or OPENAI_ANALYZE_MODEL, system_text, user,
            "emit_prose", "Returnera dikteringen som löpande text.",
            ProseResult.model_json_schema(),
        )
        return ProseResult.model_validate_json(args)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "emit_prose"},
        messages=[{"role": "user", "content": user}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return ProseResult.model_validate(block.input)
    raise RuntimeError("Modellen returnerade ingen strukturerad output (inget tool_use).")


def apply_prose(current: str, result: ProseResult) -> str:
    """Tillämpa ett ProseResult på dokumentet: append läggs till sist med
    blankrad emellan; replace_all ersätter hela dokumentet.

    Ett replace_all med TOM text ignoreras (dokumentet behålls): det inträffar
    när modellens svar trunkerats eller den svarat fel – att då nollställa hela
    dokumentet vore oåterkallelig dataförlust för ett ordkommando."""
    text = (result.text or "").strip()
    if result.mode == "replace_all":
        return text if text else current
    if not text:
        return current
    if not current.strip():
        return text
    return current.rstrip() + "\n\n" + text
