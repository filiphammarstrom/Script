"""Enhetstest för transkriberingens formateringsfunktion. Kräver inget nätverk/SDK."""
from dataclasses import dataclass, field
from pathlib import Path

import shutil
import threading
import time

import pytest

from app import transcribe as transcribe_mod
from app.transcribe import (
    DeepgramTranscriber,
    GroqTranscriber,
    LocalWhisperTranscriber,
    WatchedFolderTranscriber,
    deepgram_response_to_text,
    get_transcriber,
    openai_response_to_text,
    resolve_backend_name,
    should_trim_silence,
    transcribe_with_chunking,
    transcript_to_text,
    trim_silence,
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


def test_get_transcriber_selects_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    t = get_transcriber("groq")
    assert isinstance(t, GroqTranscriber)


def test_groq_requires_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_transcriber("groq")


def test_groq_counts_as_size_limited(tmp_path, monkeypatch):
    """Groq har samma 25 MB-gräns som OpenAI – stora filer ska chunkas."""
    audio = tmp_path / "big.m4a"
    audio.write_bytes(b"x" * (30 * 1024 * 1024))
    monkeypatch.setattr(transcribe_mod, "probe_duration_seconds", lambda p: 300.0)
    assert transcribe_mod._needs_chunking(str(audio), "groq", 300.0) is True


def test_should_trim_silence_backend_filter(monkeypatch):
    monkeypatch.delenv("TRANSCRIBE_TRIM_SILENCE", raising=False)
    assert should_trim_silence("openai") is True
    assert should_trim_silence("groq") is True
    assert should_trim_silence("assemblyai") is True
    assert should_trim_silence("deepgram") is True
    assert should_trim_silence("local") is False
    assert should_trim_silence("watch") is False
    monkeypatch.setenv("TRANSCRIBE_TRIM_SILENCE", "0")
    assert should_trim_silence("openai") is False


def test_deepgram_diarized_utterances_become_speaker_lines():
    data = {
        "results": {
            "utterances": [
                {"speaker": 0, "transcript": "Hej."},
                {"speaker": 1, "transcript": "Hur mår du?"},
                {"speaker": 0, "transcript": "  "},  # tomt segment -> hoppas över
            ]
        }
    }
    assert deepgram_response_to_text(data) == "Speaker 0: Hej.\nSpeaker 1: Hur mår du?"


def test_deepgram_plain_transcript_fallback_without_utterances():
    data = {"results": {"channels": [{"alternatives": [{"transcript": "  bara text  "}]}]}}
    assert deepgram_response_to_text(data) == "bara text"


def test_deepgram_empty_results_gives_empty_string():
    assert deepgram_response_to_text({}) == ""


def test_get_transcriber_selects_deepgram(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg_test")
    t = get_transcriber("deepgram")
    assert isinstance(t, DeepgramTranscriber)


def test_deepgram_requires_key(monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        get_transcriber("deepgram")


def test_chunking_never_splits_deepgram(tmp_path, monkeypatch):
    """Deepgram diariserar precis som AssemblyAI – uppdelning skulle förstöra talarräkningen."""
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"x" * (30 * 1024 * 1024))
    monkeypatch.setattr(transcribe_mod, "probe_duration_seconds", lambda p: 4000.0)

    def boom(*a, **kw):
        raise AssertionError("deepgram ska aldrig delas upp")

    monkeypatch.setattr(transcribe_mod, "split_audio_into_chunks", boom)
    fake = FakeTranscriber()
    text = transcribe_with_chunking(fake, str(audio), "deepgram")
    assert text == "text-1"
    assert fake.calls == [str(audio)]


def test_trim_silence_none_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"x")
    assert trim_silence(str(audio)) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="kräver riktig ffmpeg/ffprobe")
def test_trim_silence_shortens_audio_with_real_ffmpeg(tmp_path):
    """3 s ton + 6 s tystnad + 3 s ton ska krympa till ~7 s (tystnaden → 0,9 s)."""
    import os
    import subprocess

    audio = tmp_path / "gappy.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-filter_complex", "[1:a]atrim=duration=6[s];[0:a][s][0:a]concat=n=3:v=0:a=1[out]",
            "-map", "[out]", str(audio),
        ],
        capture_output=True, check=True,
    )
    assert 11.5 < transcribe_mod.probe_duration_seconds(str(audio)) < 12.5

    trimmed = trim_silence(str(audio))
    assert trimmed is not None
    try:
        out_dur = transcribe_mod.probe_duration_seconds(trimmed)
        assert out_dur is not None and out_dur < 8.5  # tystnaden bortklippt
        assert trimmed.endswith(".ogg")
    finally:
        os.unlink(trimmed)


def test_trim_silence_returns_none_on_broken_input(tmp_path):
    """Ogiltig ljudfil → None (anroparen använder originalet), ingen krasch."""
    bad = tmp_path / "inte-ljud.wav"
    bad.write_bytes(b"detta ar inte ljud")
    assert trim_silence(str(bad)) is None
