"""Enhetstest för jobbregistret. Kräver inget nätverk/SDK."""
from app import jobs


def test_job_lifecycle():
    job = jobs.create_job()
    assert job.status == "queued"
    assert jobs.get_job(job.id) is job

    jobs.update_job(job.id, status="running")
    assert jobs.get_job(job.id).status == "running"

    jobs.update_job(job.id, status="done", text="Speaker A: Hej.")
    done = jobs.get_job(job.id)
    assert done.status == "done"
    assert done.text == "Speaker A: Hej."


def test_unique_ids_and_missing():
    a = jobs.create_job()
    b = jobs.create_job()
    assert a.id != b.id
    assert jobs.get_job("does-not-exist") is None
    # update på okänt jobb ska inte krascha
    jobs.update_job("does-not-exist", status="done")
