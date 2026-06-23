"""Enhetstest för transkriberingens formateringsfunktion. Kräver inget nätverk/SDK."""
from dataclasses import dataclass

import threading
import time

from app.transcribe import (
    LocalWhisperTranscriber,
    WatchedFolderTranscriber,
    get_transcriber,
    transcript_to_text,
    utterances_to_text,
)


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


def test_get_transcriber_backend_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("TRANSCRIBE_BACKEND", "assemblyai")
    monkeypatch.setenv("WHISPER_CMD", "printf '' > {output}.txt")
    assert isinstance(get_transcriber("local"), LocalWhisperTranscriber)


def test_srt_stripped_to_plain_text():
    srt = (
        "1\n00:00:01,000 --> 00:00:04,000\nHej där.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nHur mår du?\n"
    )
    assert transcript_to_text("klipp.srt", srt) == "Hej där.\nHur mår du?"


def test_vtt_stripped_and_plain_text_passthrough():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHallå.\n"
    assert transcript_to_text("klipp.vtt", vtt) == "Hallå."
    assert transcript_to_text("anteckning.txt", "  bara text  ") == "bara text"


def test_watched_folder_drops_audio_and_reads_transcript(tmp_path, monkeypatch):
    """Simulerar en bevakad mapp: skriver transkriptet när ljudet dyker upp."""
    in_dir, out_dir = tmp_path / "in", tmp_path / "out"
    in_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setenv("WATCH_IN_DIR", str(in_dir))
    monkeypatch.setenv("WATCH_OUT_DIR", str(out_dir))
    monkeypatch.setenv("WATCH_POLL", "0.02")
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")

    def fake_app():  # härmar MacWhisper: ser ljudet → skriver <stem>.txt
        for _ in range(200):
            files = list(in_dir.iterdir())
            if files:
                (out_dir / (files[0].stem + ".txt")).write_text("hej från mappen")
                return
            time.sleep(0.01)

    worker = threading.Thread(target=fake_app)
    worker.start()
    try:
        assert WatchedFolderTranscriber().transcribe(str(audio)) == "hej från mappen"
    finally:
        worker.join(timeout=3)
