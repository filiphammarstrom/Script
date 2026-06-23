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


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def create_job() -> Job:
    job = Job(id=uuid.uuid4().hex[:12])
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def update_job(job_id: str, **fields) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)
