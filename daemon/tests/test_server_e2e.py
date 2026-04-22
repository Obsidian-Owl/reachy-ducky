"""End-to-end smoke tests exercising the full HTTP chain.

These use FastAPI's TestClient + MockBrain to prove:
1. The auth middleware, registry lookup, brain routing, and specialist
   dispatch all compose correctly when called in sequence.
2. BrainRegistry's lazy-build invariant holds across the HTTP layer:
   /health reports "unbuilt" pre-query and the concrete brain class
   post-query.
3. Bearer-token enforcement works against every protected route.
4. Invocation counts match expected side-effects end-to-end.

Live-Claude integration is NOT exercised here — that's covered by the
two @pytest.mark.integration tests in test_brain_claude_integration.py
and test_specialist_plan_reviewer.py. This file owns the HTTP chain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.brain.registry import BrainRegistry
from reachy_ducky_daemon.project import Project
from reachy_ducky_daemon.server import create_app


def _init_repo(root: Path) -> Path:
    """Minimal git repo with one plan + one uncommitted change."""
    root.mkdir(parents=True, exist_ok=True)
    run = lambda args: subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: E731
    run(["init", "-b", "main"])
    run(["config", "user.email", "t@t"])
    run(["config", "user.name", "t"])
    run(["config", "commit.gpgsign", "false"])
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "p.md").write_text("# Plan Foo\nAdd feature X\n")
    (root / "a.py").write_text("x = 1\n")
    run(["add", "."])
    run(["commit", "-m", "init"])
    # Modify a TRACKED file so `git diff` (working-tree vs HEAD) surfaces
    # the change. Adding an untracked file wouldn't — git diff omits
    # untracked additions without --no-index/--untracked.
    (root / "a.py").write_text("x = 1\ny = 2\n")
    return root


def test_e2e_http_chain_with_shared_mock_brain(tmp_path: Path) -> None:
    """Every protected route routes through the same cached brain instance.

    Hits /health → /brain/query → /health → /specialists/plan-reviewer → /health
    and asserts each layer composed correctly: auth middleware, registry
    lookup, BrainRegistry lazy-build, endpoint response shapes, and
    MockBrain's invocation count growing by exactly one per request.
    """
    mem = tmp_path / "mem"
    repo = _init_repo(tmp_path / "repo")
    shared_brain = MockBrain()
    registry = BrainRegistry(
        projects=[Project(slug="repo", path=repo, primary=True)],
        build_brain=lambda _p: shared_brain,
    )
    app = create_app(registry=registry, memory_root=mem, auth_token="sekret")
    client = TestClient(app)

    auth = {"Authorization": "Bearer sekret"}

    # 1. /health is open (no token needed). Pre-query, brain is unbuilt.
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["brain"] == "unbuilt"
    assert data["memory_ready"] is True  # ensure_layout ran during create_app
    assert data["projects"] == ["repo"]

    # 2. /brain/query uses the primary project when no slug is provided.
    r = client.post("/brain/query", json={"user_utterance": "hello"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hello"
    assert len(shared_brain.calls) == 1

    # 3. /health after first query — brain is now built; class name surfaces.
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["brain"] == "MockBrain"

    # 4. /specialists/plan-reviewer invokes PlanReviewer which calls brain.query
    #    once more with an assembled prompt containing the plan + diff.
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "repo"},
        headers=auth,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "plan-reviewer"
    assert "mock" in body["summary"].lower()
    # Side-effect verification: PlanReviewer called the brain exactly once.
    assert len(shared_brain.calls) == 2
    assembled_prompt = shared_brain.calls[-1].user_utterance
    assert "# Plan Foo" in assembled_prompt
    assert "Report drift" in assembled_prompt
    assert "y = 2" in assembled_prompt  # the uncommitted change to a.py shows in the diff


def test_e2e_auth_blocks_every_protected_route_and_health_stays_open(tmp_path: Path) -> None:
    """Bearer middleware applies uniformly to all non-open paths."""
    repo = _init_repo(tmp_path / "repo")
    registry = BrainRegistry(
        projects=[Project(slug="repo", path=repo, primary=True)],
        build_brain=lambda _p: MockBrain(),
    )
    app = create_app(registry=registry, memory_root=tmp_path / "mem", auth_token="sekret")
    client = TestClient(app)

    # /health open
    assert client.get("/health").status_code == 200

    # protected routes all 401 without Bearer
    assert client.post("/brain/query", json={"user_utterance": "x"}).status_code == 401
    assert (
        client.post(
            "/specialists/plan-reviewer",
            json={"name": "plan-reviewer", "project_slug": "repo"},
        ).status_code
        == 401
    )


def test_e2e_unknown_project_slug_returns_404(tmp_path: Path) -> None:
    """An unknown slug on either protected route is a 404 at the HTTP boundary.

    Both endpoints share the same registry; the 404 surface should be
    symmetric whether the caller asked via /brain/query or /specialists/*.
    """
    repo = _init_repo(tmp_path / "repo")
    registry = BrainRegistry(
        projects=[Project(slug="repo", path=repo, primary=True)],
        build_brain=lambda _p: MockBrain(),
    )
    app = create_app(registry=registry, memory_root=tmp_path / "mem")
    client = TestClient(app)

    r = client.post("/brain/query", json={"user_utterance": "x", "project_slug": "nope"})
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]

    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "nope"},
    )
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


def test_e2e_multi_project_routes_to_correct_brain_and_repo(tmp_path: Path) -> None:
    """Multi-project routing: each project_slug resolves to its own brain + repo."""
    alpha_repo = _init_repo(tmp_path / "alpha")
    beta_repo = _init_repo(tmp_path / "beta")
    # Make beta's plan different so we can prove the right repo was read.
    (beta_repo / "docs" / "plans" / "p.md").write_text("# Plan Beta\nBeta's feature\n")
    run_beta = lambda args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=beta_repo, check=True, capture_output=True
    )
    run_beta(["add", "."])
    run_beta(["commit", "--amend", "-m", "init-beta"])

    alpha_brain = MockBrain()
    beta_brain = MockBrain()
    brains_by_slug = {"alpha": alpha_brain, "beta": beta_brain}

    registry = BrainRegistry(
        projects=[
            Project(slug="alpha", path=alpha_repo, primary=True),
            Project(slug="beta", path=beta_repo),
        ],
        build_brain=lambda p: brains_by_slug[p.slug],
    )
    app = create_app(registry=registry, memory_root=tmp_path / "mem")
    client = TestClient(app)

    # Query routed to beta must only touch beta's brain.
    r = client.post(
        "/brain/query",
        json={"user_utterance": "q", "project_slug": "beta"},
    )
    assert r.status_code == 200
    assert len(alpha_brain.calls) == 0
    assert len(beta_brain.calls) == 1

    # PlanReviewer routed to beta must read beta's plan, not alpha's.
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "beta"},
    )
    assert r.status_code == 200
    prompt = beta_brain.calls[-1].user_utterance
    assert "# Plan Beta" in prompt
    assert "# Plan Foo" not in prompt  # alpha's plan must NOT leak

    # /health reports both slugs (sorted).
    r = client.get("/health")
    assert r.json()["projects"] == ["alpha", "beta"]
