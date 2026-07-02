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

from app import access as access_mod
from app import analyze as analyze_mod
from app import auth as auth_mod
from app import importer as importer_mod
from app import jobs as jobs_mod
from app import prose as prose_mod
from app import store
from app import transcribe as transcribe_mod
from app.fdx import to_fdx
from app.models import DictateOp, GlobalSettings, Project, ScreenplayElement, StoryBible

app = FastAPI(title="Transkription → Manus (FDX)")

# Sessionskakan signeras med SECRET_KEY. I molnläget (AUTH_ENABLED) vore en känd
# default-nyckel ett hål: vem som helst kunde smida en giltig kaka med valfri uid
# och läsa andras projekt och API-nycklar. Vägra därför starta utan riktig nyckel
# när inloggning är på; lokalt enanvändarläge klarar sig med default-nyckeln.
_secret_key = os.environ.get("SECRET_KEY", "")
if not _secret_key:
    if os.environ.get("AUTH_ENABLED", "false").lower() in ("1", "true", "yes"):
        raise RuntimeError(
            "AUTH_ENABLED=true kräver en egen SECRET_KEY (sätt t.ex. SECRET_KEY=$(openssl rand -hex 32))."
        )
    _secret_key = "dev-insecure-change-me"  # lokalt läge: kakan skyddar inget känsligt
app.add_middleware(
    SessionMiddleware,
    secret_key=_secret_key,
    same_site="lax",
    https_only=os.environ.get("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

store.migrate_legacy()  # flytta ev. äldre enanvändardata till "local" en gång


# ---- request-modeller ----
class CreateProjectIn(BaseModel):
    title: str = "Namnlöst projekt"
    kind: str = "screenplay"  # projekttyp, se app/prose.py KINDS


class ProjectUpdateIn(BaseModel):
    title: str | None = None
    author: str | None = None
    contact: str | None = None
    context: str | None = None
    directives: str | None = None
    story_bible: StoryBible | None = None
    elements: list[ScreenplayElement] | None = None
    prose: str | None = None  # prosadokumentet (synopsis/bok/tal ..., se app/prose.py)


class AnalyzeIn(BaseModel):
    text: str
    model: str | None = None
    provider: str | None = None  # 'anthropic' (Claude, default) eller 'openai' (GPT)


class ReviseIn(BaseModel):
    instruction: str
    model: str | None = None
    provider: str | None = None


class ApplyEditsIn(BaseModel):
    operations: list[DictateOp] = []


class AskIn(BaseModel):
    question: str
    model: str | None = None
    provider: str | None = None


class VersionIn(BaseModel):
    label: str = ""


class CommentIn(BaseModel):
    text: str
    scene: int | None = None


class SharedCommentIn(BaseModel):
    author: str = ""
    text: str
    scene: int | None = None


def _author_name(uid: str) -> str:
    if not auth_mod.auth_enabled():
        return "Du"
    user = store.load_user(uid) or {}
    return user.get("name") or user.get("email") or "Användare"


def _ai_key(uid: str, provider: str | None) -> str | None:
    """Användarens egen nyckel för vald AI-motor."""
    secrets = store.load_secrets(uid)
    if (provider or "anthropic").lower() == "openai":
        return secrets.get("openai_key")
    return secrets.get("anthropic_key")


class SettingsIn(BaseModel):
    directives: str = ""
    rules_filename: str = ""


class SecretsIn(BaseModel):
    anthropic_key: str | None = None
    openai_key: str | None = None
    assemblyai_key: str | None = None
    groq_key: str | None = None
    deepgram_key: str | None = None


class GoogleLoginIn(BaseModel):
    credential: str


class EmailIn(BaseModel):
    email: str


class AdminFlagIn(BaseModel):
    email: str
    is_admin: bool = True


def _current_email(uid: str) -> str:
    return (store.load_user(uid) or {}).get("email", "")


def require_admin(uid: str = Depends(auth_mod.current_uid)) -> str:
    """FastAPI-beroende: släpper bara igenom administratörer (lokal ägare = admin)."""
    if not auth_mod.auth_enabled():
        return uid  # lokalt enanvändarläge: ägaren är admin
    if not access_mod.is_admin(_current_email(uid)):
        raise HTTPException(403, "Endast administratör har åtkomst till detta.")
    return uid


# ---- inloggning / konto ----
@app.get("/api/config")
def get_config() -> dict:
    return {"auth_enabled": auth_mod.auth_enabled(), "google_client_id": auth_mod.google_client_id()}


@app.get("/api/me")
def get_me(uid: str = Depends(auth_mod.current_uid)) -> dict:
    if not auth_mod.auth_enabled():
        return {"id": uid, "name": "Lokal användare", "email": "", "auth_enabled": False, "is_admin": True}
    user = store.load_user(uid) or {"id": uid}
    return {**user, "auth_enabled": True, "is_admin": access_mod.is_admin(user.get("email", ""))}


@app.post("/auth/google")
def auth_google(body: GoogleLoginIn, request: Request) -> dict:
    try:
        info = auth_mod.verify_google_id_token(body.credential)
    except ValueError as exc:
        raise HTTPException(401, str(exc))
    if not access_mod.is_allowed(info["email"]):
        raise HTTPException(
            403,
            f"Kontot {info['email']} har inte åtkomst till ScriptVoice ännu. "
            "Be administratören att bjuda in din e-postadress.",
        )
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


# ---- delad grund (bas-AI) + admin/åtkomst ----
@app.get("/api/base-settings")
def get_base_settings(uid: str = Depends(require_admin)) -> GlobalSettings:
    """Endast admin. Grunden tillämpas på alla men dess innehåll är hemligt –
    den slås ihop server-side och returneras aldrig till vanliga användare."""
    return store.load_base_settings()


@app.put("/api/base-settings")
def put_base_settings(body: SettingsIn, uid: str = Depends(require_admin)) -> GlobalSettings:
    return store.save_base_settings(
        GlobalSettings(directives=body.directives, rules_filename=body.rules_filename)
    )


@app.get("/api/admin/access")
def get_access(uid: str = Depends(require_admin)) -> dict:
    return access_mod.snapshot()


@app.post("/api/admin/access/allow")
def access_allow(body: EmailIn, uid: str = Depends(require_admin)) -> dict:
    access_mod.add_allowed(body.email)
    return access_mod.snapshot()


@app.post("/api/admin/access/remove")
def access_remove(body: EmailIn, uid: str = Depends(require_admin)) -> dict:
    _guard_last_admin(body.email)
    access_mod.remove_allowed(body.email)
    return access_mod.snapshot()


def _guard_last_admin(email: str) -> None:
    """Stoppa att den SISTA adminen tas bort/degraderas – utan admins stängs
    allowlisten av helt (access_enabled() -> False) och vem som helst med ett
    Google-konto kan logga in."""
    remaining = {e for e in access_mod.admin_emails() if e != (email or "").strip().lower()}
    if not remaining:
        raise HTTPException(400, "Det går inte att ta bort den sista administratören – då öppnas appen för alla.")


@app.post("/api/admin/access/admin")
def access_set_admin(body: AdminFlagIn, uid: str = Depends(require_admin)) -> dict:
    if not body.is_admin:
        _guard_last_admin(body.email)
    access_mod.set_admin(body.email, body.is_admin)
    return access_mod.snapshot()


# ---- användarens egna API-nycklar ----
@app.get("/api/secrets")
def get_secrets(uid: str = Depends(auth_mod.current_uid)) -> dict:
    """Returnerar bara HUR-vida varje nyckel är satt – aldrig själva nyckeln."""
    s = store.load_secrets(uid)
    return {
        "anthropic": bool(s.get("anthropic_key")),
        "openai": bool(s.get("openai_key")),
        "assemblyai": bool(s.get("assemblyai_key")),
        "groq": bool(s.get("groq_key")),
        "deepgram": bool(s.get("deepgram_key")),
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
    if body.kind not in prose_mod.KINDS:
        raise HTTPException(400, f"Okänd projekttyp: {body.kind!r}")
    return store.create_project(uid, body.title, kind=body.kind)


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
    settings = store.effective_global_settings(uid)
    try:
        result = analyze_mod.analyze(
            project, body.text, settings,
            model=body.model, api_key=_ai_key(uid, body.provider), provider=body.provider or "anthropic",
        )
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"AI-analysen misslyckades: {exc}")
    # AI-anropet tar tiotals sekunder – hämta en FÄRSK kopia innan resultatet
    # tillämpas, annars skrivs redigeringar som autosparats under tiden över.
    project = store.load_project(uid, project_id) or project
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
    settings = store.effective_global_settings(uid)
    try:
        result = analyze_mod.revise(
            project, body.instruction, settings,
            model=body.model, api_key=_ai_key(uid, body.provider), provider=body.provider or "anthropic",
        )
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"Revideringen misslyckades: {exc}")
    return {"operations": [op.model_dump() for op in result.operations], "summary": result.summary}


@app.post("/api/projects/{project_id}/dictate")
def dictate_project(
    project_id: str, body: AnalyzeIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    """En enda dikteringsruta: tolka dikteringen och bygg om manuset.

    Additiva operationer (lägg till/infoga) tillämpas direkt och sparas. Modifierande
    operationer (ändra/ta bort befintligt) returneras som `pending_ops` för godkännande.
    """
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    settings = store.effective_global_settings(uid)
    try:
        result = analyze_mod.dictate(
            project, body.text, settings,
            model=body.model, api_key=_ai_key(uid, body.provider), provider=body.provider or "anthropic",
        )
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"Dikteringen misslyckades: {exc}")
    # Färsk kopia efter det långa AI-anropet – operationerna är id-baserade och
    # tillämpas säkert på det senaste innehållet (autosave-race, se analyze ovan).
    project = store.load_project(uid, project_id) or project
    store.save_version(uid, project_id, "", project.elements, prose=project.prose)  # auto-snapshot före diktering
    project, pending = store.apply_dictation(project, result)
    store.save_project(uid, project)
    return {
        "project": project,
        "pending_ops": [op.model_dump() for op in pending],
        "clarifications": result.clarifications,
        "summary": result.summary,
    }


@app.post("/api/projects/{project_id}/dictate-prose")
def dictate_prose_project(
    project_id: str, body: AnalyzeIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    """Diktering till prosadokumentet (storyline/synopsis, bok, tal ... – se app/prose.py).

    AI:n formaterar dikteringen som löpande text enligt projekttypens guide;
    resultatet tillämpas direkt (append, eller replace_all vid uttrycklig ändring)
    och sparas. Klienten behåller en egen ångra-kopia.
    """
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    settings = store.effective_global_settings(uid)
    try:
        result = prose_mod.dictate_prose(
            project, body.text, settings,
            model=body.model, api_key=_ai_key(uid, body.provider), provider=body.provider or "anthropic",
        )
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"Dikteringen misslyckades: {exc}")
    # Färsk kopia efter AI-anropet + auto-snapshot INNAN resultatet tillämpas:
    # ett replace_all kan skriva om hela dokumentet och måste gå att återställa.
    project = store.load_project(uid, project_id) or project
    store.save_version(uid, project_id, "", project.elements, prose=project.prose)
    project.prose = prose_mod.apply_prose(project.prose, result)
    store.save_project(uid, project)
    return {"project": project, "summary": result.summary}


@app.post("/api/projects/{project_id}/apply-edits")
def apply_edits_project(
    project_id: str, body: ApplyEditsIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    """Tillämpa godkända modifierande operationer (ändra/ta bort) från en diktering."""
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    store.save_version(uid, project_id, "", project.elements, prose=project.prose)  # auto-snapshot före ändring
    store.apply_edits(project, body.operations)
    store.save_project(uid, project)
    return {"project": project}


@app.get("/api/projects/{project_id}/versions")
def get_versions(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    return {"versions": store.list_versions(uid, project_id)}


@app.post("/api/projects/{project_id}/versions")
def create_version(
    project_id: str, body: VersionIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    meta = store.save_version(uid, project_id, body.label or "Sparad version", project.elements, prose=project.prose)
    return {"version": meta, "versions": store.list_versions(uid, project_id)}


@app.post("/api/projects/{project_id}/versions/{version_id}/restore")
def restore_version(
    project_id: str, version_id: str, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    version = store.load_version(uid, project_id, version_id)
    if version is None:
        raise HTTPException(404, "Versionen finns inte")
    store.save_version(uid, project_id, "Före återställning", project.elements, prose=project.prose)  # ångerbar
    project.elements = version["elements"]
    if version["prose"] is not None:  # gamla versioner saknar prose – rör då inte dokumentet
        project.prose = version["prose"]
    store.save_project(uid, project)
    return {"project": project, "versions": store.list_versions(uid, project_id)}


@app.get("/api/projects/{project_id}/comments")
def get_comments(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    return {"comments": store.list_comments(uid, project_id)}


@app.post("/api/projects/{project_id}/comments")
def add_comment_endpoint(
    project_id: str, body: CommentIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    if not body.text.strip():
        raise HTTPException(400, "Tom kommentar.")
    comments = store.add_comment(uid, project_id, _author_name(uid), body.text.strip(), body.scene)
    return {"comments": comments}


@app.delete("/api/projects/{project_id}/comments/{comment_id}")
def delete_comment_endpoint(
    project_id: str, comment_id: str, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    return {"comments": store.delete_comment(uid, project_id, comment_id)}


# ---- skrivskyddad delning + tittarkommentarer ----
@app.get("/api/projects/{project_id}/share")
def get_share(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    return {"token": store.share_token_for(uid, project_id)}


@app.post("/api/projects/{project_id}/share")
def create_share_endpoint(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    return {"token": store.create_share(uid, project_id)}


@app.delete("/api/projects/{project_id}/share")
def revoke_share_endpoint(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> dict:
    if store.load_project(uid, project_id) is None:
        raise HTTPException(404, "Projektet finns inte")
    store.revoke_share(uid, project_id)
    return {"token": None}


def _resolve_share_or_404(token: str) -> tuple[str, str, Project]:
    """Slå upp en delningstoken → (ägar-uid, projekt-id, projekt) eller 404."""
    ref = store.resolve_share(token)
    if not ref:
        raise HTTPException(404, "Delningslänken finns inte eller har återkallats.")
    owner, pid = ref["uid"], ref["project_id"]
    project = store.load_project(owner, pid)
    if project is None:
        raise HTTPException(404, "Det delade projektet finns inte längre.")
    return owner, pid, project


@app.get("/api/shared/{token}")
def get_shared(token: str) -> dict:
    """Skrivskyddad vy av ett delat manus – ingen inloggning krävs (token = nyckeln)."""
    owner, pid, project = _resolve_share_or_404(token)
    return {
        "title": project.title,
        "author": project.author,
        "kind": project.kind,
        "prose": project.prose,
        "elements": [e.model_dump() for e in project.elements],
        "comments": store.list_comments(owner, pid),
    }


@app.get("/api/shared/{token}/comments")
def get_shared_comments(token: str) -> dict:
    owner, pid, _ = _resolve_share_or_404(token)
    return {"comments": store.list_comments(owner, pid)}


@app.post("/api/shared/{token}/comments")
def add_shared_comment(token: str, body: SharedCommentIn) -> dict:
    """En tittare lämnar en kommentar. Den hamnar i ägarens kommentarslista."""
    owner, pid, _ = _resolve_share_or_404(token)
    if not body.text.strip():
        raise HTTPException(400, "Tom kommentar.")
    author = body.author.strip() or "Gäst"
    comments = store.add_comment(owner, pid, author, body.text.strip(), body.scene)
    return {"comments": comments}


@app.post("/api/projects/{project_id}/ask")
def ask_project(
    project_id: str, body: AskIn, uid: str = Depends(auth_mod.current_uid)
) -> dict:
    """Fritextfråga om manuset – AI:n svarar utifrån innehållet."""
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    if not body.question.strip():
        raise HTTPException(400, "Tom fråga.")
    try:
        answer = analyze_mod.ask(
            project, body.question,
            model=body.model, api_key=_ai_key(uid, body.provider), provider=body.provider or "anthropic",
        )
    except Exception as exc:  # saknad API-nyckel, nätverksfel, modellfel ...
        raise HTTPException(502, f"Frågan misslyckades: {exc}")
    return {"answer": answer}


def _run_transcription(
    job_id: str,
    tmp_path: str,
    language: str | None,
    backend: str | None,
    model: str | None,
    openai_key: str | None,
    assemblyai_key: str | None,
    groq_key: str | None,
    deepgram_key: str | None,
    allow_local: bool,
) -> None:
    """Körs i en bakgrundstråd: transkriberar och uppdaterar jobbet."""
    jobs_mod.update_job(job_id, status="running")
    trimmed_path = None
    try:
        resolved_backend = transcribe_mod.resolve_backend_name(backend)
        transcriber = transcribe_mod.get_transcriber(
            resolved_backend, model,
            openai_key=openai_key, assemblyai_key=assemblyai_key, groq_key=groq_key,
            deepgram_key=deepgram_key, allow_local=allow_local,
        )
        audio_path = tmp_path
        if transcribe_mod.should_trim_silence(resolved_backend):
            jobs_mod.update_job(job_id, progress="Rensar tystnad ...")
            trimmed_path = transcribe_mod.trim_silence(tmp_path)
            if trimmed_path:
                audio_path = trimmed_path
            jobs_mod.update_job(job_id, progress="")

        def _on_progress(i: int, n: int) -> None:
            jobs_mod.update_job(job_id, progress=f"Del {i} av {n}")

        text = transcribe_mod.transcribe_with_chunking(
            transcriber, audio_path, resolved_backend, language=language, on_progress=_on_progress
        )
        jobs_mod.update_job(job_id, status="done", text=text, progress="")
    except Exception as exc:  # saknad nyckel, nätverksfel, transkriberingsfel ...
        jobs_mod.update_job(job_id, status="error", error=str(exc))
    finally:
        for p in (tmp_path, trimmed_path):
            if p:
                try:
                    os.unlink(p)
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
    job = jobs_mod.create_job(uid=uid)
    threading.Thread(
        target=_run_transcription,
        args=(
            job.id, tmp_path, language, backend, model,
            secrets.get("openai_key"), secrets.get("assemblyai_key"), secrets.get("groq_key"),
            secrets.get("deepgram_key"),
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
    if job is None or (job.uid and job.uid != uid):  # andras transkript ska inte gå att läsa
        raise HTTPException(404, "Jobbet finns inte")
    return {"job_id": job.id, "status": job.status, "text": job.text, "error": job.error, "progress": job.progress}


@app.post("/api/projects/{project_id}/import")
def import_screenplay(
    project_id: str, file: UploadFile = File(...), uid: str = Depends(auth_mod.current_uid)
) -> dict:
    """Importera ett befintligt manus (FDX eller Fountain) och lägg till i slutet."""
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    if project.kind != "screenplay":
        # Manuselement visas aldrig i ett prosaprojekt – en import skulle bara
        # lägga osynlig data som förvirrar (scenräknare i listan m.m.).
        raise HTTPException(400, "Manusimport (FDX/Fountain) gäller bara manusprojekt.")
    data = file.file.read()
    file.file.close()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    try:
        parsed = importer_mod.parse_screenplay(file.filename or "", text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not parsed:
        raise HTTPException(400, "Hittade inget manusinnehåll i filen.")
    store.save_version(uid, project_id, "Före import", project.elements, prose=project.prose)  # ångerbar
    next_id = max((e.id for e in project.elements), default=-1) + 1
    for item in parsed:
        project.elements.append(ScreenplayElement(
            id=next_id, type=item["type"], text=item["text"],
            scene_number=item.get("scene_number"), dual=item.get("dual", False),
        ))
        next_id += 1
    store.save_project(uid, project)
    return {"project": project, "added": len(parsed)}


@app.post("/api/projects/{project_id}/export")
def export_project(project_id: str, uid: str = Depends(auth_mod.current_uid)) -> Response:
    project = store.load_project(uid, project_id)
    if project is None:
        raise HTTPException(404, "Projektet finns inte")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (project.title or "manus"))
    if project.kind != "screenplay":
        # Prosaprojekt (bok/tal/pitch ...): dokumentet som ren text – en tom
        # FDX-fil vore meningslös för den som dikterat en hel bok.
        body = (project.title + "\n\n" if project.title else "") + project.prose
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe or "text"}.txt"'},
        )
    xml = to_fdx(project.elements, title=project.title, author=project.author, contact=project.contact)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{safe or "manus"}.fdx"'},
    )


# ---- frontend ----
@app.get("/healthz")
def healthz() -> dict:
    """Lättviktig hälsokoll för hostingplattformen (ingen inloggning, ingen fil)."""
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
