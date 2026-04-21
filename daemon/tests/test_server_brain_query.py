"""Tests for the FastAPI server's POST /brain/query endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from reachy_ducky_daemon.brain.interface import BrainInterface
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.brain.registry import BrainRegistry
from reachy_ducky_daemon.project import Project
from reachy_ducky_daemon.server import create_app


def test_brain_query_round_trip(tmp_path: Path) -> None:
    """POST /brain/query forwards the utterance to the brain and returns its text."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hello"})
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hello"


def test_brain_query_with_matching_token_reaches_route(tmp_path: Path) -> None:
    """With auth_token set, a correct Bearer header reaches the route and returns 200."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)
    r = client.post(
        "/brain/query",
        json={"user_utterance": "hi"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hi"


def test_brain_query_invokes_brain_once(tmp_path: Path) -> None:
    """Each POST /brain/query results in exactly one brain.query() call.

    The registry builds a single cached brain on first access; subsequent
    calls reuse the same instance so the calls list accumulates properly.
    """
    brain = MockBrain()
    registry = BrainRegistry(
        projects=[Project(slug="repo", path=tmp_path / "repo", primary=True)],
        build_brain=lambda _: brain,
    )
    (tmp_path / "repo").mkdir()
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    client.post("/brain/query", json={"user_utterance": "one"})
    client.post("/brain/query", json={"user_utterance": "two"})
    assert len(brain.calls) == 2
    assert brain.calls[0].user_utterance == "one"
    assert brain.calls[1].user_utterance == "two"


def test_brain_query_rejects_missing_user_utterance(tmp_path: Path) -> None:
    """Missing required field returns 422 (Pydantic validation)."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={})
    assert r.status_code == 422


def test_brain_query_rejects_unknown_fields(tmp_path: Path) -> None:
    """Extra fields in the request body are rejected by the wire contract."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hi", "secret_key": "x"})
    assert r.status_code == 422


def test_brain_query_response_shape(tmp_path: Path) -> None:
    """Response matches BrainResponse schema exactly."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hi"})
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"text", "specialist_invoked"}
    assert data["specialist_invoked"] is None


# ---------------------------------------------------------------------------
# Multi-project routing: slug fallback + unknown slug
# ---------------------------------------------------------------------------


def test_brain_query_without_slug_uses_primary(tmp_path: Path) -> None:
    """A slug-less request routes to the registry's primary project's brain."""
    brain = MockBrain()
    (tmp_path / "primary-proj").mkdir()
    (tmp_path / "secondary-proj").mkdir()
    registry = BrainRegistry(
        projects=[
            Project(slug="primary-proj", path=tmp_path / "primary-proj", primary=True),
            Project(slug="secondary-proj", path=tmp_path / "secondary-proj"),
        ],
        build_brain=lambda _: brain,
    )
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hello"})
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hello"
    # Invocation hit the shared brain exactly once.
    assert len(brain.calls) == 1


def test_brain_query_without_slug_no_primary(tmp_path: Path) -> None:
    """No slug AND no primary is a 400 — the server can't guess."""
    (tmp_path / "repo").mkdir()
    registry = BrainRegistry(
        projects=[Project(slug="repo", path=tmp_path / "repo")],  # primary=False
        build_brain=lambda _: MockBrain(),
    )
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hi"})
    assert r.status_code == 400
    assert "primary" in r.json()["detail"].lower()


def test_brain_query_unknown_slug_returns_404(tmp_path: Path) -> None:
    """An explicit but unknown slug is a 404 with a useful error message."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/brain/query",
        json={"user_utterance": "hi", "project_slug": "nonexistent"},
    )
    assert r.status_code == 404
    assert "nonexistent" in r.json()["detail"]


def test_brain_query_with_explicit_slug_routes_to_that_project(tmp_path: Path) -> None:
    """An explicit slug routes to that project's brain (not the primary)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    brains: dict[str, MockBrain] = {"a": MockBrain(), "b": MockBrain()}

    def build(p: Project) -> BrainInterface:
        return brains[p.slug]

    registry = BrainRegistry(
        projects=[
            Project(slug="a", path=tmp_path / "a", primary=True),
            Project(slug="b", path=tmp_path / "b"),
        ],
        build_brain=build,
    )
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/brain/query",
        json={"user_utterance": "hi", "project_slug": "b"},
    )
    assert r.status_code == 200
    assert len(brains["a"].calls) == 0
    assert len(brains["b"].calls) == 1
