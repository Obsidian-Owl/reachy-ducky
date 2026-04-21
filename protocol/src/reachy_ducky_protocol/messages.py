from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _WireMessage(BaseModel):
    """Common base for the daemon↔app wire contract. Forbids unknown fields
    so schema drift and typos raise ValidationError at the boundary."""

    model_config = ConfigDict(extra="forbid")


class _WireResponse(_WireMessage):
    """Response-flavored wire message; immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class State(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    MUTED = "muted"


class UserUtterance(_WireMessage):
    text: str
    project_slug: str | None = None


class BrainRequest(_WireMessage):
    user_utterance: str
    project_slug: str | None = None
    include_tools: list[str] = Field(default_factory=list)


class BrainResponse(_WireResponse):
    text: str
    specialist_invoked: str | None = None


class SpecialistRequest(_WireMessage):
    name: str
    project_slug: str
    branch: str | None = None


class SpecialistResponse(_WireResponse):
    name: str
    summary: str
    flags: list[str] = Field(default_factory=list)


class HealthResponse(_WireResponse):
    ok: bool
    brain: str
    memory_ready: bool
