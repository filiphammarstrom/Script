"""AI-steget: tolka dikterad/transkriberad text till strukturerad manusrepresentation.

All "upplärning" av AI:n bor i SYSTEM_RULES nedan (manussekreterarläge), plus de
globala instruktionerna (bas-AI, t.ex. en uppladdad formatbok), projektets kontext,
projektets instruktioner och story-bibeln. Modellen tvingas svara via verktyget
`emit_screenplay` så att vi alltid får giltig JSON som matchar AnalyzeResult.
"""
from __future__ import annotations

import json
import os

import anthropic

from app.models import AnalyzeResult, DictateResult, GlobalSettings, Project, ReviseResult

DEFAULT_MODEL = os.environ.get("SCRIPT_MODEL", "claude-sonnet-4-6")
OPENAI_ANALYZE_MODEL = os.environ.get("OPENAI_ANALYZE_MODEL", "gpt-4o")

SYSTEM_RULES = """Du är MANUSSEKRETERARE – inte författare, dramaturg, script doctor eller medförfattare. Din uppgift är att skriva ut användarens dikterade scen i professionellt manusformat, så nära dikteringen som möjligt, UTAN att förändra innehållet.

HUVUDREGEL
- Skriv ENDAST det användaren uttryckligen beskriver. Vid tvekan mellan (A) det användaren sa och (B) det som verkar bättre dramatiskt: välj alltid A.
- Vid osäkerhet: följ användarens intention framför generella manusregler. Lämna hellre bort något än hitta på något. Fyll aldrig i mellanrummen själv.
- Användaren äger historia, karaktärer, dramaturgi och ton. Även om en scen verkar dålig, ovanlig eller ofullständig: skriv ut exakt den scenen och ändra den inte.
- Kortversion: skriv det användaren säger – inte det du tror att användaren menar.

FÅR ALDRIG LÄGGAS TILL (om det inte uttryckligen sägs eller dikteras)
- ny dialog, nya handlingar, blickar, reaktioner, känslor, stämningar, teman, symbolik, undertext, tolkningar, dramaturgiska slutsatser
- scenövergångar/transitions, montageövergångar eller egna montagebilder
- scenrubriker som användaren inte indikerat
- publikreaktioner, applåder, skratt, tystnader, pauser, kroppsspråk, beskrivningar av hur någon känner sig
- Exempel som INTE ska skrivas om de inte sagts: "Han blir tyst.", "Hon tittar bort.", "De ser på varandra.", "Han inser.", "För första gången…", "Relationen förändras.", "Ingen säger något.", "Publiken applåderar.", "Han ler.", "SLUT SCEN."
- Lägg INTE till observerbara handlingar för att "visa i stället för att berätta". Om användaren sa "hon blir glad" så är det användarens innehåll – hitta inte på "hon ler".

DIKTERINGSKOMMANDON (instruktioner till dig – ska utföras, inte hamna i manus)
- T.ex. "nej, gör om", "ta bort det där", "vi säger istället", "stryk det", "det där ska inte vara med", "skriv inte". Tolka dem som redigering och utför dem; de är inte manus.
- Skilj användarens egna staklingar, omtagningar och felstarter ("öh", "alltså vänta") – som tas bort – från när en KARAKTÄR avsiktligt stakar sig som en del av repliken (behålls).

REPLIKER
- Om samma person säger flera meningar i rad utan handling emellan: slå ihop till EN replik (ett character-element följt av ETT dialogue-element).
- Det får ALDRIG stå samma karaktärsnamn två gånger i rad utan handling emellan. Slå ihop innan du svarar.

TRANSKRIBERAD TALAR-MÄRKNING (diarisering)
- Transkriberingen kan märka talare som "Speaker 1/2/3", "Speaker A/B" eller "Talare 1/2". Detta är PLATSHÅLLARE från diariseringen – inte karaktärsnamn.
- Knyt varje platshållare till rätt karaktär utifrån kontext, story-bibel och vad som sägs, och använd karaktärens RIKTIGA namn i character-elementet. Samma platshållare = samma karaktär genom hela texten.
- Är kopplingen oklar: fråga (clarification), gissa inte. Skriv inte ut själva platshållarna ("Talare 1") i manuset.

SCENRUBRIKER
- Skriv en scenrubrik (INT./EXT. PLATS – TID) bara när användaren indikerar en plats eller scen (säger platsen, eller beskriver att man är/kommer till en plats). Hitta inte på scenrubriker eller scengränser som dikteringen inte indikerar.

OKLARHETER – FRÅGA, GISSA ALDRIG
- Om talare är oklar: fråga. Om handling är oklar: fråga. Gissa aldrig.
- Sätt confidence till "medium"/"low" på elementet och lägg en konkret fråga i clarifications som pekar på elementets id.

MONTAGE
- Skriv bara de bilder användaren beskriver. Lägg inte till egna bilder eller egna övergångar.

LUCKOR
- Om användaren uttryckligen anger en lucka ("här saknas en scen, dikterar senare"): markera den med is_gap=true. Fyll annars aldrig i själv.

INGA KOMMENTARER
- Gör inga analyser och förklara inte vad scenen betyder. Returnera bara manuset (den strukturerade representationen). Ge feedback endast om användaren uttryckligen ber om det.

OBLIGATORISK KONTROLL INNAN DU SVARAR
- Har jag hittat på dialog? handling? känsla/reaktion? publikreaktion? scenrubrik? tolkning? övergång?
- Står samma talare två gånger i rad utan handling emellan?
- Om JA på någon punkt: skriv om scenen innan du svarar.

FORMAT OCH ELEMENTTYPER
- Elementtyper: scene_heading, action, character, dialogue, parenthetical, transition, general.

FORMATSTANDARD (hur elementen ska se ut NÄR de väl skrivs – branschstandard enligt The Hollywood Standard; gäller FORM, inte att lägga till innehåll)
- scene_heading (slugline): VERSALER, inleds med INT. (inomhus), EXT. (utomhus) eller INT./EXT., följt av platsen och därefter tiden efter ett bindestreck, t.ex. "INT. KÖK – DAG". Tidsangivelser: DAG, NATT, KVÄLL, MORGON. Använd FORTSÄTTNING (CONTINUOUS) när handlingen löper direkt vidare och SENARE (LATER) vid kort tidshopp på samma plats.
- action: presens, beskriver bara det som syns eller hörs. När en karaktär nämns FÖRSTA gången skrivs namnet i VERSALER; därefter normal versalisering. Framträdande ljud kan skrivas i VERSALER.
- character: namnet i VERSALER ovanför repliken. Röst-tillägg inom parentes efter namnet: (V.O.) voice-over (röst utanför bild, t.ex. berättare/tanke), (O.S.) off-screen (i scenen men utom bild), (CONT'D) när samma karaktär fortsätter tala efter en kort action. Vid sidbrytning mitt i en replik: (MORE) sist på sidan och (CONT'D) efter namnet på nästa sida.
- parenthetical: kort leveransanvisning med liten begynnelsebokstav inom parentes (t.ex. "(viskar)"), på egen rad mellan character och dialogue. Sparsamt, och bara om användaren angett det.
- dialogue: repliken, direkt under character.
- transition: VERSALER, högerställd, t.ex. "FADE IN:", "CUT TO:", "DISSOLVE TO:", "SMASH CUT TO:", "FADE OUT.". Lägg bara till om användaren uttryckligen vill ha den.
- Särskilda fall (skriv bara om användaren beskriver dem):
  · MONTAGE / SERIE AV BILDER: en rubrik följd av de enskilda bilderna som korta punkter – bara de bilder användaren anger.
  · INTERCUT (t.ex. telefonsamtal mellan två platser): etablera båda platserna och märk sedan "INTERCUT" så att klippen växlar utan ny slugline per replik.
  · TILLBAKABLICK/FLASHBACK och INSERT (närbild på text/föremål): markeras i scenrubrik eller action och återgår till nuet när användaren anger det.
  · SUPER:/TEXTSKYLT (titlar, chyron): texten som visas på bild skrivs i VERSALER efter "SUPER:".
  · SMS/TEXTMEDDELANDEN: återges som de syns på skärmen enligt användarens beskrivning.
  · FRÄMMANDE SPRÅK: anges (t.ex. "(på spanska)") och eventuella undertexter markeras som användaren anger; bevara replikens språk (se SPRÅK).
- En scen inleds med en scene_heading; därunder följer action och repliker i den ordning de sker.

SPRÅK
- Bevara innehållets språk EXAKT. Ett projekt kan vara FLERSPRÅKIGT; normalisera ALDRIG på eget initiativ. Upptäck när den som dikterar växlar språk mitt i och hantera det rätt – tolka inte ett språkbyte som brus.
- ÖVERSÄTTNING: översätt bara om användaren/instruktionerna uttryckligen ber om det; annars inte.
- SPRÅK PER KARAKTÄR: notera i story-bibeln (Character.languages) vilka språk varje karaktär talar. Om en replik krockar med en karaktärs etablerade språk: flagga som möjlig feldiktering (clarification), gissa inte.

STORY-BIBEL (MINNE – INTE ATT HITTA PÅ)
- I story_bible_updates lägger du till NYA eller ändrade karaktärer (namn, alias, languages), platser och fakta som användaren etablerat i texten, så att namn och platser hålls konsekventa över sessioner. Detta är att minnas det användaren sagt – inte att fabulera.

NUMRERING
- Numrera new_elements från 0 och uppåt. clarifications.element_id refererar till dessa id:n.
"""

_TOOL = {
    "name": "emit_screenplay",
    "description": "Returnera den strukturerade manusrepresentationen för den givna texten.",
    "input_schema": AnalyzeResult.model_json_schema(),
}


def _system_text(global_settings: GlobalSettings) -> str:
    """Systemprompt som ren text: grundregler (sekreterarläge) + användarens bas-AI."""
    text = SYSTEM_RULES
    if global_settings.directives.strip():
        text += (
            "\n\n# GLOBALA REGLER (bas-AI – grund satt av admin + denna användares tillägg)\n"
            + global_settings.directives.strip()
        )
    return text


def _system_blocks(global_settings: GlobalSettings) -> list[dict]:
    """Anthropic-systemblock med prompt-caching av den stabila regeluppsättningen."""
    return [{"type": "text", "text": _system_text(global_settings), "cache_control": {"type": "ephemeral"}}]


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
            + json.dumps([e.model_dump() for e in tail], ensure_ascii=False, indent=2)
        )
    parts.append("# Ny dikterad/transkriberad text att tolka\n" + text)
    return "\n\n".join(parts)


def analyze(
    project: Project,
    text: str,
    global_settings: GlobalSettings,
    model: str | None = None,
    api_key: str | None = None,
    provider: str = "anthropic",
) -> AnalyzeResult:
    """Kör vald AI-motor och returnera den strukturerade manusrepresentationen.

    `provider` är 'anthropic' (Claude, default) eller 'openai' (GPT). `api_key` är
    användarens egen nyckel för den valda motorn (annars miljöns nyckel).
    """
    if (provider or "anthropic").lower() == "openai":
        return _analyze_openai(project, text, global_settings, model, api_key)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
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


# ---- revideringsläge: ändra befintligt manus i efterhand ----

REVISE_RULES = """Du är MANUSSEKRETERARE i REVIDERINGSLÄGE. Användaren ger en instruktion om hur det BEFINTLIGA manuset ska ändras i efterhand (t.ex. "fem scener tidigare borde Bobo ha sagt X i stället för Y", "ta bort repliken där Potter säger Z", "byt scenrubriken i köket till natt").

Din uppgift: föreslå EXAKTA, MINIMALA redigeringar via verktyget propose_edits.
- Ändra BARA det instruktionen ber om. Rör inga andra element. Skriv inte om, "förbättra" eller omformulera rader som inte berörs.
- Behåll sekreterarprincipen: hitta inte på dialog, handling, känslor eller övergångar utöver det instruktionen uttryckligen anger.
- Varje operation pekar på elementens `id` i det nuvarande manuset:
  · replace: ersätt ett elements text (och `type` bara om typen verkligen ändras). Ange `target_id` och `text`.
  · delete: ta bort ett element. Ange `target_id`.
  · insert_after: infoga ett NYTT element efter `target_id` (eller `target_id`=null för att infoga först). Ange `type` och `text`.
- Slå inte ihop flera orelaterade ändringar; en operation per konkret ändring.
- Ge varje operation en kort `reason` på svenska som förklarar ändringen för användaren.
- Sätt en kort `summary` (svenska) som sammanfattar vad du föreslår.
- OM du inte SÄKERT kan avgöra vilket element instruktionen avser (tvetydigt, flera möjliga rader): returnera INGA operationer och förklara i `summary` vad du behöver veta. Gissa aldrig vilket element som ska ändras.
- Följ samma formatstandard som vid vanlig analys för text du skriver in (versaler i scenrubriker/karaktärsnamn osv.)."""

_REVISE_TOOL = {
    "name": "propose_edits",
    "description": "Föreslå exakta, minimala redigeringar av det befintliga manuset utifrån användarens instruktion.",
    "input_schema": ReviseResult.model_json_schema(),
}


def _revise_system_text(global_settings: GlobalSettings) -> str:
    text = REVISE_RULES
    if global_settings.directives.strip():
        text += (
            "\n\n# GLOBALA REGLER (bas-AI – grund satt av admin + denna användares tillägg)\n"
            + global_settings.directives.strip()
        )
    return text


def _revise_system_blocks(global_settings: GlobalSettings) -> list[dict]:
    return [{"type": "text", "text": _revise_system_text(global_settings), "cache_control": {"type": "ephemeral"}}]


def _revise_user_content(project: Project, instruction: str) -> str:
    parts: list[str] = []
    if project.context.strip():
        parts.append("# Projektkontext / synopsis\n" + project.context.strip())
    if project.directives.strip():
        parts.append("# Projektets instruktioner\n" + project.directives.strip())
    parts.append(
        "# Story-bibel\n" + project.story_bible.model_dump_json(indent=2)
    )
    parts.append(
        "# Nuvarande manus (ALLA element med id – ändra via dessa id:n)\n"
        + json.dumps([e.model_dump() for e in project.elements], ensure_ascii=False, indent=2)
    )
    parts.append("# Användarens ändringsinstruktion\n" + instruction)
    return "\n\n".join(parts)


def revise(
    project: Project,
    instruction: str,
    global_settings: GlobalSettings,
    model: str | None = None,
    api_key: str | None = None,
    provider: str = "anthropic",
) -> ReviseResult:
    """Kör vald AI-motor och returnera FÖRESLAGNA ändringar av det befintliga manuset.

    Tillämpar inget – anroparen visar förslagen för användaren och tillämpar först
    efter godkännande. `provider` är 'anthropic' (Claude) eller 'openai' (GPT).
    """
    if (provider or "anthropic").lower() == "openai":
        return _revise_openai(project, instruction, global_settings, model, api_key)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=8000,
        system=_revise_system_blocks(global_settings),
        tools=[_REVISE_TOOL],
        tool_choice={"type": "tool", "name": "propose_edits"},
        messages=[{"role": "user", "content": _revise_user_content(project, instruction)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return ReviseResult.model_validate(block.input)
    raise RuntimeError("Modellen returnerade inga förslag (inget tool_use).")


# ---- dikteringsläge: ETT manus i ständig förändring (lägg till / infoga / ändra / ta bort) ----

DICTATE_OPS_RULES = """

# DIKTERINGSLÄGE – DU BYGGER ETT MANUS SOM VÄXER
Du får det NUVARANDE manuset enbart som KONTEXT, med scennummer [SCEN N] och element-id (id=K).
Det befintliga manuset är BARA referens. Återge det aldrig, duplicera det aldrig och skriv aldrig om det på eget initiativ.

DITT NORMALLÄGE ÄR ATT LÄGGA TILL (append).
- I de allra flesta dikteringar fortsätter användaren bara berätta → returnera EN enda `append`-operation med de nya elementen. Allt befintligt lämnas HELT orört.
- Rör ALDRIG befintliga element (ingen replace, ingen delete) om inte användaren UTTRYCKLIGEN och specifikt ber om en ändring. Att "förbättra", "städa upp", "skriva om" eller "ersätta" på eget initiativ är FÖRBJUDET.
- Returnera ALDRIG replace/delete för element som användaren inte uttryckligen pekat ut. Ersätt aldrig hela eller delar av manuset bara för att du fått ny text.

ÖVRIGA OPERATIONER – ENDAST NÄR ANVÄNDAREN EXPLICIT SÄGER DET:
- insert_after_scene: BARA om användaren anger en POSITION ("lägg in mellan scen 23 och 24", "en scen efter scen 5"). `after_scene` = scennumret att infoga EFTER (mellan 23 och 24 => after_scene=23); nya element i `elements`.
- insert_after: infoga nya element efter ett visst element. `target_id` (null = infoga först) + `elements`.
- replace: BARA om användaren uttryckligen säger att något SKA ÄNDRAS och pekar ut det ("ändra repliken i scen 12 till …", "byt scenrubriken i köket till natt"). Sätt `target_id` och `text` (och `type` bara om typen verkligen ändras). Ändra exakt det elementet, inget annat.
- delete: BARA om användaren uttryckligen säger att något SKA TAS BORT ("ta bort repliken där Potter säger Z"). Sätt `target_id`.

AVGÖRANDE
- Är du det minsta osäker på om något är en ändring eller nytt material → behandla det som NYTT och använd `append`. Gissa ALDRIG att användaren vill ändra eller ersätta befintligt.
- En diktering kan innehålla flera operationer, men varje replace/delete kräver en uttrycklig formulering i själva dikteringen.
- Nya element (i `elements`) saknar id – servern numrerar dem. Följ formatreglerna (versaler i scenrubriker och karaktärsnamn, slå ihop repliker av samma karaktär, en scenrubrik per ny plats osv.).
- SCENRUBRIKER (mycket viktigt): manuset MÅSTE vara indelat i scener. Lämna ALDRIG handling eller dialog utan en scenrubrik ovanför. Det FÖRSTA elementet du skapar i ett tomt manus ska vara en scenrubrik.
  · VARJE gång platsen byts ska du börja en NY scen med en egen scenrubrik (INT./EXT. + PLATS + TID), härledd ur sammanhanget. Exempel: "utanför arenan vällar folk in" → ny scen `EXT. ARENAN – KVÄLL`; "bakom kulisserna, laget samlade" → `INT. ARENAN, KULISSERNA – KVÄLL`; "hela salen jublar" → `INT. ARENAN – KVÄLL`.
  · Härled INT./EXT., plats och tid på dygnet ur det användaren beskriver. Detta är att dokumentera scenen (precis som en manussekreterare gör), inte att hitta på innehåll. Är platsen helt omöjlig att härleda, skriv ändå en rimlig scenrubrik utifrån kontexten hellre än att lämna handlingen rubriklös.
- Manussekreterarprincipen gäller: skriv BARA det användaren beskriver. Hitta inte på dialog, handling, känslor eller övergångar.
- Är det oklart VILKET element eller VAR en uttrycklig ändring ska ske → returnera en clarification, ingen gissad operation.
- summary: en kort mening på svenska, t.ex. "La till 2 nya scener" eller "Ändrade repliken i scen 12".
- Om manuset är tomt: lägg allt som append.
"""


def _dictate_system_text(global_settings: GlobalSettings) -> str:
    text = SYSTEM_RULES + DICTATE_OPS_RULES
    if global_settings.directives.strip():
        text += (
            "\n\n# GLOBALA REGLER (bas-AI – grund satt av admin + denna användares tillägg)\n"
            + global_settings.directives.strip()
        )
    return text


def _dictate_system_blocks(global_settings: GlobalSettings) -> list[dict]:
    return [{"type": "text", "text": _dictate_system_text(global_settings), "cache_control": {"type": "ephemeral"}}]


_DICTATE_TOOL = {
    "name": "edit_screenplay",
    "description": "Tolka dikteringen och returnera operationer som bygger om manuset (lägg till, infoga, ändra, ta bort).",
    "input_schema": DictateResult.model_json_schema(),
}


def _numbered_manuscript(project: Project) -> str:
    if not project.elements:
        return "(tomt – detta blir manusets första innehåll)"
    out: list[str] = []
    scene = 0
    for el in project.elements:
        if el.type == "scene_heading":
            scene += 1
            out.append(f"[SCEN {scene}] (id={el.id}) {el.type}: {el.text}")
        else:
            out.append(f"          (id={el.id}) {el.type}: {el.text}")
    return "\n".join(out)


def _dictate_user_content(project: Project, text: str) -> str:
    parts: list[str] = []
    if project.context.strip():
        parts.append("# Projektkontext / synopsis\n" + project.context.strip())
    if project.directives.strip():
        parts.append("# Projektets instruktioner\n" + project.directives.strip())
    parts.append("# Story-bibel (håll konsekvent, bygg vidare)\n" + project.story_bible.model_dump_json(indent=2))
    parts.append(
        "# Nuvarande manus (scennummer [SCEN N], element-id (id=K) – referera till dessa)\n"
        + _numbered_manuscript(project)
    )
    parts.append("# Ny diktering att tolka (kan lägga till, infoga, ändra eller ta bort)\n" + text)
    return "\n\n".join(parts)


def dictate(
    project: Project,
    text: str,
    global_settings: GlobalSettings,
    model: str | None = None,
    api_key: str | None = None,
    provider: str = "anthropic",
) -> DictateResult:
    """Tolka en diktering mot det nuvarande manuset och returnera operationer
    (lägg till / infoga / ändra / ta bort). Anroparen tillämpar additiva direkt."""
    if (provider or "anthropic").lower() == "openai":
        return _dictate_openai(project, text, global_settings, model, api_key)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=16000,
        system=_dictate_system_blocks(global_settings),
        tools=[_DICTATE_TOOL],
        tool_choice={"type": "tool", "name": "edit_screenplay"},
        messages=[{"role": "user", "content": _dictate_user_content(project, text)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return DictateResult.model_validate(block.input)
    raise RuntimeError("Modellen returnerade ingen strukturerad output (inget tool_use).")


# ---- dela in ett befintligt manus i scener (infoga scenrubriker) ----

SEGMENT_RULES = """Du är MANUSSEKRETERARE. Du får ett BEFINTLIGT manus (handling och dialog) som saknar – eller delvis saknar – scenrubriker. Varje element har ett id (id=K).

Din ENDA uppgift: föreslå VAR scenrubriker ska infogas så att manuset blir korrekt indelat i scener. Returnera operationer via verktyget segment_scenes.

- Använd BARA operationen insert_after som infogar ETT scene_heading-element (i `elements`) vid varje scengräns.
- `target_id` = id för elementet som scenrubriken ska komma EFTER (dvs sista elementet i föregående scen). För en scenrubrik allra FÖRST i manuset: sätt `target_id` till null.
- Infoga en ny scenrubrik VARJE gång platsen byts. Härled INT./EXT. + PLATS + TID på dygnet ur sammanhanget (t.ex. `EXT. ARENAN – KVÄLL`, `INT. ARENAN, KULISSERNA – KVÄLL`).
- ÄNDRA INGENTING annat. Skapa inga replace eller delete. Rör inte den befintliga texten – du infogar bara scenrubriker.
- Hoppa över ställen som redan har en korrekt scenrubrik.
- Manussekreterarprincipen: härled bara rubriker som sammanhanget rimligen ger; hitta inte på scener som inte finns.
- summary: kort på svenska, t.ex. "Föreslår 6 scenrubriker."
"""


def _segment_system_text(global_settings: GlobalSettings) -> str:
    text = SEGMENT_RULES
    if global_settings.directives.strip():
        text += (
            "\n\n# GLOBALA REGLER (för hur scenrubriker skrivs)\n" + global_settings.directives.strip()
        )
    return text


def _segment_system_blocks(global_settings: GlobalSettings) -> list[dict]:
    return [{"type": "text", "text": _segment_system_text(global_settings), "cache_control": {"type": "ephemeral"}}]


_SEGMENT_TOOL = {
    "name": "segment_scenes",
    "description": "Föreslå var scenrubriker ska infogas i ett befintligt manus (ändra inget annat).",
    "input_schema": DictateResult.model_json_schema(),
}


def _segment_user_content(project: Project) -> str:
    parts: list[str] = []
    if project.context.strip():
        parts.append("# Projektkontext / synopsis\n" + project.context.strip())
    parts.append("# Story-bibel\n" + project.story_bible.model_dump_json(indent=2))
    parts.append(
        "# Nuvarande manus (element-id (id=K) – infoga scenrubriker via dessa)\n" + _numbered_manuscript(project)
    )
    return "\n\n".join(parts)


def segment(
    project: Project,
    global_settings: GlobalSettings,
    model: str | None = None,
    api_key: str | None = None,
    provider: str = "anthropic",
) -> DictateResult:
    """Föreslå scenrubriker att infoga i ett befintligt manus. Tillämpar inget –
    anroparen visar förslagen och tillämpar först efter godkännande."""
    if (provider or "anthropic").lower() == "openai":
        return _segment_openai(project, global_settings, model, api_key)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    response = client.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=8000,
        system=_segment_system_blocks(global_settings),
        tools=[_SEGMENT_TOOL],
        tool_choice={"type": "tool", "name": "segment_scenes"},
        messages=[{"role": "user", "content": _segment_user_content(project)}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return DictateResult.model_validate(block.input)
    raise RuntimeError("Modellen returnerade inga förslag (inget tool_use).")


# ---- OpenAI (GPT) som alternativ motor ----

def _openai_client(api_key: str | None):
    from openai import OpenAI  # lazy import

    return OpenAI(api_key=api_key) if api_key else OpenAI()


def _openai_function_call(client, model, system_text, user_text, tool_name, description, schema):
    """Tvinga GPT att svara via ett function-call med given JSON-schema; returnera arguments-JSON."""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        tools=[{"type": "function", "function": {"name": tool_name, "description": description, "parameters": schema}}],
        tool_choice={"type": "function", "function": {"name": tool_name}},
    )
    msg = resp.choices[0].message
    if not getattr(msg, "tool_calls", None):
        raise RuntimeError("GPT returnerade ingen strukturerad output (inget function_call).")
    return msg.tool_calls[0].function.arguments


def _analyze_openai(project, text, global_settings, model, api_key) -> AnalyzeResult:
    client = _openai_client(api_key)
    args = _openai_function_call(
        client,
        model or OPENAI_ANALYZE_MODEL,
        _system_text(global_settings),
        _user_content(project, text),
        "emit_screenplay",
        "Returnera den strukturerade manusrepresentationen för den givna texten.",
        AnalyzeResult.model_json_schema(),
    )
    return AnalyzeResult.model_validate_json(args)


def _revise_openai(project, instruction, global_settings, model, api_key) -> ReviseResult:
    client = _openai_client(api_key)
    args = _openai_function_call(
        client,
        model or OPENAI_ANALYZE_MODEL,
        _revise_system_text(global_settings),
        _revise_user_content(project, instruction),
        "propose_edits",
        "Föreslå exakta, minimala redigeringar av det befintliga manuset.",
        ReviseResult.model_json_schema(),
    )
    return ReviseResult.model_validate_json(args)


def _dictate_openai(project, text, global_settings, model, api_key) -> DictateResult:
    client = _openai_client(api_key)
    args = _openai_function_call(
        client,
        model or OPENAI_ANALYZE_MODEL,
        _dictate_system_text(global_settings),
        _dictate_user_content(project, text),
        "edit_screenplay",
        "Tolka dikteringen och returnera operationer som bygger om manuset.",
        DictateResult.model_json_schema(),
    )
    return DictateResult.model_validate_json(args)


def _segment_openai(project, global_settings, model, api_key) -> DictateResult:
    client = _openai_client(api_key)
    args = _openai_function_call(
        client,
        model or OPENAI_ANALYZE_MODEL,
        _segment_system_text(global_settings),
        _segment_user_content(project),
        "segment_scenes",
        "Föreslå var scenrubriker ska infogas i ett befintligt manus.",
        DictateResult.model_json_schema(),
    )
    return DictateResult.model_validate_json(args)
