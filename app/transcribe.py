"""Utbytbart transkriberingslager: ljud -> talar-märkt text.

Default-backend är AssemblyAI (diarisering på). Texten formateras som rader
"Speaker A: ..." vilket den befintliga analyspipelinen redan känner igen.

assemblyai-SDK:t importeras "lazy" så att modulen – och enhetstesterna av
formateringsfunktionen – kan användas utan att SDK:t är installerat.

Mycket långa ljudfiler delas upp i bitar (`transcribe_with_chunking`) innan de
skickas till en motor som annars skulle strula: OpenAI:s API stoppar filer över
25 MB, och lokala/bevakade motorer ger annars ingen framstegsstatus under en
lång körning. Kräver `ffmpeg`/`ffprobe` i PATH – annars faller det tillbaka på
en vanlig (odelad) transkribering. AssemblyAI delas aldrig upp; den hanterar
långa filer i sin egen molntjänst och en uppdelning skulle bara förstöra dess
talarigenkänning (varje bit skulle börja om räkningen på "Speaker A").
"""
from __future__ import annotations

import glob
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Protocol


def utterances_to_text(utterances) -> str:
    """Formatera diariserade talarsegment till rader 'Speaker X: ...'.

    Tar vilken sekvens som helst av objekt med attributen `speaker` och `text`.
    Tomma segment hoppas över.
    """
    lines: list[str] = []
    for utt in utterances:
        speaker = getattr(utt, "speaker", None) or "?"
        text = (getattr(utt, "text", "") or "").strip()
        if text:
            lines.append(f"Speaker {speaker}: {text}")
    return "\n".join(lines)


class Transcriber(Protocol):
    def transcribe(self, path: str, language: str | None = None) -> str: ...


class AssemblyAITranscriber:
    """Molntranskribering med diarisering via AssemblyAI."""

    def __init__(self, api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise RuntimeError("AssemblyAI-nyckel saknas (ASSEMBLYAI_API_KEY eller din egen nyckel).")
        import assemblyai as aai  # lazy import

        aai.settings.api_key = api_key
        self._aai = aai

    def transcribe(self, path: str, language: str | None = None) -> str:
        aai = self._aai
        if language:
            config = aai.TranscriptionConfig(speaker_labels=True, language_code=language)
        else:
            config = aai.TranscriptionConfig(speaker_labels=True, language_detection=True)
        transcript = aai.Transcriber().transcribe(path, config=config)
        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"Transkribering misslyckades: {transcript.error}")
        if transcript.utterances:
            return utterances_to_text(transcript.utterances)
        return (transcript.text or "").strip()


class LocalWhisperTranscriber:
    """Lokal transkribering via en Whisper-CLI på din egen dator – gratis, ingen moln-API.

    Konfigureras via miljövariabler så att den funkar med olika CLI:er:
      WHISPER_CMD   – (valfritt) kommandomall för en annan CLI än standard. Stödjer
                      platshållarna {input}, {output}, {outdir}, {model}, {language}.
                      Ex. whisper.cpp:
                        "whisper-cli -m ~/models/ggml-small.bin -f {input} -otxt -of {output}"
                      Skriv transkriptet till {output}.txt, annars läses CLI:ns stdout.
      WHISPER_BIN   – binär för standardläget (openai-whisper), default "whisper".
      WHISPER_MODEL – modellnamn/sökväg, default "small".

    Ger ingen diarisering (talar-etiketter); AI:n attribuerar talare från sammanhang.
    """

    def __init__(self) -> None:
        self._cmd_template = os.environ.get("WHISPER_CMD")
        self._model = os.environ.get("WHISPER_MODEL", "small")
        self._bin = os.environ.get("WHISPER_BIN", "whisper")
        if not self._cmd_template and shutil.which(self._bin) is None:
            raise RuntimeError(
                f"Lokal Whisper-CLI '{self._bin}' hittades inte i PATH. Installera t.ex. "
                "openai-whisper (pip install -U openai-whisper) eller sätt WHISPER_CMD "
                "för en annan CLI (t.ex. whisper.cpp)."
            )

    def transcribe(self, path: str, language: str | None = None) -> str:
        with tempfile.TemporaryDirectory() as outdir:
            if self._cmd_template:
                text = self._run_template(path, outdir, language)
            else:
                text = self._run_default(path, outdir, language)
        text = text.strip()
        if not text:
            raise RuntimeError("Lokal transkribering gav ingen text.")
        return text

    def _run_default(self, path: str, outdir: str, language: str | None) -> str:
        cmd = [
            self._bin, path, "--model", self._model,
            "--output_format", "txt", "--output_dir", outdir, "--fp16", "False",
        ]
        if language:
            cmd += ["--language", language]
        self._run(cmd)
        stem = os.path.splitext(os.path.basename(path))[0]
        out_txt = os.path.join(outdir, stem + ".txt")
        produced = [out_txt] if os.path.exists(out_txt) else glob.glob(os.path.join(outdir, "*.txt"))
        if not produced:
            raise RuntimeError("Lokal transkribering producerade ingen txt-fil.")
        with open(produced[0], encoding="utf-8") as fh:
            return fh.read()

    def _run_template(self, path: str, outdir: str, language: str | None) -> str:
        out_base = os.path.join(outdir, "transcript")
        cmd = self._cmd_template.format(
            input=shlex.quote(path),
            output=shlex.quote(out_base),
            outdir=shlex.quote(outdir),
            model=shlex.quote(self._model),
            language=shlex.quote(language or ""),
        )
        result = self._run(cmd, shell=True)
        for cand in (out_base + ".txt", out_base):
            if os.path.exists(cand):
                with open(cand, encoding="utf-8") as fh:
                    return fh.read()
        return result.stdout or ""

    @staticmethod
    def _run(cmd, shell: bool = False) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                cmd, shell=shell, capture_output=True, text=True, timeout=3600
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Kunde inte starta lokal transkribering: {exc}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-500:]
            raise RuntimeError(
                f"Lokal transkribering misslyckades (kod {result.returncode}): {detail}"
            )
        return result


class WatchedFolderTranscriber:
    """Helautomatisk via en GUI-apps BEVAKADE MAPP (t.ex. MacWhisper / Whisper Transcription).

    Appen lägger ljudet i mappen som GUI-appen bevakar, väntar in transkriptfilen som
    appen skriver, läser tillbaka den och rensar ev. SRT/VTT-tidskoder. Konfig via env:
      WATCH_IN_DIR  – (krävs) mappen GUI-appen bevakar; hit kopieras ljudet.
      WATCH_OUT_DIR – mappen transkriptet dyker upp i (default = WATCH_IN_DIR).
      WATCH_OUT_EXT – transkriptfilens ändelse (default ".txt"; t.ex. ".srt").
      WATCH_TIMEOUT – max väntetid i sekunder (default 1800).
      WATCH_POLL    – hur ofta mappen kollas, sekunder (default 2).
    """

    def __init__(self) -> None:
        self._in_dir = os.environ.get("WATCH_IN_DIR", "")
        if not self._in_dir:
            raise RuntimeError(
                "WATCH_IN_DIR saknas – peka den på mappen som din transkriberingsapp bevakar."
            )
        if not os.path.isdir(self._in_dir):
            raise RuntimeError(f"WATCH_IN_DIR finns inte: {self._in_dir}")
        self._out_dir = os.environ.get("WATCH_OUT_DIR") or self._in_dir
        self._out_ext = os.environ.get("WATCH_OUT_EXT", ".txt")
        self._timeout = float(os.environ.get("WATCH_TIMEOUT", "1800"))
        self._poll = float(os.environ.get("WATCH_POLL", "2"))

    def transcribe(self, path: str, language: str | None = None) -> str:
        suffix = os.path.splitext(path)[1] or ".audio"
        stem = "script_" + uuid.uuid4().hex
        in_path = os.path.join(self._in_dir, stem + suffix)
        out_path = os.path.join(self._out_dir, stem + self._out_ext)
        shutil.copyfile(path, in_path)
        deadline = time.monotonic() + self._timeout
        last_size = -1
        try:
            while time.monotonic() < deadline:
                if os.path.exists(out_path):
                    size = os.path.getsize(out_path)
                    if size > 0 and size == last_size:  # stabil = färdigskriven
                        with open(out_path, encoding="utf-8", errors="replace") as fh:
                            return transcript_to_text(out_path, fh.read()).strip()
                    last_size = size
                time.sleep(self._poll)
            raise RuntimeError(
                f"Tidsgräns: inget transkript dök upp i {self._out_dir} inom "
                f"{self._timeout:.0f}s. Kontrollera att appens bevakade mapp är WATCH_IN_DIR "
                f"och att den skriver {self._out_ext}-filer till WATCH_OUT_DIR."
            )
        finally:
            for p in (in_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _attr_or_key(obj, key):
    """Hämta `key` från ett objekt (attribut) eller en dict – tål båda formerna."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _norm_speaker(speaker) -> str:
    """Normalisera talar-etikett: 'speaker_0'/'SPEAKER 1' -> '0'/'1' (annars oförändrad)."""
    if speaker is None:
        return ""
    s = str(speaker).strip()
    low = s.lower()
    for prefix in ("speaker_", "speaker ", "speaker"):
        if low.startswith(prefix):
            return s[len(prefix):].strip() or s
    return s


def openai_response_to_text(resp) -> str:
    """Formatera OpenAI:s transkriberingssvar till text.

    Diariserade segment blir rader 'Speaker X: ...' (samma format som AssemblyAI och som
    analyspipelinen känner igen); intilliggande segment från samma talare slås ihop.
    Saknas segment används svarets råa `text`.
    """
    segments = _attr_or_key(resp, "segments")
    if segments:
        rows: list[list[str]] = []  # [speaker, text]
        for seg in segments:
            speaker = _norm_speaker(_attr_or_key(seg, "speaker"))
            text = (_attr_or_key(seg, "text") or "").strip()
            if not text:
                continue
            if rows and rows[-1][0] == speaker:
                rows[-1][1] += " " + text
            else:
                rows.append([speaker, text])
        if rows:
            return "\n".join(
                (f"Speaker {sp}: {tx}" if sp else tx) for sp, tx in rows
            )
    return (_attr_or_key(resp, "text") or "").strip()


class GroqTranscriber:
    """Molntranskribering via Groqs Whisper-API – mycket billigt (~0,04 $/timme,
    ungefär en tiondel av OpenAI/AssemblyAI) och snabbt.

    OpenAI-kompatibelt API, så openai-SDK:t återanvänds med en annan base_url.
    Ingen diarisering (talar-etiketter) – AI:n attribuerar talare från sammanhang.
    Env: GROQ_API_KEY (eller användarens egen nyckel), GROQ_TRANSCRIBE_MODEL
    (default whisper-large-v3-turbo).
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("Groq-nyckel saknas (GROQ_API_KEY eller din egen nyckel).")
        from openai import OpenAI  # lazy import

        self._client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        self._model = model or os.environ.get("GROQ_TRANSCRIBE_MODEL") or "whisper-large-v3-turbo"

    def transcribe(self, path: str, language: str | None = None) -> str:
        kwargs: dict = {"model": self._model}
        if language:
            kwargs["language"] = language
        with open(path, "rb") as fh:
            resp = self._client.audio.transcriptions.create(file=fh, **kwargs)
        text = (getattr(resp, "text", None) or "").strip()
        if not text:
            raise RuntimeError("Groq-transkribering gav ingen text.")
        return text


class OpenAITranscriber:
    """Molntranskribering via OpenAI:s ljud-API – default med diarisering.

    Env:
      OPENAI_API_KEY          – (krävs) API-nyckel.
      OPENAI_TRANSCRIBE_MODEL – modell, default 'gpt-4o-transcribe-diarize' (talar-etiketter).
                                Sätt t.ex. 'gpt-4o-mini-transcribe' för billigare utan diarisering.
    """

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OpenAI-nyckel saknas (OPENAI_API_KEY eller din egen nyckel).")
        from openai import OpenAI  # lazy import

        self._client = OpenAI(api_key=api_key)
        self._model = (
            model
            or os.environ.get("OPENAI_TRANSCRIBE_MODEL")
            or "gpt-4o-transcribe-diarize"
        )

    def transcribe(self, path: str, language: str | None = None) -> str:
        kwargs: dict = {"model": self._model}
        if language:
            kwargs["language"] = language
        if "diarize" in self._model:
            kwargs["response_format"] = "diarized_json"
        with open(path, "rb") as fh:
            resp = self._client.audio.transcriptions.create(file=fh, **kwargs)
        text = openai_response_to_text(resp)
        if not text:
            raise RuntimeError("OpenAI-transkribering gav ingen text.")
        return text


def resolve_backend_name(backend: str | None = None) -> str:
    """Slå upp vilken motor som faktiskt avses: explicit `backend`, annars env, annars 'assemblyai'."""
    return (backend or os.environ.get("TRANSCRIBE_BACKEND", "assemblyai")).lower()


def get_transcriber(
    backend: str | None = None,
    model: str | None = None,
    openai_key: str | None = None,
    assemblyai_key: str | None = None,
    groq_key: str | None = None,
    allow_local: bool = True,
) -> Transcriber:
    """Välj transkriberingsmotor.

    `backend` väljs per anrop (UI), annars env TRANSCRIBE_BACKEND, annars 'assemblyai'.
    `model` väljer modell per anrop (openai/groq). `openai_key`/`assemblyai_key`/
    `groq_key` är användarens egna nycklar. `allow_local=False` (hostat läge)
    blockerar lokala motorer som bara fungerar på den egna datorn.
      'assemblyai' = moln med diarisering (kostar per minut, valbar reserv).
      'openai' = moln via OpenAI; modell väljs med `model` (diarize / mini / standard).
      'groq' = moln via Groq – billigast (~0,04 $/timme), ingen diarisering.
      'local'/'whisper' = lokal Whisper-CLI på din dator (gratis).
      'watch' = helautomatiskt via en GUI-apps bevakade mapp (MacWhisper m.fl.).
    """
    backend = resolve_backend_name(backend)
    if backend == "assemblyai":
        return AssemblyAITranscriber(api_key=assemblyai_key)
    if backend in ("openai", "gpt-4o", "gpt4o", "openai_diarize"):
        return OpenAITranscriber(model=model, api_key=openai_key)
    if backend == "groq":
        return GroqTranscriber(model=model, api_key=groq_key)
    if backend in ("local", "whisper", "whisper_cli"):
        if not allow_local:
            raise RuntimeError("Lokal transkribering är inte tillgänglig i molnläget – välj OpenAI eller AssemblyAI.")
        return LocalWhisperTranscriber()
    if backend in ("watch", "watched", "watched_folder", "macwhisper"):
        if not allow_local:
            raise RuntimeError("Bevakad mapp är inte tillgänglig i molnläget – välj OpenAI eller AssemblyAI.")
        return WatchedFolderTranscriber()
    raise RuntimeError(f"Okänd transkriberingsmotor: {backend!r}")


def transcript_to_text(filename: str, text: str) -> str:
    """Gör om ett färdigt transkript (.txt/.srt/.vtt) från en lokal app till ren text.

    SRT/VTT: indexrader, tidskoder och cue-formatering tas bort så att bara dialogen blir kvar.
    """
    name = (filename or "").lower()
    if name.endswith(".srt") or name.endswith(".vtt"):
        lines: list[str] = []
        for raw in text.splitlines():
            s = raw.strip()
            if not s or s == "WEBVTT" or s.startswith("NOTE") or "-->" in s or s.isdigit():
                continue
            lines.append(s)
        return "\n".join(lines).strip()
    return text.strip()


# ---- Chunkning av mycket långa ljudfiler --------------------------------------------

CHUNK_SECONDS_DEFAULT = int(os.environ.get("TRANSCRIBE_CHUNK_SECONDS", "900"))  # 15 min
OPENAI_MAX_BYTES = 25 * 1024 * 1024  # OpenAI:s och Groqs ljud-API:er accepterar inte större filer

# AssemblyAI hanterar långa filer på egen hand i molnet; att dela upp den skulle bara
# förstöra dess talarigenkänning (varje bit börjar om räkningen på "Speaker A").
_NO_CHUNK_BACKENDS = {"assemblyai"}
_SIZE_LIMITED_BACKENDS = {"openai", "gpt-4o", "gpt4o", "openai_diarize", "groq"}

# Molnmotorer som fakturerar per minut – där lönar det sig att klippa bort tystnad.
_TRIMMABLE_BACKENDS = {"assemblyai", "openai", "gpt-4o", "gpt4o", "openai_diarize", "groq"}


def should_trim_silence(backend: str) -> bool:
    """Tystnad klipps före uppladdning till motorer som fakturerar per minut.
    Stängs av med TRANSCRIBE_TRIM_SILENCE=0. Lokala motorer och bevakad mapp rörs inte."""
    enabled = os.environ.get("TRANSCRIBE_TRIM_SILENCE", "1").lower() in ("1", "true", "yes")
    return enabled and backend in _TRIMMABLE_BACKENDS


def trim_silence(path: str) -> str | None:
    """Klipp bort längre tystnader ur ljudet med ffmpeg innan moln-uppladdning.

    Diktering innehåller mycket paus och tanketid, och molnmotorerna fakturerar per
    minut – att kapa tystnader brukar spara en rejäl andel av de fakturerade
    minuterna utan att tal går förlorat (0,9 s paus lämnas kvar i varje skarv så
    talrytmen bevaras). Ljudet kodas samtidigt om till mono-Opus 32 kbit/s, vilket
    även krymper uppladdningen. Returnerar sökvägen till en NY temporär fil, eller
    None om ffmpeg saknas eller klippningen misslyckas – då används originalet.
    """
    if shutil.which("ffmpeg") is None:
        return None
    fd, out_path = tempfile.mkstemp(suffix=".ogg", prefix="script_trim_")
    os.close(fd)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", path,
                "-af",
                "silenceremove=start_periods=1:start_threshold=-40dB:start_silence=0.3:"
                "stop_periods=-1:stop_threshold=-40dB:stop_silence=0.9",
                "-c:a", "libopus", "-b:a", "32k", "-ac", "1",
                out_path,
            ],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0 or os.path.getsize(out_path) == 0:
            raise RuntimeError((result.stderr or "").strip()[-300:] or "tom utfil")
        return out_path
    except Exception:
        try:
            os.unlink(out_path)
        except OSError:
            pass
        return None


def probe_duration_seconds(path: str) -> float | None:
    """Fråga ffprobe hur lång ljudfilen är (sekunder). None om ffprobe saknas/misslyckas."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=60,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def split_audio_into_chunks(path: str, chunk_seconds: int, outdir: str) -> list[str]:
    """Dela en ljudfil i ~chunk_seconds-långa bitar med ffmpeg (stream-copy, ingen omkodning)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg hittades inte – installera det för att transkribera mycket långa ljudfiler."
        )
    suffix = os.path.splitext(path)[1] or ".m4a"
    pattern = os.path.join(outdir, f"chunk_%03d{suffix}")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", path, "-f", "segment",
                "-segment_time", str(chunk_seconds), "-c", "copy", "-reset_timestamps", "1",
                pattern,
            ],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.SubprocessError as exc:
        raise RuntimeError(f"ffmpeg misslyckades: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-500:]
        raise RuntimeError(f"ffmpeg kunde inte dela upp ljudfilen (kod {result.returncode}): {detail}")
    chunks = sorted(glob.glob(os.path.join(outdir, f"chunk_*{suffix}")))
    if not chunks:
        raise RuntimeError("ffmpeg delade inte upp ljudfilen (inga bitar skapades).")
    return chunks


def _needs_chunking(path: str, backend: str, duration: float | None) -> bool:
    if backend in _NO_CHUNK_BACKENDS:
        return False
    if backend in _SIZE_LIMITED_BACKENDS and os.path.getsize(path) > OPENAI_MAX_BYTES:
        return True
    return bool(duration and duration > CHUNK_SECONDS_DEFAULT)


def _target_chunk_seconds(path: str, backend: str, duration: float | None) -> int:
    """Hur långa bitarna ska vara. Räknar ut en säker längd om det är filstorleken
    (inte längden) som tvingar fram uppdelningen, annars standardlängden."""
    if backend in _SIZE_LIMITED_BACKENDS and duration and duration > 0:
        size = os.path.getsize(path)
        if size > OPENAI_MAX_BYTES:
            bytes_per_sec = size / duration
            # 80% marginal mot gränsen (containerformat/headers tar lite extra per bit)
            return max(30, int(OPENAI_MAX_BYTES * 0.8 / bytes_per_sec))
    return CHUNK_SECONDS_DEFAULT


def transcribe_with_chunking(
    transcriber: Transcriber,
    path: str,
    backend: str,
    language: str | None = None,
    on_progress=None,
) -> str:
    """Transkribera en ljudfil – delar upp den i bitar först om den är mycket lång.

    `on_progress(i, n)` anropas inför varje bit (1-indexerat) så anroparen kan visa
    "del 2 av 5"-status under en lång körning. Behövs `ffmpeg`/`ffprobe` inte (filen är
    kort nog, eller motorn ska aldrig delas upp) transkriberas filen som vanligt.
    """
    duration = probe_duration_seconds(path)
    if not _needs_chunking(path, backend, duration):
        return transcriber.transcribe(path, language=language)
    chunk_seconds = _target_chunk_seconds(path, backend, duration)
    with tempfile.TemporaryDirectory(prefix="script_chunks_") as workdir:
        chunks = split_audio_into_chunks(path, chunk_seconds, workdir)
        if len(chunks) <= 1:
            return transcriber.transcribe(path, language=language)
        total = len(chunks)
        texts: list[str] = []
        for i, chunk_path in enumerate(chunks, start=1):
            if on_progress:
                on_progress(i, total)
            text = transcriber.transcribe(chunk_path, language=language)
            if text:
                texts.append(text)
        return "\n".join(texts)
