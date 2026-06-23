"""Utbytbart transkriberingslager: ljud -> talar-märkt text.

Default-backend är AssemblyAI (diarisering på). Texten formateras som rader
"Speaker A: ..." vilket den befintliga analyspipelinen redan känner igen.

assemblyai-SDK:t importeras "lazy" så att modulen – och enhetstesterna av
formateringsfunktionen – kan användas utan att SDK:t är installerat.
"""
from __future__ import annotations

import os
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


def get_transcriber() -> Transcriber:
    """Välj backend via env TRANSCRIBE_BACKEND (default 'assemblyai')."""
    backend = os.environ.get("TRANSCRIBE_BACKEND", "assemblyai").lower()
    if backend == "assemblyai":
        return AssemblyAITranscriber()
    raise RuntimeError(f"Okänd TRANSCRIBE_BACKEND: {backend!r}")
