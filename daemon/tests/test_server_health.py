"""Tests for the FastAPI server scaffold: /health endpoint + BearerAuthMiddleware."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reachy_ducky_daemon.brain.registry import BrainRegistry
from reachy_ducky_daemon.server import create_app


def test_health_ok(tmp_path: Path) -> None:
    """/health returns 200 with ok/brain/memory_ready/projects reflecting the registry."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    # No brain has been built yet — ``/health`` does NOT trigger a lazy build.
    assert data["brain"] == "unbuilt"
    assert data["memory_ready"] is True
    assert data["projects"] == ["repo"]


def test_health_reports_projects(tmp_path: Path) -> None:
    """``/health`` reports every registered slug sorted alphabetically."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    from reachy_ducky_daemon.brain.mock import MockBrain
    from reachy_ducky_daemon.project import Project

    registry = BrainRegistry(
        projects=[
            Project(slug="zeta", path=tmp_path / "a", primary=True),
            Project(slug="alpha", path=tmp_path / "b"),
        ],
        build_brain=lambda _: MockBrain(),
    )
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["projects"] == ["alpha", "zeta"]


def test_health_reports_none_when_no_primary(tmp_path: Path) -> None:
    """With no primary project, ``/health.brain`` reports ``"none"`` — not a crash."""
    from reachy_ducky_daemon.brain.mock import MockBrain
    from reachy_ducky_daemon.project import Project

    registry = BrainRegistry(
        projects=[Project(slug="demo", path=tmp_path / "demo")],
        build_brain=lambda _: MockBrain(),
    )
    (tmp_path / "demo").mkdir()
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["brain"] == "none"


def test_health_reports_built_brain_class_after_query(tmp_path: Path) -> None:
    """Once a brain has been built, ``/health.brain`` reports its concrete class."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    # Force a brain build by hitting /brain/query first.
    client.post("/brain/query", json={"user_utterance": "hi"})
    r = client.get("/health")
    assert r.json()["brain"] == "MockBrain"


def test_health_open_even_with_auth_token(tmp_path: Path) -> None:
    """/health is intentionally open so Tailscale/LAN health checks don't need the token."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200


def test_protected_routes_require_bearer(tmp_path: Path) -> None:
    """When auth_token is set, non-/health routes require a matching Authorization header."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)

    # No header -> 401
    r = client.post("/brain/query", json={"user_utterance": "hi"})
    assert r.status_code == 401

    # Wrong token -> 401
    r = client.post(
        "/brain/query",
        json={"user_utterance": "hi"},
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


@pytest.mark.parametrize(
    "header_value",
    [
        None,  # no header at all (already covered by test 3 first branch; keep for clarity)
        "",  # empty header
        "Basic abcdef",  # wrong scheme
        "Bearer",  # scheme only, no token
        "Bearer ",  # scheme + space, no token
        "Bearer wrong",  # scheme + wrong token
        "bearer secret",  # lowercase scheme (RFC says insensitive; current impl rejects; lock in)
        "BEARER secret",  # uppercase scheme; ditto
    ],
)
def test_protected_routes_reject_malformed_auth(tmp_path: Path, header_value: str | None) -> None:
    """Auth middleware locks out anything that isn't exactly 'Bearer <token>'."""
    registry = BrainRegistry.single_mock("repo", tmp_path / "repo")
    app = create_app(registry=registry, memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)
    headers = {"Authorization": header_value} if header_value is not None else {}
    r = client.post("/brain/query", json={"user_utterance": "hi"}, headers=headers)
    assert r.status_code == 401
