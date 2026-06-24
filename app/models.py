"""Pydantic-modeller för den strukturerade manusrepresentationen.

Representationen är "sanningen" i appen: Claude producerar den, användaren kan
redigera den, och FDX genereras deterministiskt från den (se app/fdx.py).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

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


class Project(BaseModel):
    id: str
    title: str = "Namnlöst projekt"
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
