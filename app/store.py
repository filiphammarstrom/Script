"""Enkel JSON-persistens för projekt och globala inställningar (ingen databas i V1).

Varje projekt är en egen fil i data/projects/, så man kan ha flera manus parallellt
och återuppta exakt där man var.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.models import AnalyzeResult, GlobalSettings, Project, StoryBible

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROJECTS_DIR = DATA_DIR / "projects"
GLOBAL_FILE = DATA_DIR / "global.json"


def _ensure_dirs() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


# ---- globala inställningar (bas-AI) ----
def load_global_settings() -> GlobalSettings:
    if GLOBAL_FILE.exists():
        return GlobalSettings.model_validate_json(GLOBAL_FILE.read_text("utf-8"))
    return GlobalSettings()


def save_global_settings(settings: GlobalSettings) -> GlobalSettings:
    _ensure_dirs()
    GLOBAL_FILE.write_text(settings.model_dump_json(indent=2), "utf-8")
    return settings


# ---- projekt ----
def create_project(title: str = "Namnlöst projekt") -> Project:
    return save_project(Project(id=uuid.uuid4().hex[:12], title=title))


def save_project(project: Project) -> Project:
    _ensure_dirs()
    (PROJECTS_DIR / f"{project.id}.json").write_text(
        project.model_dump_json(indent=2), "utf-8"
    )
    return project


def load_project(project_id: str) -> Project | None:
    path = PROJECTS_DIR / f"{project_id}.json"
    if not path.exists():
        return None
    return Project.model_validate_json(path.read_text("utf-8"))


def list_projects() -> list[dict]:
    _ensure_dirs()
    out: list[dict] = []
    for path in sorted(PROJECTS_DIR.glob("*.json")):
        try:
            proj = Project.model_validate_json(path.read_text("utf-8"))
        except Exception:
            continue
        scenes = sum(1 for e in proj.elements if e.type == "scene_heading")
        out.append({"id": proj.id, "title": proj.title, "scenes": scenes})
    return out


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
