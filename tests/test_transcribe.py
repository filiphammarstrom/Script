"""Enhetstest för transkriberingens formateringsfunktion. Kräver inget nätverk/SDK."""
from dataclasses import dataclass

from app.transcribe import LocalWhisperTranscriber, get_transcriber, utterances_to_text


@dataclass
class U:
    speaker: str
    text: str


def test_formats_speaker_lines_and_skips_empty():
    out = utterances_to_text([U("A", "Hej."), U("B", "  Tjena  "), U("A", "")])
    assert out == "Speaker A: Hej.\nSpeaker B: Tjena"


def test_empty_input():
    assert utterances_to_text([]) == ""


def test_local_whisper_runs_cli_and_reads_output(tmp_path, monkeypatch):
    """WHISPER_CMD-mallen körs och {output}.txt läses tillbaka – ingen riktig Whisper."""
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    monkeypatch.setenv("WHISPER_CMD", "printf 'hej världen' > {output}.txt")
    assert LocalWhisperTranscriber().transcribe(str(audio)) == "hej världen"


def test_get_transcriber_selects_local_backend(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_BACKEND", "local")
    monkeypatch.setenv("WHISPER_CMD", "printf '' > {output}.txt")  # undvik PATH-koll
    assert isinstance(get_transcriber(), LocalWhisperTranscriber)
