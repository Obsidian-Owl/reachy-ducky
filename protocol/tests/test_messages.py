from __future__ import annotations

import pytest
from pydantic import ValidationError
from reachy_ducky_protocol.messages import (
    BrainRequest,
    BrainResponse,
    HealthResponse,
    SpecialistRequest,
    State,
    UserUtterance,
)


def test_brain_request_serializes() -> None:
    """BrainRequest serializes its fields into a dict via model_dump."""
    req = BrainRequest(
        user_utterance="what's on my branch?",
        project_slug="reachy-ducky",
        include_tools=["git", "gh", "fs", "plans"],
    )
    data = req.model_dump()
    assert data["user_utterance"] == "what's on my branch?"
    assert "git" in data["include_tools"]


def test_brain_response_round_trip() -> None:
    """BrainResponse survives JSON round-trip with field values intact."""
    resp = BrainResponse(text="you have 3 commits ahead of main", specialist_invoked=None)
    clone = BrainResponse.model_validate_json(resp.model_dump_json())
    assert clone.text == resp.text


def test_specialist_request_plan_reviewer() -> None:
    """SpecialistRequest carries the specialist name, project, and branch."""
    req = SpecialistRequest(
        name="plan-reviewer",
        project_slug="reachy-ducky",
        branch="main",
    )
    assert req.name == "plan-reviewer"


def test_specialist_request_pr_number_defaults_to_none() -> None:
    """pr_number is optional on SpecialistRequest — omission leaves it None."""
    req = SpecialistRequest(name="pr-reviewer", project_slug="reachy-ducky")
    assert req.pr_number is None


def test_specialist_request_accepts_pr_number() -> None:
    """pr_number is a plain int when supplied."""
    req = SpecialistRequest(name="pr-reviewer", project_slug="reachy-ducky", pr_number=42)
    assert req.pr_number == 42


def test_specialist_request_rejects_non_int_pr_number() -> None:
    """pr_number must be int-coercible — non-numeric strings fail validation.

    Pydantic v2 lax-mode coerces ``"42"`` to ``42``, which is consistent with
    the rest of the protocol. This test pins the int-valued-ness of the field
    against an input Pydantic cannot coerce.
    """
    with pytest.raises(ValidationError):
        SpecialistRequest.model_validate(
            {
                "name": "pr-reviewer",
                "project_slug": "reachy-ducky",
                "pr_number": "not-a-number",
            }
        )


def test_state_enum_values() -> None:
    """State enum serializes to the lowercase strings the voice app receives."""
    assert State.IDLE.value == "idle"
    assert State.LISTENING.value == "listening"
    assert State.THINKING.value == "thinking"
    assert State.MUTED.value == "muted"


def test_user_utterance_defaults_project_slug_to_none() -> None:
    """UserUtterance omits optional project_slug unless provided."""
    utt = UserUtterance(text="hello")
    assert utt.text == "hello"
    assert utt.project_slug is None


def test_health_response_round_trip() -> None:
    """HealthResponse survives JSON round-trip with all fields intact."""
    resp = HealthResponse(
        ok=True,
        brain="claude",
        memory_ready=True,
        projects=["reachy-ducky", "other"],
    )
    clone = HealthResponse.model_validate_json(resp.model_dump_json())
    assert clone == resp


def test_health_response_projects_defaults_to_empty() -> None:
    """``projects`` defaults to an empty list so pre-registry callers still validate."""
    resp = HealthResponse(ok=True, brain="Mock", memory_ready=False)
    assert resp.projects == []


def test_health_response_carries_project_slugs() -> None:
    """The registry surfaces its watched slugs through the health contract."""
    resp = HealthResponse(
        ok=True,
        brain="MockBrain",
        memory_ready=True,
        projects=["alpha", "beta"],
    )
    assert resp.projects == ["alpha", "beta"]


def test_brain_request_rejects_unknown_fields() -> None:
    """Wire contract forbids extra fields so schema drift surfaces as an error."""
    with pytest.raises(ValidationError):
        BrainRequest.model_validate({"user_utterance": "hi", "secret_key": "leak"})
