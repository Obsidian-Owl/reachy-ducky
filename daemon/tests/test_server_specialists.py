"""Tests for the FastAPI server's POST /specialists/plan-reviewer endpoint."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from reachy_ducky_daemon.brain.interface import BrainInterface
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.brain.registry import BrainRegistry
from reachy_ducky_daemon.project import Project
from reachy_ducky_daemon.server import create_app


def _init_repo(root: Path) -> Path:
    """Create a minimal git repo with a plan file so PlanReviewer has content."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=root, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=root, check=True, capture_output=True
    )
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "p.md").write_text("# Plan\nAdd feature X\n")
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def _registry_for(repo: Path, brain: BrainInterface | None = None) -> BrainRegistry:
    """Build a one-project registry wired to ``repo`` and ``brain`` (or a fresh MockBrain)."""
    b = brain if brain is not None else MockBrain()
    return BrainRegistry(
        projects=[Project(slug="repo", path=repo, primary=True)],
        build_brain=lambda _: b,
    )


def test_plan_reviewer_endpoint(tmp_path: Path) -> None:
    """POST /specialists/plan-reviewer dispatches the specialist and returns its response."""
    mem = tmp_path / "mem"
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_registry_for(repo), memory_root=mem)
    client = TestClient(app)
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "repo"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "plan-reviewer"
    assert "mock" in body["summary"].lower()


def test_plan_reviewer_endpoint_unknown_slug(tmp_path: Path) -> None:
    """Unknown project_slug returns 404 with a useful error message."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_registry_for(repo), memory_root=tmp_path / "mem")
    client = TestClient(app)
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "nonexistent"},
    )
    assert r.status_code == 404
    assert "nonexistent" in r.json()["detail"]


def test_plan_reviewer_endpoint_with_empty_registry(tmp_path: Path) -> None:
    """With a zero-project registry, any slug returns 404."""
    registry = BrainRegistry(projects=[], build_brain=lambda _: MockBrain())
    app = create_app(registry=registry, memory_root=tmp_path)
    client = TestClient(app)
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "anything"},
    )
    assert r.status_code == 404


def test_plan_reviewer_endpoint_with_matching_token(tmp_path: Path) -> None:
    """Bearer middleware protects the specialists route; matching token reaches it."""
    mem = tmp_path / "mem"
    repo = _init_repo(tmp_path / "repo")
    app = create_app(
        registry=_registry_for(repo),
        memory_root=mem,
        auth_token="secret",
    )
    client = TestClient(app)
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "repo"},
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 200


def test_plan_reviewer_endpoint_rejects_missing_fields(tmp_path: Path) -> None:
    """Missing required `name` or `project_slug` returns 422."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_registry_for(repo), memory_root=tmp_path / "mem")
    client = TestClient(app)
    r = client.post("/specialists/plan-reviewer", json={})
    assert r.status_code == 422


def test_plan_reviewer_endpoint_response_shape(tmp_path: Path) -> None:
    """Response matches SpecialistResponse schema exactly (name, summary, flags)."""
    mem = tmp_path / "mem"
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_registry_for(repo), memory_root=mem)
    client = TestClient(app)
    r = client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "repo"},
    )
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"name", "summary", "flags"}
    assert isinstance(data["flags"], list)


def test_plan_reviewer_endpoint_invokes_brain_once(tmp_path: Path) -> None:
    """Each POST triggers exactly one brain.query() call (via PlanReviewer.review())."""
    mem = tmp_path / "mem"
    repo = _init_repo(tmp_path / "repo")
    brain = MockBrain()
    app = create_app(registry=_registry_for(repo, brain=brain), memory_root=mem)
    client = TestClient(app)
    client.post(
        "/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "project_slug": "repo"},
    )
    assert len(brain.calls) == 1
    assert "# Plan" in brain.calls[0].user_utterance
    assert "Report drift" in brain.calls[0].user_utterance
