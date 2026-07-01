"""Enhetstest för transkriberingens formateringsfunktion. Kräver inget nätverk/SDK."""
from dataclasses import dataclass, field
from pathlib import Path

import shutil
import threading
import time

import pytest

from app import transcribe as transcribe_mod
from app.transcribe import (
    LocalWhisperTranscriber,
    WatchedFolderTranscriber,
    get_transcriber,
    openai_response_to_text,
    resolve_backend_name,
    transcribe_with_chunking,
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


def test_openai_diarized_segments_become_speaker_lines():
    resp = {
        "segments": [
            {"speaker": "speaker_0", "text": "Hej."},
            {"speaker": "speaker_0", "text": "Hur är läget?"},
            {"speaker": "speaker_1", "text": "Bra tack."},
            {"speaker": "speaker_0", "text": "Skönt."},
        ]
    }
    assert openai_response_to_text(resp) == (
        "Speaker 0: Hej. Hur är läget?\nSpeaker 1: Bra tack.\nSpeaker 0: Skönt."
    )


def test_openai_plain_text_fallback_without_segments():
    assert openai_response_to_text({"text": "  bara text  "}) == "bara text"


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


def test_resolve_backend_name_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("TRANSCRIBE_BACKEND", raising=False)
    assert resolve_backend_name() == "assemblyai"
    assert resolve_backend_name("OpenAI") == "openai"
    monkeypatch.setenv("TRANSCRIBE_BACKEND", "local")
    assert resolve_backend_name() == "local"
    assert resolve_backend_name("watch") == "watch"


@dataclass
class FakeTranscriber:
    """Loggar varje anropad sökväg i stället för att prata med en riktig motor."""

    calls: list = field(default_factory=list)

    def transcribe(self, path, language=None):
        self.calls.append(path)
        return f"text-{len(self.calls)}"


def test_chunking_skipped_for_short_file(tmp_path, monkeypatch):
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"x" * 100)
    monkeypatch.setattr(transcribe_mod, "probe_duration_seconds", lambda p: 60.0)

    def boom(*a, **kw):
        raise AssertionError("ska inte delas upp för en kort fil")

    monkeypatch.setattr(transcribe_mod, "split_audio_into_chunks", boom)
    fake = FakeTranscriber()
    text = transcribe_with_chunking(fake, str(audio), "openai")
    assert text == "text-1"
    assert fake.calls == [str(audio)]


def test_chunking_never_splits_assemblyai(tmp_path, monkeypatch):
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"x" * (30 * 1024 * 1024))  # över OpenAI-gränsen, men irrelevant här
    monkeypatch.setattr(transcribe_mod, "probe_duration_seconds", lambda p: 4000.0)

    def boom(*a, **kw):
        raise AssertionError("assemblyai ska aldrig delas upp")

    monkeypatch.setattr(transcribe_mod, "split_audio_into_chunks", boom)
    fake = FakeTranscriber()
    text = transcribe_with_chunking(fake, str(audio), "assemblyai")
    assert text == "text-1"
    assert fake.calls == [str(audio)]


def test_chunking_splits_oversized_openai_file_and_reports_progress(tmp_path, monkeypatch):
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"x" * (30 * 1024 * 1024))  # över 25 MB-gränsen
    fake_chunks = [str(tmp_path / f"chunk_{i}.m4a") for i in range(3)]
    for c in fake_chunks:
        open(c, "wb").close()

    monkeypatch.setattr(transcribe_mod, "probe_duration_seconds", lambda p: 1800.0)
    monkeypatch.setattr(transcribe_mod, "split_audio_into_chunks", lambda p, secs, outdir: fake_chunks)

    fake = FakeTranscriber()
    progress_calls = []
    text = transcribe_with_chunking(
        fake, str(audio), "openai", on_progress=lambda i, n: progress_calls.append((i, n))
    )
    assert fake.calls == fake_chunks
    assert text == "text-1\ntext-2\ntext-3"
    assert progress_calls == [(1, 3), (2, 3), (3, 3)]


def test_chunking_splits_long_local_file_by_duration(tmp_path, monkeypatch):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x" * 100)  # liten fil, men mycket lång speltid
    fake_chunks = [str(tmp_path / f"chunk_{i}.wav") for i in range(2)]
    for c in fake_chunks:
        open(c, "wb").close()

    monkeypatch.setattr(transcribe_mod, "probe_duration_seconds", lambda p: 3600.0)
    captured = {}

    def fake_split(p, secs, outdir):
        captured["chunk_seconds"] = secs
        return fake_chunks

    monkeypatch.setattr(transcribe_mod, "split_audio_into_chunks", fake_split)
    fake = FakeTranscriber()
    text = transcribe_with_chunking(fake, str(audio), "local")
    assert fake.calls == fake_chunks
    assert text == "text-1\ntext-2"
    assert captured["chunk_seconds"] == transcribe_mod.CHUNK_SECONDS_DEFAULT


def test_probe_duration_seconds_none_without_ffprobe(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    assert transcribe_mod.probe_duration_seconds(str(audio)) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="kräver riktig ffmpeg/ffprobe")
def test_split_audio_into_chunks_with_real_ffmpeg(tmp_path):
    import subprocess

    audio = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "2", str(audio)],
        capture_output=True, check=True,
    )
    duration = transcribe_mod.probe_duration_seconds(str(audio))
    assert duration is not None and 1.5 < duration < 2.5

    outdir = tmp_path / "chunks"
    outdir.mkdir()
    chunks = transcribe_mod.split_audio_into_chunks(str(audio), 1, str(outdir))
    assert len(chunks) == 2
    for c in chunks:
        assert Path(c).exists()
