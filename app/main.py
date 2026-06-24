"""FastAPI-app: konton, projekt-/regelhantering per användare, AI-analys och FDX-export.

Inloggning är valfri (se app/auth.py): AUTH_ENABLED=false ger lokalt enanvändarläge,
AUTH_ENABLED=true kräver Google-login och ger varje konto sin egen data och egna nycklar.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app import analyze as analyze_mod
from app import auth as auth_mod
from app import jobs as jobs_mod
from app import store
from app import transcribe as transcribe_mod
from app.fdx import to_fdx
from app.models import GlobalSettings, Project, ScreenplayElement, StoryBible

app = FastAPI(title="Transkription → Manus (FDX)")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SECRET_KEY", "dev-insecure-change-me"),
    same_site="lax",
    https_only=os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

store.migrate_legacy()  # flytta ev. äldre enanvändardata till "local" en gång


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


class ReviseIn(BaseModel):
    instruction: str
    model: str | None = None


class SettingsIn(BaseModel):
    directives: str = ""
    rules_filename: str = ""


class SecretsIn(BaseModel):
    anthropic_key: str | None = None
    openai_key: str | None = None
    assemblyai_key: str | None = None


class GoogleLoginIn(BaseModel):
    credential: str


# ---- inloggning / konto ----
@app.get("/api/config")
def get_config() -> dict:
    return {"auth_enabled": auth_mod.auth_enabled(), "google_client_id": auth_mod.google_client_id()}


@app.get("/api/me")
def get_me(uid: str = Depends(auth_mod.current_uid)) -> dict:
    if not auth_mod.auth_enabled():
        return {"id": uid, "name": "Lokal användare", "email": "", "auth_enabled": False}
    user = store.load_user(uid) or {"id": uid}
    return {**user, "auth_enabled": True}


@app.post("/auth/google")
def auth_google(body: GoogleLoginIn, request: Request) -> dict:
    try:
        info = auth_mod.verify_google_id_token(body.credential)
    except ValueError as exc:
        raise HTTPException(401, str(exc))
    uid = store.upsert_user(info["sub"], info["email"], info["name"])
    request.session["uid"] = uid
    return {"ok": True, "id": uid, "name": info["name"], "email": info["email"]}


@app.post("/auth/logout")
def auth_logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


# ---- globala inställningar (bas-AI) ----
@app.get("/api/settings")
def get_settings(uid: str = Depends(auth_mod.current_uid)) -> GlobalSettings:
    return store.load_global_settings(uid)


@app.put("/api/settings")
def put_settings(body: SettingsIn, uid: str = Depends(auth_mod.current_uid)) -> GlobalSettings:
    return store.save_global_settings(
        uid, GlobalSettings(directives=body.directives, rules_filename=body.rules_filename)
    )


# ---- användarens egna API-nycklar ----
@app.get("/api/secrets")
def get_secrets(uid: str = Depends(auth_mod.current_uid)) -> dict:
    """Returnerar bara HUR-vida varje nyckel är satt – aldrig själva nyckeln."""
    s = store.load_secrets(uid)
    return {
        "anthropic": bool(s.get("anthropic_key")),
        "openai": bool(s.get("openai_key")),
        "assemblyai": bool(s.get("assemblyai_key")),
    }


@app.put("/api/secrets")
def put_secrets(body: SecretsIn, uid: str = Depends(auth_mod.current_uid)) -> dict:
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    store.save_secrets(uid, updates)
    return get_secrets(uid)


@app.post("/api/extract-text")
def extract_text(file: UploadFile = File(...), uid: str = Depends(auth_mod.current_uid)) -> dict:
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
def list_projects(uid: str = Depends(auth_mod.current_uid)) -> list[dict]:
    return store.list_projects(uid)


@app.post("/api/projects")
def create_project(body: CreateProjectIn, uid: str = Depends(auth_mod.current_uid)) -> Project:
    return store.create_project(uid, body.title)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> Project:
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    return project


@app.put("/api/projects/{project_id}")
def update_project(
    project_id: str, body: ProjectUpdateIn, uid: str = Depends(auth_mod.current_uid)
) -> Project:
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    merged = project.model_dump()
    merged.update(body.model_dump(exclude_none=True))
    return store.save_project(uid, Project.model_validate(merged))


@app.delete("/api/projects/{project_id}")
def remove_project(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    if not store.delete_project(uid, project_id):
        raise HTTPException(404, "Projektet finns inte")
    return {"ok": True}


@app.post("/api/projects/{project_id}/analyze")
def analyze_project(
    project_id: str, body: AnalyzeIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    settings = store.load_global_settings(uid)
    api_key = store.load_secrets(uid).get("anthropic_key")
    try:
        result = analyze_mod.analyze(project, body.text, settings, model=body.model, api_key=api_key)
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"AI-analysen misslyckades: {exc}")
    project = store.merge_analyze_result(project, result)
    store.save_project(uid, project)
    return {"project": project, "clarifications": result.clarifications}


@app.post("/api/projects/{project_id}/revise")
def revise_project(
    project_id: str, body: ReviseIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    """Föreslå ändringar av befintligt manus. Tillämpar inget – klienten godkänner först."""
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    settings = store.load_global_settings(uid)
    api_key = store.load_secrets(uid).get("anthropic_key")
    try:
        result = analyze_mod.revise(
            project, body.instruction, settings, model=body.model, api_key=api_key
        )
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"Revideringen misslyckades: {exc}")
    return {"operations": [op.model_dump() for op in result.operations], "summary": result.summary}


def _run_transcription(
    job_id: str,
    tmp_path: str,
    language: str | None,
    backend: str | None,
    model: str | None,
    openai_key: str | None,
    assemblyai_key: str | None,
    allow_local: bool,
) -> None:
    """Körs i en bakgrundstråd: transkriberar och uppdaterar jobbet."""
    jobs_mod.update_job(job_id, status="running")
    try:
        transcriber = transcribe_mod.get_transcriber(
            backend, model, openai_key=openai_key, assemblyai_key=assemblyai_key, allow_local=allow_local
        )
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
    project_id: str,
    file: UploadFile = File(...),
    language: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    uid: str = Depends(auth_mod.current_uid),
) -> dict:
    """Ladda upp ljud → starta ett transkriberingsjobb i bakgrunden → returnera job_id.

    `backend` väljer motor per anrop; lokala motorer (local/watch) är bara tillgängliga
    i lokalt läge. Klienten pollar status via GET /api/transcribe-jobs/{job_id}.
    """
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    secrets = store.load_secrets(uid)
    suffix = os.path.splitext(file.filename or "")[1] or ".audio"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file.file.read())
            tmp_path = tmp.name
    finally:
        file.file.close()
    job = jobs_mod.create_job()
    threading.Thread(
        target=_run_transcription,
        args=(
            job.id, tmp_path, language, backend, model,
            secrets.get("openai_key"), secrets.get("assemblyai_key"),
            not auth_mod.auth_enabled(),
        ),
        daemon=True,
    ).start()
    return {"job_id": job.id, "status": job.status}


@app.post("/api/import-transcript")
def import_transcript(file: UploadFile = File(...), uid: str = Depends(auth_mod.current_uid)) -> dict:
    """Ta ett färdigt transkript (.txt/.srt/.vtt) från en lokal app → ren text."""
    data = file.file.read()
    file.file.close()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return {"text": transcribe_mod.transcript_to_text(file.filename or "", text)}


@app.get("/api/transcribe-jobs/{job_id}")
def transcribe_job_status(job_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    job = jobs_mod.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Jobbet finns inte")
    return {"job_id": job.id, "status": job.status, "text": job.text, "error": job.error}


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> Response:
    project = store.load_project(uid, project_id)
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
