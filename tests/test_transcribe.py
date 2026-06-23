"""Enhetstest för transkriberingens formateringsfunktion. Kräver inget nätverk/SDK."""
from dataclasses import dataclass

from app.transcribe import utterances_to_text


@dataclass
class U:
    speaker: str
    text: str


def test_formats_speaker_lines_and_skips_empty():
    out = utterances_to_text([U("A", "Hej."), U("B", "  Tjena  "), U("A", "")])
    assert out == "Speaker A: Hej.\nSpeaker B: Tjena"


def test_empty_input():
    assert utterances_to_text([]) == ""
