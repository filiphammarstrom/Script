"""Pydantic-modeller för den strukturerade manusrepresentationen.

Representationen är "sanningen" i appen: Claude producerar den, användaren kan
redigera den, och FDX genereras deterministiskt från den (se app/fdx.py).
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _as_list(v):
    """Vissa modeller returnerar nästlade listfält som en JSON-*sträng*. Tolka
    strängen som JSON innan validering; tom/ogiltig sträng blir tom lista."""
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            return json.loads(s)
        except Exception:
            return []
    return v


def _as_obj(v):
    """Som _as_list men för ett objektfält (tom/ogiltig sträng -> tomt objekt)."""
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            return {}
    return v

# Våra elementtyper motsvarar Final Drafts Paragraph-typer (se app/fdx.py).
ElementType = Literal[
    "scene_heading",
    "action",
    "character",
    "dialogue",
    "parenthetical",
    "transition",
    "general",
]


class ScreenplayElement(BaseModel):
    id: int
    type: ElementType
    text: str
    confidence: Literal["high", "medium", "low"] = "high"
    # Markerar en medveten lucka i manuset. AI:n hittar aldrig på för att fylla den.
    is_gap: bool = False


class Clarification(BaseModel):
    """En konkret fråga AI:n ställer när den var osäker, kopplad till ett element."""

    element_id: int
    question: str
    options: list[str] = Field(default_factory=list)


class Character(BaseModel):
    name: str  # kanoniskt namn, skrivs i VERSALER i manus
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    # Språk karaktären talar. Används för att flagga möjliga feldikteringar.
    languages: list[str] = Field(default_factory=list)


class StoryBible(BaseModel):
    """Etablerade fakta som hålls konsekventa över sessioner – AI:ns 'minne' av projektet."""

    characters: list[Character] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)  # kanoniska scenrubrik-slugs
    notes: list[str] = Field(default_factory=list)

    @field_validator("characters", "locations", "notes", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _as_list(v)


class Project(BaseModel):
    id: str
    title: str = "Namnlöst projekt"
    author: str = ""  # för titelsidan i exporten
    contact: str = ""  # kontaktuppgifter (titelsidan), en rad per rad
    context: str = ""  # synopsis/bakgrund
    directives: str = ""  # användarens stående instruktioner för DETTA projekt
    story_bible: StoryBible = Field(default_factory=StoryBible)
    elements: list[ScreenplayElement] = Field(default_factory=list)


class GlobalSettings(BaseModel):
    """'Bas-AI:n' – instruktioner som gäller ALLA projekt."""

    directives: str = ""
    rules_filename: str = ""  # namnet på den senast uppladdade regel-/formatboken (visas i UI:t)


# --- Modellens strukturerade output från analyssteget ---


class AnalyzeResult(BaseModel):
    new_elements: list[ScreenplayElement] = Field(default_factory=list)
    story_bible_updates: StoryBible = Field(default_factory=StoryBible)
    clarifications: list[Clarification] = Field(default_factory=list)

    @field_validator("new_elements", "clarifications", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _as_list(v)

    @field_validator("story_bible_updates", mode="before")
    @classmethod
    def _coerce_bible(cls, v):
        return _as_obj(v)


# --- Revideringsläge: föreslagna ändringar av BEFINTLIGT manus ---


class EditOp(BaseModel):
    """En enskild, exakt redigering av ett befintligt element (pekar på dess id)."""

    op: Literal["replace", "delete", "insert_after"]
    target_id: int | None = None  # element att ersätta/ta bort, eller infoga EFTER (null = först)
    type: ElementType | None = None  # för replace (om typen ändras) och insert_after
    text: str | None = None  # för replace och insert_after
    reason: str = ""  # kort förklaring på svenska som visas för användaren


class ReviseResult(BaseModel):
    """AI:ns förslag på ändringar – tillämpas först efter användarens godkännande."""

    operations: list[EditOp] = Field(default_factory=list)
    summary: str = ""

    @field_validator("operations", mode="before")
    @classmethod
    def _coerce_ops(cls, v):
        return _as_list(v)


# --- Dikteringsläge: ett manus i ständig förändring (lägg till / infoga / ändra / ta bort) ---


class NewElement(BaseModel):
    """Ett nytt element som AI:n vill skriva in (utan id – servern numrerar)."""

    type: ElementType
    text: str = ""
    confidence: Literal["high", "medium", "low"] = "high"
    is_gap: bool = False


class DictateOp(BaseModel):
    """En operation på manuset. Additiva (append/insert_*) tillämpas direkt;
    modifierande (replace/delete) av befintligt innehåll kräver godkännande."""

    op: Literal["append", "insert_after", "insert_after_scene", "replace", "delete"]
    target_id: int | None = None      # insert_after / replace / delete: elementets id
    after_scene: int | None = None    # insert_after_scene: scennummer (1-baserat)
    type: ElementType | None = None   # replace: ny typ (om den ändras)
    text: str | None = None           # replace: ny text
    elements: list[NewElement] = Field(default_factory=list)  # append / insert_*: nya element
    reason: str = ""                  # kort förklaring (svenska) som visas för användaren

    @field_validator("elements", mode="before")
    @classmethod
    def _coerce_elements(cls, v):
        return _as_list(v)

    def is_additive(self) -> bool:
        return self.op in ("append", "insert_after", "insert_after_scene")


class DictateResult(BaseModel):
    """AI:ns tolkning av en diktering: blandning av tillägg, infogningar och ändringar."""

    operations: list[DictateOp] = Field(default_factory=list)
    story_bible_updates: StoryBible = Field(default_factory=StoryBible)
    clarifications: list[Clarification] = Field(default_factory=list)
    summary: str = ""

    @field_validator("operations", "clarifications", mode="before")
    @classmethod
    def _coerce_lists(cls, v):
        return _as_list(v)

    @field_validator("story_bible_updates", mode="before")
    @classmethod
    def _coerce_bible(cls, v):
        return _as_obj(v)
