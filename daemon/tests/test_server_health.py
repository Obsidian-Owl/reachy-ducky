"""Tests for the FastAPI server scaffold: /health endpoint + BearerAuthMiddleware."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.server import create_app


def test_health_ok(tmp_path: Path) -> None:
    """/health returns 200 with ok/brain/memory_ready reflecting the injected brain."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["brain"] == "MockBrain"
    assert data["memory_ready"] is True


def test_health_open_even_with_auth_token(tmp_path: Path) -> None:
    """/health is intentionally open so Tailscale/LAN health checks don't need the token."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200


def test_protected_routes_require_bearer(tmp_path: Path) -> None:
    """When auth_token is set, non-/health routes require a matching Authorization header."""
    app = create_app(brain=MockBrain(), memory_root=tmp_path, auth_token="secret")
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

    # Right token -> reaches the route (added in Task 6.2; this test may need to be
    # re-run after Task 6.2 ships the endpoint). Left as a comment because
    # /brain/query does not exist yet: with middleware-first ordering, the 401
    # branches above return before any route handler runs, so we can verify auth
    # denial on an unknown path without the route existing. The positive-path
    # assertion belongs with the Task 6.2 test suite.


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
    app = create_app(brain=MockBrain(), memory_root=tmp_path, auth_token="secret")
    client = TestClient(app)
    headers = {"Authorization": header_value} if header_value is not None else {}
    r = client.post("/brain/query", json={"user_utterance": "hi"}, headers=headers)
    assert r.status_code == 401
