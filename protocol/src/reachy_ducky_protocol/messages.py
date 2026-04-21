from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    MUTED = "muted"


class UserUtterance(BaseModel):
    text: str
    project_slug: str | None = None


class BrainRequest(BaseModel):
    user_utterance: str
    project_slug: str | None = None
    include_tools: list[str] = Field(default_factory=list)


class BrainResponse(BaseModel):
    text: str
    specialist_invoked: str | None = None


class SpecialistRequest(BaseModel):
    name: str
    project_slug: str
    branch: str | None = None


class SpecialistResponse(BaseModel):
    name: str
    summary: str
    flags: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    ok: bool
    brain: str
    memory_ready: bool
