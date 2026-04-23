"""Tests for the FastAPI server's POST /specialists/* endpoints."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# /specialists/pr-reviewer
# ---------------------------------------------------------------------------


def _ok(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _pr_registry(
    repo: Path,
    *,
    github_repo: str | None = "Obsidian-Owl/reachy-ducky",
    brain: BrainInterface | None = None,
) -> BrainRegistry:
    """One-project registry with a configurable ``github_repo`` for pr-reviewer tests."""
    b = brain if brain is not None else MockBrain()
    return BrainRegistry(
        projects=[
            Project(
                slug="repo",
                path=repo,
                github_repo=github_repo,
                primary=True,
            )
        ],
        build_brain=lambda _: b,
    )


def _mock_gh_min(*, pr_json: str) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Minimum subprocess mocks for a pr-reviewer route happy-path request.

    Any ``gh pr view`` returns ``pr_json``; everything else returns empty
    list / envelope. Good enough to exercise routing and response shape
    without over-specifying the fetch graph (covered in detail by the
    specialist's own tests).
    """

    def _side_effect(
        argv: list[str],
        *args: Any,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        if argv[:3] == ["gh", "pr", "view"]:
            return _ok(pr_json)
        if argv[:3] == ["gh", "pr", "diff"]:
            return _ok("")
        if argv[:2] == ["gh", "api"] and "check-runs" in argv[2]:
            return _ok('{"total_count": 0, "check_runs": []}')
        if argv[:2] == ["gh", "api"]:  # comments
            return _ok("[]")
        if argv[:3] == ["gh", "pr", "list"]:
            return _ok('[{"number": 42}]')
        if argv[:2] == ["git", "rev-parse"]:
            return _ok("feat-retry\n")
        raise AssertionError(f"unexpected argv: {argv}")

    return _side_effect


_HAPPY_PR_JSON = (
    '{"number": 42, "title": "feat: retry", "body": "Closes #15",'
    ' "state": "OPEN", "mergeable": "MERGEABLE",'
    ' "headRefName": "feat-retry", "baseRefName": "main",'
    ' "url": "https://github.com/Obsidian-Owl/reachy-ducky/pull/42",'
    ' "headRefOid": "abc123"}'
)


def test_pr_reviewer_endpoint_explicit_pr_number(tmp_path: Path) -> None:
    """Explicit pr_number → 200 with pr-reviewer response."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_pr_registry(repo), memory_root=tmp_path / "mem")
    client = TestClient(app)

    with patch("subprocess.run", side_effect=_mock_gh_min(pr_json=_HAPPY_PR_JSON)):
        r = client.post(
            "/specialists/pr-reviewer",
            json={"name": "pr-reviewer", "project_slug": "repo", "pr_number": 42},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "pr-reviewer"
    assert "mock" in body["summary"].lower()


def test_pr_reviewer_endpoint_auto_detects_when_no_pr_number(tmp_path: Path) -> None:
    """No pr_number → route still resolves via git rev-parse + gh pr list."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_pr_registry(repo), memory_root=tmp_path / "mem")
    client = TestClient(app)

    with patch("subprocess.run", side_effect=_mock_gh_min(pr_json=_HAPPY_PR_JSON)):
        r = client.post(
            "/specialists/pr-reviewer",
            json={"name": "pr-reviewer", "project_slug": "repo"},
        )

    assert r.status_code == 200
    assert r.json()["name"] == "pr-reviewer"


def test_pr_reviewer_endpoint_unknown_slug_returns_404(tmp_path: Path) -> None:
    """Unknown slug → 404 with a useful detail (mirrors plan-reviewer behaviour)."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_pr_registry(repo), memory_root=tmp_path / "mem")
    client = TestClient(app)

    r = client.post(
        "/specialists/pr-reviewer",
        json={"name": "pr-reviewer", "project_slug": "nonexistent"},
    )

    assert r.status_code == 404
    assert "nonexistent" in r.json()["detail"]


def test_pr_reviewer_endpoint_missing_github_repo_returns_400(tmp_path: Path) -> None:
    """Project without github_repo → 400 — pr-reviewer can't target GitHub without it."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(
        registry=_pr_registry(repo, github_repo=None),
        memory_root=tmp_path / "mem",
    )
    client = TestClient(app)

    r = client.post(
        "/specialists/pr-reviewer",
        json={"name": "pr-reviewer", "project_slug": "repo", "pr_number": 42},
    )

    assert r.status_code == 400
    assert "github_repo" in r.json()["detail"]


def test_pr_reviewer_endpoint_response_shape(tmp_path: Path) -> None:
    """Response matches ``SpecialistResponse`` schema exactly — {name, summary, flags}."""
    repo = _init_repo(tmp_path / "repo")
    app = create_app(registry=_pr_registry(repo), memory_root=tmp_path / "mem")
    client = TestClient(app)

    with patch("subprocess.run", side_effect=_mock_gh_min(pr_json=_HAPPY_PR_JSON)):
        r = client.post(
            "/specialists/pr-reviewer",
            json={"name": "pr-reviewer", "project_slug": "repo", "pr_number": 42},
        )

    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"name", "summary", "flags"}
    assert isinstance(data["flags"], list)


def test_pr_reviewer_endpoint_invokes_brain_once(tmp_path: Path) -> None:
    """Each POST triggers exactly one brain.query() call (Pattern A contract)."""
    repo = _init_repo(tmp_path / "repo")
    brain = MockBrain()
    app = create_app(registry=_pr_registry(repo, brain=brain), memory_root=tmp_path / "mem")
    client = TestClient(app)

    with patch("subprocess.run", side_effect=_mock_gh_min(pr_json=_HAPPY_PR_JSON)):
        client.post(
            "/specialists/pr-reviewer",
            json={"name": "pr-reviewer", "project_slug": "repo", "pr_number": 42},
        )

    assert len(brain.calls) == 1
    assert "feat: retry" in brain.calls[0].user_utterance
