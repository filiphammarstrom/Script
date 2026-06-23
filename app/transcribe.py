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


def get_transcriber(backend: str | None = None) -> Transcriber:
    """Välj transkriberingsmotor.

    `backend` väljs per anrop (UI), annars env TRANSCRIBE_BACKEND, annars 'assemblyai'.
      'assemblyai' = moln med diarisering (kostar per minut, valbar reserv).
      'local'/'whisper' = lokal Whisper-CLI på din dator (gratis).
    """
    backend = (backend or os.environ.get("TRANSCRIBE_BACKEND", "assemblyai")).lower()
    if backend == "assemblyai":
        return AssemblyAITranscriber()
    if backend in ("local", "whisper", "whisper_cli"):
        return LocalWhisperTranscriber()
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
