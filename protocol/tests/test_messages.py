from __future__ import annotations

from reachy_ducky_protocol.messages import (
    BrainRequest,
    BrainResponse,
    HealthResponse,
    SpecialistRequest,
    State,
    UserUtterance,
)


def test_brain_request_serializes() -> None:
    req = BrainRequest(
        user_utterance="what's on my branch?",
        project_slug="reachy-ducky",
        include_tools=["git", "gh", "fs", "plans"],
    )
    data = req.model_dump()
    assert data["user_utterance"] == "what's on my branch?"
    assert "git" in data["include_tools"]


def test_brain_response_round_trip() -> None:
    resp = BrainResponse(text="you have 3 commits ahead of main", specialist_invoked=None)
    clone = BrainResponse.model_validate_json(resp.model_dump_json())
    assert clone.text == resp.text


def test_specialist_request_plan_reviewer() -> None:
    req = SpecialistRequest(
        name="plan-reviewer",
        project_slug="reachy-ducky",
        branch="main",
    )
    assert req.name == "plan-reviewer"


def test_state_enum_values() -> None:
    assert State.IDLE.value == "idle"
    assert State.LISTENING.value == "listening"
    assert State.THINKING.value == "thinking"
    assert State.MUTED.value == "muted"


def test_user_utterance_and_health_response_importable() -> None:
    # Named by the task spec as importable symbols; exercise a minimal construct
    # so unused-import lint stays clean and the symbols are verified to exist.
    utt = UserUtterance(text="hello")
    health = HealthResponse(ok=True, brain="claude", memory_ready=True)
    assert utt.text == "hello"
    assert health.ok is True
