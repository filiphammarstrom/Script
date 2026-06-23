# Script — transkription → manus (FDX)

Webbapp som omvandlar en **dikterad/transkriberad text** till ett **filmmanus i FDX-format**
(Final Draft XML). En AI (Claude) läser texten, förstår sammanhanget, avgör vem som säger
vad, tillämpar manushantverkets regler och bygger upp manuset. Indata kan vara **text**
(klistra in/transkriberad i annan tjänst) eller en **ljudfil** som transkriberas och
diariseras i molnet (default AssemblyAI) och sedan matar samma pipeline.

## Idé och upplägg

- **Projekt:** varje manus är ett eget projekt (egen JSON-fil i `data/projects/`). Du kan ha
  flera parallellt och återuppta exakt där du var.
- **Story-bibel:** AI:ns "minne" av projektet (karaktärer, alias, språk, platser, fakta). Den
  växer för varje diktering så att namn och platser hålls konsekventa över sessioner.
- **Tre lager av regler/instruktioner till AI:n:**
  1. **Inbyggda grundregler** (`SYSTEM_RULES` i `app/analyze.py`) – alltid på.
  2. **Bas-AI (globala regler)** – dina egna regler för *alla* projekt (t.ex. en formatbok).
     Klistra in eller ladda upp en **PDF/TXT/MD** i appen (texten extraheras automatiskt);
     cachas så att en stor regeluppsättning blir billig per anrop.
  3. **Projektinstruktioner** – regler för ett enskilt manus, uppdateras när som helst.
- **Granskning:** lättviktig och frivillig. AI:n flaggar bara det den var osäker på som
  konkreta frågor – du kan svara eller exportera FDX direkt.

### AI:ns regler – manussekreterarläge (urval)
AI:n är **manussekreterare, inte medförfattare**: skriver bara det användaren dikterar och
lägger inte till dialog, handlingar, känslor, blickar, reaktioner, tolkningar, övergångar eller
scenrubriker som inte sagts. Slår ihop på varandra följande repliker från samma talare till en,
behandlar "nej, gör om"/"stryk det" som instruktioner, frågar vid oklar talare/handling i stället
för att gissa, bevarar språk (flerspråk + styrd översättning), flaggar repliker som krockar med en
karaktärs språk, och ger inga kommentarer efter scenen. Den stora formatboken laddas in i Bas-AI.

## Komma igång

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fyll i ANTHROPIC_API_KEY (och ASSEMBLYAI_API_KEY för ljud)
export $(grep -v '^#' .env | xargs)   # eller sätt ANTHROPIC_API_KEY på annat sätt

uvicorn app.main:app --reload
```

Öppna http://localhost:8000.

Modell: default `claude-sonnet-4-6`. Sätt `SCRIPT_MODEL=claude-opus-4-8` för svårare fall.

## Tester

FDX-generatorn testas utan API-nyckel:

```bash
pytest tests/test_fdx.py
```

## Arbetsflöde i appen

1. (Valfritt) Klistra in/ladda upp dina globala regler i **Bas-AI** och spara.
2. Skapa ett projekt, fyll i synopsis/kontext och ev. projektinstruktioner.
3. Klistra in transkriberad text — **eller** ladda upp en ljudfil och tryck **Transkribera**
   (talar-märkt text fylls i rutan) — och tryck sedan **Analysera**. AI:n bygger på manuset
   och uppdaterar story-bibeln.
4. Svara ev. på AI:ns frågor (frivilligt) eller redigera elementen direkt.
5. **Exportera FDX** och öppna `.fdx`-filen i Final Draft.

## Status / nästa steg

- Async/bakgrundsjobb + statuspoll för lång ljudtranskribering; chunkning av mycket långa transkriptioner.
- Titelsida och avancerade FDX-element.
