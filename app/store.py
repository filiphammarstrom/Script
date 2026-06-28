"""JSON-persistens, scoped per användare (ingen databas i V1).

Layout:
  data/users/<uid>/profile.json    – konto (sub, e-post, namn)
  data/users/<uid>/global.json     – bas-AI (globala regler) för användaren
  data/users/<uid>/secrets.json    – användarens egna API-nycklar (gitignorerat under data/)
  data/users/<uid>/projects/*.json – ett manus per fil

I lokalt läge (AUTH_ENABLED=false) används uid "local", och eventuell äldre data
(data/global.json, data/projects/) migreras dit en gång så inget går förlorat.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.models import (
    AnalyzeResult,
    DictateOp,
    DictateResult,
    GlobalSettings,
    Project,
    ScreenplayElement,
    StoryBible,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_DIR = DATA_DIR / "users"
BASE_DIR = DATA_DIR / "base"  # delad "grund" (bas-AI) som admin sätter för alla
LOCAL_UID = "local"

# Äldre (enanvändar-)platser, för engångsmigrering till "local".
_LEGACY_GLOBAL = DATA_DIR / "global.json"
_LEGACY_PROJECTS = DATA_DIR / "projects"


def _safe_uid(uid: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", uid or "")
    return cleaned or LOCAL_UID


def _user_dir(uid: str) -> Path:
    return USERS_DIR / _safe_uid(uid)


def _projects_dir(uid: str) -> Path:
    return _user_dir(uid) / "projects"


def _ensure_user(uid: str) -> None:
    _projects_dir(uid).mkdir(parents=True, exist_ok=True)


# ---- konton ----
def upsert_user(sub: str, email: str = "", name: str = "") -> str:
    """Skapa/uppdatera ett konto utifrån Google-sub. Returnerar uid."""
    uid = _safe_uid(sub)
    _ensure_user(uid)
    path = _user_dir(uid) / "profile.json"
    profile = {"id": uid, "sub": sub, "email": email, "name": name}
    if path.exists():
        import json

        old = json.loads(path.read_text("utf-8"))
        profile = {**old, **{k: v for k, v in profile.items() if v}}
    path.write_text(_dumps(profile), "utf-8")
    return uid


def load_user(uid: str) -> dict | None:
    path = _user_dir(uid) / "profile.json"
    if not path.exists():
        return None
    import json

    return json.loads(path.read_text("utf-8"))


# ---- globala inställningar (bas-AI) per användare ----
def load_global_settings(uid: str) -> GlobalSettings:
    path = _user_dir(uid) / "global.json"
    if path.exists():
        return GlobalSettings.model_validate_json(path.read_text("utf-8"))
    return GlobalSettings()


def save_global_settings(uid: str, settings: GlobalSettings) -> GlobalSettings:
    _ensure_user(uid)
    (_user_dir(uid) / "global.json").write_text(settings.model_dump_json(indent=2), "utf-8")
    return settings


# ---- delad grund (bas-AI som admin sätter för alla) ----
def load_base_settings() -> GlobalSettings:
    path = BASE_DIR / "global.json"
    if path.exists():
        return GlobalSettings.model_validate_json(path.read_text("utf-8"))
    return GlobalSettings()


def save_base_settings(settings: GlobalSettings) -> GlobalSettings:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "global.json").write_text(settings.model_dump_json(indent=2), "utf-8")
    return settings


def effective_global_settings(uid: str) -> GlobalSettings:
    """Slå ihop grunden (gäller alla) med användarens egna tillägg.

    Additivt (användarens val 2a): grunden ligger alltid först och kan inte tas
    bort; användarens egna instruktioner läggs på ovanpå.
    """
    base = load_base_settings()
    own = load_global_settings(uid)
    parts: list[str] = []
    if base.directives.strip():
        parts.append("# GRUND (gäller alla – satt av administratör)\n" + base.directives.strip())
    if own.directives.strip():
        parts.append("# EGNA TILLÄGG (denna användare)\n" + own.directives.strip())
    return GlobalSettings(
        directives="\n\n".join(parts),
        rules_filename=own.rules_filename or base.rules_filename,
    )


# ---- användarens egna API-nycklar ----
def load_secrets(uid: str) -> dict:
    path = _user_dir(uid) / "secrets.json"
    if not path.exists():
        return {}
    import json

    return json.loads(path.read_text("utf-8"))


def save_secrets(uid: str, updates: dict) -> dict:
    """Slå ihop och spara nycklar. Tomma strängar tas bort (= rensa nyckeln)."""
    _ensure_user(uid)
    secrets = load_secrets(uid)
    for key, value in updates.items():
        if value:
            secrets[key] = value
        else:
            secrets.pop(key, None)
    (_user_dir(uid) / "secrets.json").write_text(_dumps(secrets), "utf-8")
    return secrets


# ---- projekt per användare ----
def create_project(uid: str, title: str = "Namnlöst projekt") -> Project:
    return save_project(uid, Project(id=uuid.uuid4().hex[:12], title=title))


def save_project(uid: str, project: Project) -> Project:
    _ensure_user(uid)
    (_projects_dir(uid) / f"{project.id}.json").write_text(
        project.model_dump_json(indent=2), "utf-8"
    )
    return project


def load_project(uid: str, project_id: str) -> Project | None:
    path = _projects_dir(uid) / f"{_safe_uid(project_id)}.json"
    if not path.exists():
        return None
    return Project.model_validate_json(path.read_text("utf-8"))


def delete_project(uid: str, project_id: str) -> bool:
    path = _projects_dir(uid) / f"{_safe_uid(project_id)}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


# ---- versionshistorik (ögonblicksbilder av manuset) ----
def _versions_dir(uid: str, project_id: str) -> Path:
    return _user_dir(uid) / "versions" / _safe_uid(project_id)


def _version_meta(data: dict, fallback_id: str) -> dict:
    els = data.get("elements", [])
    return {
        "id": data.get("id", fallback_id),
        "ts": data.get("ts", ""),
        "label": data.get("label", ""),
        "scenes": sum(1 for e in els if e.get("type") == "scene_heading"),
        "rows": len(els),
    }


def save_version(uid: str, project_id: str, label: str, elements) -> dict:
    """Spara en ögonblicksbild av elementen. label="" = automatisk version."""
    import json
    import time
    from datetime import datetime, timezone

    d = _versions_dir(uid, project_id)
    d.mkdir(parents=True, exist_ok=True)
    vid = str(time.time_ns())
    payload = {
        "id": vid,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "label": (label or "").strip(),
        "elements": [e.model_dump() for e in elements],
    }
    (d / f"{vid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    _prune_versions(d)
    return _version_meta(payload, vid)


def list_versions(uid: str, project_id: str) -> list[dict]:
    import json

    d = _versions_dir(uid, project_id)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in d.glob("*.json"):
        try:
            out.append(_version_meta(json.loads(p.read_text("utf-8")), p.stem))
        except Exception:
            continue
    out.sort(key=lambda v: v["id"], reverse=True)  # nyast först
    return out


def load_version_elements(uid: str, project_id: str, version_id: str):
    import json

    p = _versions_dir(uid, project_id) / f"{_safe_uid(version_id)}.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text("utf-8"))
    return [ScreenplayElement.model_validate(e) for e in data.get("elements", [])]


def _prune_versions(d: Path, keep_auto: int = 30) -> None:
    """Behåll alla namngivna versioner + de senaste `keep_auto` automatiska."""
    import json

    autos = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stem):  # äldst först
        try:
            label = json.loads(p.read_text("utf-8")).get("label", "")
        except Exception:
            label = ""
        if not label:
            autos.append(p)
    for p in autos[:-keep_auto] if len(autos) > keep_auto else []:
        try:
            p.unlink()
        except Exception:
            pass


def list_projects(uid: str) -> list[dict]:
    _ensure_user(uid)
    out: list[dict] = []
    for path in sorted(_projects_dir(uid).glob("*.json")):
        try:
            proj = Project.model_validate_json(path.read_text("utf-8"))
        except Exception:
            continue
        scenes = sum(1 for e in proj.elements if e.type == "scene_heading")
        out.append({"id": proj.id, "title": proj.title, "scenes": scenes})
    return out


# ---- engångsmigrering av äldre enanvändardata till "local" ----
def migrate_legacy() -> None:
    target = _user_dir(LOCAL_UID)
    if target.exists():
        return  # redan migrerat / lokal användare finns
    if not _LEGACY_GLOBAL.exists() and not _LEGACY_PROJECTS.exists():
        return  # inget gammalt att flytta
    _ensure_user(LOCAL_UID)
    if _LEGACY_GLOBAL.exists():
        (target / "global.json").write_text(_LEGACY_GLOBAL.read_text("utf-8"), "utf-8")
    if _LEGACY_PROJECTS.exists():
        for path in _LEGACY_PROJECTS.glob("*.json"):
            (_projects_dir(LOCAL_UID) / path.name).write_text(path.read_text("utf-8"), "utf-8")


def _dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---- sammanfoga AI-resultat in i projektet ----
def merge_analyze_result(project: Project, result: AnalyzeResult) -> Project:
    """Lägg till nya element och slå ihop story-bibel-uppdateringar.

    Nya element (och deras clarifications) numreras om med en offset så att id:n
    blir unika och länkningen clarification -> element bevaras.
    """
    offset = max((e.id for e in project.elements), default=-1) + 1
    for el in result.new_elements:
        el.id += offset
    for clar in result.clarifications:
        clar.element_id += offset
    project.elements.extend(result.new_elements)
    _merge_story_bible(project.story_bible, result.story_bible_updates)
    return project


# ---- dikteringsläge: tillämpa operationer på ett manus i ständig förändring ----
def _next_id(project: Project) -> int:
    return max((e.id for e in project.elements), default=-1) + 1


def _index_of_id(elements: list[ScreenplayElement], target_id) -> int | None:
    for i, el in enumerate(elements):
        if el.id == target_id:
            return i
    return None


def _scene_end_index(elements: list[ScreenplayElement], scene_no: int) -> int | None:
    """Index för SISTA elementet i scen `scene_no` (1-baserat efter scenrubriker).
    None om manuset är tomt; sista index om scennumret pekar bortom slutet."""
    if not elements:
        return None
    seen = 0
    start = None
    for i, el in enumerate(elements):
        if el.type == "scene_heading":
            seen += 1
            if seen == scene_no:
                start = i
            elif seen == scene_no + 1:
                return i - 1  # sista elementet i scenen ligger precis före nästa rubrik
    return len(elements) - 1 if (start is not None or scene_no) else None


def _insert_block(project: Project, after_index: int | None, new_elements) -> None:
    if not new_elements:
        return
    base = _next_id(project)
    block = [ScreenplayElement(id=base + k, **ne.model_dump()) for k, ne in enumerate(new_elements)]
    pos = 0 if after_index is None else after_index + 1
    project.elements[pos:pos] = block


def apply_dict_op(project: Project, op: DictateOp) -> None:
    """Tillämpa EN operation. Nya element får färska id:n; befintliga id:n rörs aldrig,
    så väntande (modifierande) operationer behåller giltiga target_id."""
    els = project.elements
    if op.op == "append":
        _insert_block(project, len(els) - 1 if els else None, op.elements)
    elif op.op == "insert_after_scene":
        _insert_block(project, _scene_end_index(els, op.after_scene or 0), op.elements)
    elif op.op == "insert_after":
        if op.target_id is None:
            _insert_block(project, None, op.elements)  # infoga först
        else:
            idx = _index_of_id(els, op.target_id)
            _insert_block(project, idx if idx is not None else len(els) - 1, op.elements)
    elif op.op == "replace":
        el = next((e for e in els if e.id == op.target_id), None)
        if el is not None:
            if op.text is not None:
                el.text = op.text
            if op.type is not None:
                el.type = op.type
    elif op.op == "delete":
        project.elements = [e for e in els if e.id != op.target_id]


def apply_dictation(project: Project, result: DictateResult) -> tuple[Project, list[DictateOp]]:
    """Tillämpa additiva operationer (lägg till/infoga) direkt och slå ihop story-bibeln.
    Returnerar (projekt, väntande modifierande operationer som kräver godkännande)."""
    pending: list[DictateOp] = []
    for op in result.operations:
        if op.is_additive():
            apply_dict_op(project, op)
        else:
            pending.append(op)
    _merge_story_bible(project.story_bible, result.story_bible_updates)
    return project, pending


def apply_edits(project: Project, operations: list[DictateOp]) -> Project:
    """Tillämpa godkända modifierande operationer (replace/delete)."""
    for op in operations:
        apply_dict_op(project, op)
    return project


def _merge_story_bible(bible: StoryBible, updates: StoryBible) -> None:
    by_name = {c.name.upper(): c for c in bible.characters}
    for c in updates.characters:
        existing = by_name.get(c.name.upper())
        if existing is None:
            bible.characters.append(c)
            by_name[c.name.upper()] = c
            continue
        for alias in c.aliases:
            if alias not in existing.aliases:
                existing.aliases.append(alias)
        for lang in c.languages:
            if lang not in existing.languages:
                existing.languages.append(lang)
        if c.description and not existing.description:
            existing.description = c.description
    for loc in updates.locations:
        if loc not in bible.locations:
            bible.locations.append(loc)
    for note in updates.notes:
        if note not in bible.notes:
            bible.notes.append(note)
