"""FastAPI-app: projekt-/regelhantering, AI-analys (Claude) och FDX-export."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import analyze as analyze_mod
from app import jobs as jobs_mod
from app import store
from app import transcribe as transcribe_mod
from app.fdx import to_fdx
from app.models import GlobalSettings, Project, ScreenplayElement, StoryBible

app = FastAPI(title="Transkription → Manus (FDX)")

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---- request-modeller ----
class CreateProjectIn(BaseModel):
    title: str = "Namnlöst projekt"


class ProjectUpdateIn(BaseModel):
    title: str | None = None
    context: str | None = None
    directives: str | None = None
    story_bible: StoryBible | None = None
    elements: list[ScreenplayElement] | None = None


class AnalyzeIn(BaseModel):
    text: str
    model: str | None = None


class SettingsIn(BaseModel):
    directives: str = ""


# ---- globala inställningar (bas-AI) ----
@app.get("/api/settings")
def get_settings() -> GlobalSettings:
    return store.load_global_settings()


@app.put("/api/settings")
def put_settings(body: SettingsIn) -> GlobalSettings:
    return store.save_global_settings(GlobalSettings(directives=body.directives))


@app.post("/api/extract-text")
def extract_text(file: UploadFile = File(...)) -> dict:
    """Extrahera text ur en uppladdad regel-/formatbok (PDF, TXT, MD) för bas-AI."""
    data = file.file.read()
    file.file.close()
    name = (file.filename or "").lower()
    if name.endswith(".pdf"):
        import io

        from pypdf import PdfReader  # lazy import

        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            raise HTTPException(400, f"Kunde inte läsa PDF: {exc}")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")
    return {"text": text.strip()}


# ---- projekt ----
@app.get("/api/projects")
def list_projects() -> list[dict]:
    return store.list_projects()


@app.post("/api/projects")
def create_project(body: CreateProjectIn) -> Project:
    return store.create_project(body.title)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> Project:
    project = store.load_project(project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    return project


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateIn) -> Project:
    project = store.load_project(project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    merged = project.model_dump()
    merged.update(body.model_dump(exclude_none=True))
    return store.save_project(Project.model_validate(merged))


@app.post("/api/projects/{project_id}/analyze")
def analyze_project(project_id: str, body: AnalyzeIn) -> dict:
    project = store.load_project(project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    settings = store.load_global_settings()
    try:
        result = analyze_mod.analyze(project, body.text, settings, model=body.model)
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"AI-analysen misslyckades: {exc}")
    project = store.merge_analyze_result(project, result)
    store.save_project(project)
    return {"project": project, "clarifications": result.clarifications}


def _run_transcription(job_id: str, tmp_path: str, language: str | None) -> None:
    """Körs i en bakgrundstråd: transkriberar och uppdaterar jobbet."""
    jobs_mod.update_job(job_id, status="running")
    try:
        transcriber = transcribe_mod.get_transcriber()
        text = transcriber.transcribe(tmp_path, language=language)
        jobs_mod.update_job(job_id, status="done", text=text)
    except Exception as exc:  # saknad nyckel, nätverksfel, transkriberingsfel ...
        jobs_mod.update_job(job_id, status="error", error=str(exc))
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.post("/api/projects/{project_id}/transcribe", status_code=202)
def transcribe_audio(
    project_id: str, file: UploadFile = File(...), language: str | None = None
) -> dict:
    """Ladda upp ljud → starta ett transkriberingsjobb i bakgrunden → returnera job_id.

    Lång audio håller inte uppe requesten; klienten pollar status via
    GET /api/transcribe-jobs/{job_id}. Statslöst – lägger inte till i manuset.
    """
    if store.load_project(project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    suffix = os.path.splitext(file.filename or "")[1] or ".audio"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name
    finally:
        file.file.close()
    job = jobs_mod.create_job()
    threading.Thread(
        target=_run_transcription, args=(job.id, tmp_path, language), daemon=True
    ).start()
    return {"job_id": job.id, "status": job.status}


@app.get("/api/transcribe-jobs/{job_id}")
def transcribe_job_status(job_id: str) -> dict:
    job = jobs_mod.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Jobbet finns inte")
    return {"job_id": job.id, "status": job.status, "text": job.text, "error": job.error}


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str) -> Response:
    project = store.load_project(project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    xml = to_fdx(project.elements)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (project.title or "manus"))
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{safe or "manus"}.fdx"'},
    )


# ---- frontend ----
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
