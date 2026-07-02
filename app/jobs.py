"""Enkelt in-memory-jobbregister för asynkrona uppgifter (t.ex. ljudtranskribering).

Jobben lever i processminnet vilket räcker för den här enprocess-servern. Görs
trådsäkert med ett lås eftersom transkriberingen körs i en bakgrundstråd.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

# Statusvärden: queued | running | done | error
@dataclass
class Job:
    id: str
    status: str = "queued"
    text: str = ""
    error: str = ""
    progress: str = ""  # t.ex. "Del 2 av 5" under en chunkad transkribering
    uid: str = ""       # ägare – andras jobb (och deras transkript) ska inte gå att läsa
    done_at: float = 0.0  # när jobbet blev klart (för utrensning)


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_PURGE_AFTER_SECONDS = 3600  # färdiga jobb (med hela transkriptet i minnet) rensas efter en timme


def _purge_finished_locked() -> None:
    """Släpp färdiga jobb efter en stund – annars växer processminnet med varje
    transkript för alltid på en långkörande server. Anropas med låset taget."""
    import time

    cutoff = time.monotonic() - _PURGE_AFTER_SECONDS
    for jid in [j.id for j in _jobs.values() if j.done_at and j.done_at < cutoff]:
        _jobs.pop(jid, None)


def create_job(uid: str = "") -> Job:
    job = Job(id=uuid.uuid4().hex[:12], uid=uid)
    with _lock:
        _purge_finished_locked()
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def update_job(job_id: str, **fields) -> None:
    import time

    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        if job.status in ("done", "error") and not job.done_at:
            job.done_at = time.monotonic()
