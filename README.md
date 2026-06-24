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

**Enklast (macOS): dubbelklicka `start.command`** i projektmappen. Första gången sätts allt upp automatiskt (virtuell miljö + beroenden) och sedan öppnas appen i webbläsaren. Stäng terminalfönstret för att stänga av. Fyll i dina inställningar i `.env` (skapas automatiskt från mallen vid första körningen). Vill du ha en riktig app-ikon: se "App-ikon i Dock" nedan.

Manuellt (alla plattformar):
```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fyll i ANTHROPIC_API_KEY (och ASSEMBLYAI_API_KEY för ljud)
export $(grep -v '^#' .env | xargs)   # eller sätt ANTHROPIC_API_KEY på annat sätt

uvicorn app.main:app --reload
```

Öppna http://localhost:8000.

### App-ikon i Dock (macOS, valfritt)

Vill du starta från en riktig app-ikon i stället för att leta upp `start.command`:

1. Öppna **Automator** → nytt dokument → **Program** (Application).
2. Sök upp **"Kör Skalskript"** (Run Shell Script), dra in den.
3. Klistra in (byt sökväg om mappen ligger någon annanstans):
   `open "$HOME/Script/start.command"`
4. **Spara** som `Script` i mappen Program. Dra ikonen till Dock.

Nu startar ett klick på ikonen appen och öppnar den i webbläsaren.

Modell: default `claude-sonnet-4-6`. Sätt `SCRIPT_MODEL=claude-opus-4-8` för svårare fall.

### Transkribering: moln eller lokalt (gratis)

`TRANSCRIBE_BACKEND` väljer motor:

- `assemblyai` (default) – moln med diarisering (talar-etiketter). Kräver `ASSEMBLYAI_API_KEY`. Kostar per minut.
- `openai` – moln via OpenAI. Kräver `OPENAI_API_KEY`. Modell väljs i appen per uppladdning (eller med `OPENAI_TRANSCRIBE_MODEL`): `gpt-4o-mini-transcribe` (billigast, 1 röst – när bara du dikterar), `gpt-4o-transcribe` (1 röst, hög kvalitet) eller `gpt-4o-transcribe-diarize` (flera talare, ~$0,006/min).
- `local` – lokal **Whisper-CLI** på din egen dator. Gratis, ingen moln-API (ingen diarisering – AI:n attribuerar talare från sammanhang).

Lokalt läge, standard (openai-whisper):

```bash
pip install -U openai-whisper        # ger CLI:n `whisper`
export TRANSCRIBE_BACKEND=local
export WHISPER_MODEL=small           # tiny/base/small/medium/large
```

Annan CLI (t.ex. whisper.cpp) – sätt en kommandomall med platshållarna `{input} {output} {outdir} {model} {language}`:

```bash
export TRANSCRIBE_BACKEND=local
export WHISPER_CMD="whisper-cli -m ~/models/ggml-small.bin -f {input} -otxt -of {output}"
```

Mallen ska skriva transkriptet till `{output}.txt` (annars läses CLI:ns stdout). Samma mekanism kan peka mot en bevakad mapp/skript för appar som MacWhisper.

**Helautomatiskt via en GUI-apps bevakade mapp** (t.ex. MacWhisper / Whisper Transcription) – appen lägger ljudet i mappen och väntar in transkriptet:

```bash
export TRANSCRIBE_BACKEND=watch
export WATCH_IN_DIR="$HOME/ScriptInbox"     # mappen appen bevakar
export WATCH_OUT_DIR="$HOME/ScriptInbox"    # där transkriptet dyker upp (default = IN_DIR)
export WATCH_OUT_EXT=.txt                     # eller .srt (tidskoder rensas)
```

Ställ in transkriberingsappen att bevaka `WATCH_IN_DIR` och spara transkript (samma filnamn) till `WATCH_OUT_DIR`.

I appen kan du även **välja motor per uppladdning** (Lokalt / Bevakad mapp / Moln: OpenAI / Moln: AssemblyAI) och **importera ett färdigt transkript** (`.txt/.srt/.vtt`) från en valfri app – tidskoder i SRT/VTT rensas automatiskt.

Ger din app **talar-etiketter** (t.ex. "Talare 1/2" eller "Speaker A/B" – många, som Whisper Transcription, gör det) behåller Script dem: AI:n knyter varje platshållare till rätt karaktär utifrån kontext och story-bibel och skriver ut det riktiga namnet (frågar om kopplingen är oklar).

## Flera användare (hostad webapp)

Som standard körs appen i **lokalt enanvändarläge** (ingen inloggning) – perfekt på din egen dator. Sätt `AUTH_ENABLED=true` för att slå på **Google-inloggning** och ge varje konto sin egen data och egna API-nycklar:

```bash
export AUTH_ENABLED=true
export GOOGLE_CLIENT_ID="<ditt-id>.apps.googleusercontent.com"   # från Google Cloud Console
export SECRET_KEY="<lång slumpsträng>"   # signerar sessionskakan
export COOKIE_SECURE=true                 # när du kör bakom HTTPS
```

- **Google-login** använder Googles "Sign in with Google" (ID-token) och kräver bara ett **client-ID** (inget secret). Skapa en OAuth 2.0-klient (typ: webbapp) i Google Cloud Console och lägg din domän under *Authorized JavaScript origins*.
- **Egen nyckel per användare:** varje inloggad användare lägger in sina egna Anthropic/OpenAI/AssemblyAI-nycklar under **Inställningar → API-nycklar** (lagras på kontot, visas aldrig igen). Du som driftar betalar alltså inget för andras körningar.
- I molnläget är **bara moln-transkribering** (OpenAI/AssemblyAI) tillgänglig – lokal Whisper och bevakad mapp fungerar bara på din egen dator.
- All användardata (projekt, bas-AI, nycklar) ligger under `data/users/<uid>/` och är gitignorerat.

> Driftsättning (Dockerfile + deploy-guide) kommer i nästa steg.

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
