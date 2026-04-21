"""Tests for the FastAPI server's POST /brain/query endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.server import create_app


def test_brain_query_round_trip(tmp_path: Path) -> None:
    """POST /brain/query forwards the utterance to the brain and returns its text."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hello"})
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hello"


def test_brain_query_with_matching_token_reaches_route(tmp_path: Path) -> None:
    """With auth_token set, a correct Bearer header reaches the route and returns 200."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)
    r = client.post(
        "/brain/query",
        json={"user_utterance": "hi"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hi"


def test_brain_query_invokes_brain_once(tmp_path: Path) -> None:
    """Each POST /brain/query results in exactly one brain.query() call."""
    brain = MockBrain()
    app = create_app(brain=brain, memory_root=tmp_path)
    client = TestClient(app)
    client.post("/brain/query", json={"user_utterance": "one"})
    client.post("/brain/query", json={"user_utterance": "two"})
    assert len(brain.calls) == 2
    assert brain.calls[0].user_utterance == "one"
    assert brain.calls[1].user_utterance == "two"


def test_brain_query_rejects_missing_user_utterance(tmp_path: Path) -> None:
    """Missing required field returns 422 (Pydantic validation)."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={})
    assert r.status_code == 422


def test_brain_query_rejects_unknown_fields(tmp_path: Path) -> None:
    """Extra fields in the request body are rejected by the wire contract."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hi", "secret_key": "x"})
    assert r.status_code == 422


def test_brain_query_response_shape(tmp_path: Path) -> None:
    """Response matches BrainResponse schema exactly."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hi"})
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"text", "specialist_invoked"}
    assert data["specialist_invoked"] is None
