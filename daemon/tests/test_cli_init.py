"""Tests for the ``reachy-ducky init`` CLI wizard.

Each test scripts stdin via :class:`typer.testing.CliRunner` so the
prompts are driven end-to-end without spawning a subprocess. The wizard
is redirected away from the real ``~`` by pointing ``$HOME`` (and the
fallback ``HOMEDRIVE``/``USERPROFILE`` honoured by ``Path.home()``) at
``tmp_path``.

A lightweight ``fake_claude_auth`` fixture replaces ``subprocess.run``
with a scripted stand-in so we can exercise both the logged-in and
not-logged-in paths without touching the real CLI.
"""

from __future__ import annotations

import re
import stat
import subprocess
import sys
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

import pytest
from reachy_ducky_daemon.cli import app
from typer.testing import CliRunner


class _ConfigureClaudeAuth(Protocol):
    """Type of the ``fake_claude_auth`` fixture — a kw-only configurator."""

    def __call__(self, *, logged_in: bool = ..., missing: bool = ...) -> None: ...


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` and ``~`` expansion to ``tmp_path``.

    Setting ``HOME`` is sufficient on POSIX; on the off-chance the tests
    ever run on Windows CI, ``USERPROFILE`` covers the fallback.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("REACHY_DUCKY_CONFIG", raising=False)
    return tmp_path


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a directory that looks like a git checkout (has ``.git/``)."""
    repo = tmp_path / "work" / "sample-repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def second_git_repo(tmp_path: Path) -> Path:
    """A second git-repo-shaped directory for multi-project tests."""
    repo = tmp_path / "work" / "other-repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def fake_claude_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_ConfigureClaudeAuth]:
    """Patch ``subprocess.run`` to simulate ``claude auth status``.

    Yields a configurator so individual tests can opt into "logged in",
    "not logged in", or "binary missing from PATH". The default is
    logged-in so tests that don't care don't need to configure it.
    """
    state = {"logged_in": True, "missing": False}

    real_run = subprocess.run

    def fake_run(
        cmd: Sequence[str],
        *args: Any,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        if (
            isinstance(cmd, list | tuple)
            and len(cmd) >= 3
            and list(cmd[:3])
            == [
                "claude",
                "auth",
                "status",
            ]
        ):
            if state["missing"]:
                raise FileNotFoundError("claude not on PATH")
            if state["logged_in"]:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout='{"loggedIn": true, "email": "user@example.com"}',
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout='{"loggedIn": false}', stderr=""
            )
        return real_run(cmd, *args, **kwargs)  # pragma: no cover — passthrough

    monkeypatch.setattr(subprocess, "run", fake_run)

    def configure(*, logged_in: bool = True, missing: bool = False) -> None:
        state["logged_in"] = logged_in
        state["missing"] = missing

    yield configure


def _invoke(inp: str) -> Any:
    """Run the ``init`` command with scripted input, fail-loudly on crash."""
    runner = CliRunner()
    result = runner.invoke(app, ["init"], input=inp, catch_exceptions=False)
    return result


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_creates_config_env_and_memory(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """Scripted answers produce a valid config.toml, .env, and memory tree."""
    fake_claude_auth(logged_in=True)
    # Order matches the wizard prompt sequence:
    # 1. memory_root, 2. host, 3. port, 4. auth-token CHOICE (s=skip),
    # 5. GH PAT, 6. OpenAI API key, 7. project slug, 8. path, 9. github_repo,
    # 10. primary, 11. add another.
    script = "\n".join(
        [
            "",  # memory_root default
            "",  # host default
            "",  # port default
            "s",  # auth-token: skip
            "",  # GH PAT empty
            "",  # OpenAI key empty
            "reachy-ducky",  # slug
            str(git_repo),  # path
            "",  # github_repo empty
            "y",  # primary
            "n",  # add another
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output

    cfg_path = home / ".reachy-ducky" / "config.toml"
    assert cfg_path.exists()
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["daemon"]["host"] == "127.0.0.1"
    assert data["daemon"]["port"] == 8765
    assert "auth_token" not in data["daemon"]
    assert data["projects"][0]["slug"] == "reachy-ducky"
    assert data["projects"][0]["path"] == str(git_repo.resolve())
    assert data["projects"][0]["primary"] is True
    assert "github_repo" not in data["projects"][0]

    # Memory tree seeded.
    assert (home / ".reachy-ducky" / "memory" / "ducky" / "soul.md").exists()
    assert (home / ".reachy-ducky" / "memory" / "projects").is_dir()

    # No .env: no PAT, no auth token, no OpenAI key.
    assert not (home / ".reachy-ducky" / ".env").exists()


def test_happy_path_round_trips_through_app_config(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """AppConfig.load() reads the wizard's output without errors."""
    from reachy_ducky_daemon.config import AppConfig

    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "my-proj",
            str(git_repo),
            "owner/my-proj",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output

    cfg = AppConfig.load(path=home / ".reachy-ducky" / "config.toml")
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert len(cfg.projects) == 1
    assert cfg.projects[0].slug == "my-proj"
    assert cfg.projects[0].github_repo == "owner/my-proj"
    assert cfg.projects[0].primary is True


# ---------------------------------------------------------------------------
# Existing config — overwrite prompt
# ---------------------------------------------------------------------------


def test_existing_config_overwrite_no_exits_without_changes(
    home: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """Default answer to overwrite prompt is 'no' — file unchanged, clear message."""
    fake_claude_auth(logged_in=True)
    cfg_path = home / ".reachy-ducky" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    original = "# pre-existing\n"
    cfg_path.write_text(original)

    # Just hit Enter on the overwrite prompt — default is no.
    result = _invoke("\n")
    assert result.exit_code == 0, result.output
    assert "keeping existing" in result.output.lower() or "aborted" in result.output.lower()
    assert cfg_path.read_text() == original


def test_existing_config_overwrite_yes_replaces_file(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """User confirms overwrite — wizard proceeds and writes a fresh file."""
    fake_claude_auth(logged_in=True)
    cfg_path = home / ".reachy-ducky" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("# ignore me\n")

    script = "\n".join(
        [
            "y",  # overwrite yes
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "fresh-proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["projects"][0]["slug"] == "fresh-proj"


# ---------------------------------------------------------------------------
# Validation re-prompts
# ---------------------------------------------------------------------------


def test_invalid_slug_reprompts_until_valid(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """Uppercase / underscore / empty slugs are rejected, then the valid one wins."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "Bad_Slug",  # rejected: underscore + uppercase
            "no spaces ok",  # rejected: space
            "good-slug",  # accepted
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    cfg_path = home / ".reachy-ducky" / "config.toml"
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["projects"][0]["slug"] == "good-slug"
    # At least one "invalid slug" complaint in the output.
    assert "slug" in result.output.lower()


def test_init_wizard_rejects_duplicate_slug(
    home: Path,
    git_repo: Path,
    second_git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """P2 from Codex re-review: duplicate slugs are caught at wizard time.

    ``BrainRegistry.__init__`` raises ``ValueError`` when two configured
    projects share a slug, so a wizard run that writes ``[alpha, alpha]``
    produces a TOML that the daemon refuses to load. The wizard must
    re-prompt instead, keeping ``reachy-ducky init`` success from implying a
    non-bootable config.

    Scripted flow:
      1. first project  → slug=alpha, path=git_repo, no GH repo, primary=y, add=y
      2. second project → slug=alpha (rejected as duplicate) →
                          slug=beta, path=second_git_repo, no GH repo,
                          primary=n, add=n
    """
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory default
            "",  # host default
            "",  # port default
            "s",  # auth-token: skip
            "",  # no PAT
            "",  # no OpenAI key
            "alpha",  # project #1 slug
            str(git_repo),  # project #1 path
            "",  # no github_repo
            "y",  # primary
            "y",  # add another
            "alpha",  # duplicate slug — rejected
            "beta",  # accepted after re-prompt
            str(second_git_repo),  # project #2 path
            "",  # no github_repo
            "n",  # not primary
            "n",  # done
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output

    # User-visible complaint surfaces the conflict.
    assert "already used" in result.output.lower()

    cfg_path = home / ".reachy-ducky" / "config.toml"
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    # Two distinct slugs, in order; no duplicate alpha.
    assert [p["slug"] for p in data["projects"]] == ["alpha", "beta"]


def test_invalid_path_nonexistent_reprompts(
    home: Path,
    git_repo: Path,
    tmp_path: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """A path that does not exist is rejected with a clear message."""
    fake_claude_auth(logged_in=True)
    nonexistent = tmp_path / "does" / "not" / "exist"
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "good-slug",
            str(nonexistent),  # rejected
            str(git_repo),  # accepted
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert "does not exist" in result.output.lower()


def test_invalid_path_not_a_git_repo_reprompts(
    home: Path,
    git_repo: Path,
    tmp_path: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """A path that exists but is not a git repo is rejected with a clear message."""
    fake_claude_auth(logged_in=True)
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "good-slug",
            str(not_a_repo),  # rejected
            str(git_repo),  # accepted
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert "not a git repository" in result.output.lower()


def test_invalid_github_repo_reprompts_and_accepts_empty(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """A malformed github_repo is rejected; empty is accepted as 'none'."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "proj",
            str(git_repo),
            "not-a-repo",  # rejected: no slash
            "",  # accepted: empty == no github_repo
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert "owner/repo" in result.output.lower() or "owner/name" in result.output.lower()
    cfg_path = home / ".reachy-ducky" / "config.toml"
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    assert "github_repo" not in data["projects"][0]


# ---------------------------------------------------------------------------
# Multiple projects + primary logic
# ---------------------------------------------------------------------------


def test_multiple_projects_only_first_primary(
    home: Path,
    git_repo: Path,
    second_git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """Two projects; only the one the user marked primary has primary=true."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "first-proj",
            str(git_repo),
            "",
            "y",  # primary
            "y",  # add another
            "second-proj",
            str(second_git_repo),
            "",
            "n",  # not primary
            "n",  # done
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    cfg_path = home / ".reachy-ducky" / "config.toml"
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    assert len(data["projects"]) == 2
    assert data["projects"][0]["slug"] == "first-proj"
    assert data["projects"][0]["primary"] is True
    assert data["projects"][1]["slug"] == "second-proj"
    assert data["projects"][1].get("primary") is not True


def test_primary_swap_demotes_previous(
    home: Path,
    git_repo: Path,
    second_git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """Second project marked primary — first is silently demoted."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "first-proj",
            str(git_repo),
            "",
            "y",  # first = primary
            "y",  # add another
            "second-proj",
            str(second_git_repo),
            "",
            "y",  # second ALSO primary → demote first
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    cfg_path = home / ".reachy-ducky" / "config.toml"
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["projects"][0]["slug"] == "first-proj"
    assert data["projects"][0].get("primary") is not True
    assert data["projects"][1]["slug"] == "second-proj"
    assert data["projects"][1]["primary"] is True


# ---------------------------------------------------------------------------
# claude auth status
# ---------------------------------------------------------------------------


def test_claude_not_logged_in_warns_and_continues(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """If ``claude auth status`` reports not-logged-in, warn but continue."""
    fake_claude_auth(logged_in=False)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert "claude login" in result.output.lower()
    # File was still written.
    assert (home / ".reachy-ducky" / "config.toml").exists()


def test_claude_binary_missing_warns_and_continues(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """If ``claude`` isn't on PATH, the wizard prints a warning and continues."""
    fake_claude_auth(missing=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # GH PAT
            "",  # OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert (home / ".reachy-ducky" / "config.toml").exists()


# ---------------------------------------------------------------------------
# PAT / .env handling
# ---------------------------------------------------------------------------


def test_pat_writes_env_with_0600(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """When the user supplies a PAT, .env is 0600 and contains the token."""
    fake_claude_auth(logged_in=True)
    pat = "ghp_" + "x" * 36
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            pat,  # GH PAT
            "",  # OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    env = home / ".reachy-ducky" / ".env"
    assert env.exists()
    content = env.read_text()
    assert f"GITHUB_PERSONAL_ACCESS_TOKEN={pat}" in content
    # TOML never contains the PAT.
    cfg_text = (home / ".reachy-ducky" / "config.toml").read_text()
    assert pat not in cfg_text
    # 0600 perms — POSIX-only contract. Windows uses NTFS ACLs rather
    # than POSIX modes, so ``os.chmod(path, 0o600)`` is largely a no-op
    # there and ``stat().st_mode`` returns the NTFS default (0o666). The
    # file-existence + content checks above still run on every platform.
    if sys.platform != "win32":
        mode = stat.S_IMODE(env.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_auth_token_writes_env_with_0600(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """A pasted auth_token is written to .env (not TOML) with 0600 perms."""
    fake_claude_auth(logged_in=True)
    token = "supersecrettoken123"
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "p",  # auth-token: paste
            token,  # auth-token value
            "",  # no PAT
            "",  # no OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    env = home / ".reachy-ducky" / ".env"
    assert env.exists()
    content = env.read_text()
    assert f"REACHY_DUCKY_AUTH_TOKEN={token}" in content
    # The token is NOT written to config.toml.
    cfg_text = (home / ".reachy-ducky" / "config.toml").read_text()
    assert token not in cfg_text
    # POSIX-only: NTFS enforces secrecy via ACLs, not POSIX modes. See
    # the matching guard in ``test_pat_writes_env_with_0600``.
    if sys.platform != "win32":
        mode = stat.S_IMODE(env.stat().st_mode)
        assert mode == 0o600


def test_auth_token_default_generate_writes_random_token(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """Hitting Enter on the auth-token choice generates a 256-bit random token.

    Generate is the recommended primary path. The wizard previously
    buried this behind a confusing post-prompt offer; the new flow
    defaults to G so the secure choice is one Enter away.
    """
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "",  # auth-token CHOICE — empty == default == G (generate)
            "",  # no PAT
            "",  # no OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    env = home / ".reachy-ducky" / ".env"
    assert env.exists()
    content = env.read_text()
    # 64-hex-char token (32 bytes * 2).
    match = re.search(r"^REACHY_DUCKY_AUTH_TOKEN=([0-9a-f]{64})$", content, re.MULTILINE)
    assert match, f"expected 64-hex-char token in .env, got: {content!r}"


def test_auth_token_invalid_choice_reprompts_before_skip(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """A typo at the auth-token menu must not silently choose Skip."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "x",  # invalid auth-token choice
            "s",  # explicit skip after re-prompt
            "",  # no PAT
            "",  # no OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert "Unrecognized choice" in result.output
    assert not (home / ".reachy-ducky" / ".env").exists()


def test_auth_token_empty_paste_reprompts_before_non_loopback_skip_warning(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """An empty pasted token must not silently skip auth on a LAN bind."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",  # memory_root
            "0.0.0.0",  # host: non-loopback bind
            "",  # port
            "p",  # auth-token: paste
            "",  # empty pasted token
            "s",  # explicit skip after re-prompt
            "",  # no PAT
            "",  # no OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert "Empty token" in result.output
    assert "WARNING: skipping the auth token while bound to 0.0.0.0" in result.output
    assert not (home / ".reachy-ducky" / ".env").exists()


def test_openai_key_writes_env_with_0600(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """OPENAI_API_KEY supplied to the wizard lands in .env with the other secrets."""
    fake_claude_auth(logged_in=True)
    key = "sk-test-" + "y" * 40
    script = "\n".join(
        [
            "",  # memory_root
            "",  # host
            "",  # port
            "s",  # auth-token: skip
            "",  # no PAT
            key,  # OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    env = home / ".reachy-ducky" / ".env"
    assert env.exists()
    content = env.read_text()
    assert f"OPENAI_API_KEY={key}" in content
    # TOML never contains the key.
    cfg_text = (home / ".reachy-ducky" / "config.toml").read_text()
    assert key not in cfg_text
    if sys.platform != "win32":
        mode = stat.S_IMODE(env.stat().st_mode)
        assert mode == 0o600


def test_no_pat_no_auth_token_no_env_file(
    home: Path,
    git_repo: Path,
    fake_claude_auth: _ConfigureClaudeAuth,
) -> None:
    """With no secrets collected, .env is not created."""
    fake_claude_auth(logged_in=True)
    script = "\n".join(
        [
            "",
            "",
            "",
            "s",  # auth-token: skip
            "",  # no PAT
            "",  # no OpenAI key
            "proj",
            str(git_repo),
            "",
            "y",
            "n",
            "",
        ]
    )
    result = _invoke(script)
    assert result.exit_code == 0, result.output
    assert not (home / ".reachy-ducky" / ".env").exists()


# ---------------------------------------------------------------------------
# --help smoke
# ---------------------------------------------------------------------------


def test_help_lists_init_command() -> None:
    """``reachy-ducky init --help`` reaches the command and exits cleanly."""
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "init" in result.output.lower()
