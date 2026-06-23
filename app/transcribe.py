"""Utbytbart transkriberingslager: ljud -> talar-märkt text.

Default-backend är AssemblyAI (diarisering på). Texten formateras som rader
"Speaker A: ..." vilket den befintliga analyspipelinen redan känner igen.

assemblyai-SDK:t importeras "lazy" så att modulen – och enhetstesterna av
formateringsfunktionen – kan användas utan att SDK:t är installerat.
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

    def __init__(self) -> None:
        api_key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY saknas i miljön.")
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


class OpenAITranscriber:
    """Molntranskribering via OpenAI:s ljud-API – default med diarisering.

    Env:
      OPENAI_API_KEY          – (krävs) API-nyckel.
      OPENAI_TRANSCRIBE_MODEL – modell, default 'gpt-4o-transcribe-diarize' (talar-etiketter).
                                Sätt t.ex. 'gpt-4o-mini-transcribe' för billigare utan diarisering.
    """

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY saknas i miljön.")
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


def get_transcriber(backend: str | None = None, model: str | None = None) -> Transcriber:
    """Välj transkriberingsmotor.

    `backend` väljs per anrop (UI), annars env TRANSCRIBE_BACKEND, annars 'assemblyai'.
    `model` väljer modell per anrop (används av openai-motorn, t.ex. diarisering vs billig 1-röst).
      'assemblyai' = moln med diarisering (kostar per minut, valbar reserv).
      'openai' = moln via OpenAI; modell väljs med `model` (diarize / mini / standard).
      'local'/'whisper' = lokal Whisper-CLI på din dator (gratis).
      'watch' = helautomatiskt via en GUI-apps bevakade mapp (MacWhisper m.fl.).
    """
    backend = (backend or os.environ.get("TRANSCRIBE_BACKEND", "assemblyai")).lower()
    if backend == "assemblyai":
        return AssemblyAITranscriber()
    if backend in ("openai", "gpt-4o", "gpt4o", "openai_diarize"):
        return OpenAITranscriber(model=model)
    if backend in ("local", "whisper", "whisper_cli"):
        return LocalWhisperTranscriber()
    if backend in ("watch", "watched", "watched_folder", "macwhisper"):
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
