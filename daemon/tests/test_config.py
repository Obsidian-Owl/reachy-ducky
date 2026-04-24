"""Tests for :class:`AppConfig` — TOML loading, env overrides, and the
``warn_if_exposed_without_auth`` loopback safety check."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from reachy_ducky_daemon.config import AppConfig

# ---------------------------------------------------------------------------
# warn_if_exposed_without_auth — carried over from Task 6.1
# ---------------------------------------------------------------------------


def test_warn_fires_on_non_loopback_without_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """0.0.0.0 / LAN IP / Tailscale IP with no token -> loud warning."""
    cfg = AppConfig(memory_root=tmp_path, host="0.0.0.0", auth_token=None)
    with caplog.at_level(logging.WARNING):
        cfg.warn_if_exposed_without_auth()
    assert any(
        "NO auth token" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
    )


def test_warn_silent_on_loopback(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """127.0.0.1 / localhost / ::1 produce no warning regardless of token state."""
    for host in ["127.0.0.1", "localhost", "::1"]:
        caplog.clear()
        cfg = AppConfig(memory_root=tmp_path, host=host, auth_token=None)
        with caplog.at_level(logging.WARNING):
            cfg.warn_if_exposed_without_auth()
        assert not caplog.records, f"unexpected warning for loopback host {host}"


def test_warn_silent_with_token(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Non-loopback WITH a token is the intended config and stays silent."""
    cfg = AppConfig(memory_root=tmp_path, host="0.0.0.0", auth_token="secret")
    with caplog.at_level(logging.WARNING):
        cfg.warn_if_exposed_without_auth()
    assert not caplog.records


# ---------------------------------------------------------------------------
# AppConfig.load — defaults, TOML, env overrides
# ---------------------------------------------------------------------------


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrub every REACHY_DUCKY_* var so the test starts from a clean slate."""
    import os

    for key in [k for k in os.environ if k.startswith("REACHY_DUCKY_")]:
        monkeypatch.delenv(key, raising=False)


def test_load_defaults_with_no_toml_no_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No TOML + no env vars → hard-coded defaults."""
    _clear_env(monkeypatch)
    cfg = AppConfig.load(path=tmp_path / "does-not-exist.toml")
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.auth_token is None
    assert cfg.projects == []
    # memory_root default is under the user's home.
    assert "reachy-ducky" in str(cfg.memory_root)


def test_load_parses_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A full TOML config populates every field correctly."""
    _clear_env(monkeypatch)
    toml_path = tmp_path / "config.toml"
    project_dir = tmp_path / "work" / "reachy-ducky"
    project_dir.mkdir(parents=True)
    # ``.as_posix()`` keeps the interpolated paths free of backslashes so
    # ``tomllib`` doesn't see Windows ``C:\Users\...`` as escape sequences
    # (e.g. ``\U`` is a unicode-escape header). Forward slashes are safe
    # cross-platform — Linux/macOS already use them.
    toml_path.write_text(
        f"""
[daemon]
host = "0.0.0.0"
port = 9000
memory_root = "{(tmp_path / "mem").as_posix()}"
auth_token = "hunter2"

[[projects]]
slug = "reachy-ducky"
path = "{project_dir.as_posix()}"
github_repo = "Obsidian-Owl/reachy-ducky"
primary = true
        """
    )
    cfg = AppConfig.load(path=toml_path)
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.memory_root == tmp_path / "mem"
    assert cfg.auth_token == "hunter2"
    assert len(cfg.projects) == 1
    p = cfg.projects[0]
    assert p.slug == "reachy-ducky"
    assert p.path == project_dir.resolve()
    assert p.github_repo == "Obsidian-Owl/reachy-ducky"
    assert p.primary is True


def test_load_env_fallback_single_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-only single-project fallback (backward-compat with pre-6.4 shape)."""
    _clear_env(monkeypatch)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    monkeypatch.setenv("REACHY_DUCKY_PROJECT_ROOT", str(project_dir))
    monkeypatch.setenv("REACHY_DUCKY_PROJECT_SLUG", "envy")
    monkeypatch.setenv("REACHY_DUCKY_GITHUB_REPO", "owner/envy")
    cfg = AppConfig.load(path=tmp_path / "does-not-exist.toml")
    assert len(cfg.projects) == 1
    p = cfg.projects[0]
    assert p.slug == "envy"
    assert p.path == project_dir.resolve()
    assert p.github_repo == "owner/envy"
    assert p.primary is True


def test_load_env_fallback_default_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without REACHY_DUCKY_PROJECT_SLUG, slug defaults to the project dir's basename."""
    _clear_env(monkeypatch)
    project_dir = tmp_path / "some-project"
    project_dir.mkdir()
    monkeypatch.setenv("REACHY_DUCKY_PROJECT_ROOT", str(project_dir))
    cfg = AppConfig.load(path=tmp_path / "does-not-exist.toml")
    assert len(cfg.projects) == 1
    assert cfg.projects[0].slug == "some-project"


def test_env_overrides_toml_on_every_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env vars win over TOML on host / port / memory_root / auth_token."""
    _clear_env(monkeypatch)
    toml_path = tmp_path / "config.toml"
    proj = tmp_path / "p"
    proj.mkdir()
    toml_path.write_text(
        f"""
[daemon]
host = "10.0.0.1"
port = 1111
memory_root = "{(tmp_path / "toml-mem").as_posix()}"
auth_token = "toml-token"

[[projects]]
slug = "toml-proj"
path = "{proj.as_posix()}"
primary = true
        """
    )
    monkeypatch.setenv("REACHY_DUCKY_DAEMON_HOST", "192.168.1.2")
    monkeypatch.setenv("REACHY_DUCKY_DAEMON_PORT", "2222")
    monkeypatch.setenv("REACHY_DUCKY_MEMORY_ROOT", str(tmp_path / "env-mem"))
    monkeypatch.setenv("REACHY_DUCKY_AUTH_TOKEN", "env-token")
    cfg = AppConfig.load(path=toml_path)
    assert cfg.host == "192.168.1.2"
    assert cfg.port == 2222
    assert cfg.memory_root == tmp_path / "env-mem"
    assert cfg.auth_token == "env-token"
    # TOML's project survives (env-fallback only engages when TOML projects is empty).
    assert len(cfg.projects) == 1
    assert cfg.projects[0].slug == "toml-proj"


def test_empty_env_auth_token_clears_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting REACHY_DUCKY_AUTH_TOKEN='' is an explicit "disable token" signal."""
    _clear_env(monkeypatch)
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        """
[daemon]
auth_token = "toml-token"
        """
    )
    monkeypatch.setenv("REACHY_DUCKY_AUTH_TOKEN", "")
    cfg = AppConfig.load(path=toml_path)
    assert cfg.auth_token is None


def test_memory_root_expands_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TOML memory_root with ``~`` is expanded to the user's home."""
    _clear_env(monkeypatch)
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        """
[daemon]
memory_root = "~/custom-mem"
        """
    )
    cfg = AppConfig.load(path=toml_path)
    assert not str(cfg.memory_root).startswith("~")
    # ``.name`` is platform-agnostic; using ``str(...).endswith("/custom-mem")``
    # would fail on Windows where the separator is a backslash.
    assert cfg.memory_root.name == "custom-mem"


def test_project_path_expands_tilde_and_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TOML project path with ``~`` is expanded + resolved."""
    _clear_env(monkeypatch)
    proj = tmp_path / "proj"
    proj.mkdir()
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        f"""
[[projects]]
slug = "demo"
path = "{proj.as_posix()}"
        """
    )
    cfg = AppConfig.load(path=toml_path)
    assert cfg.projects[0].path == proj.resolve()


def test_env_project_fallback_yields_to_toml_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When TOML has projects, REACHY_DUCKY_PROJECT_ROOT does NOT silently add another."""
    _clear_env(monkeypatch)
    toml_proj = tmp_path / "toml-proj"
    toml_proj.mkdir()
    env_proj = tmp_path / "env-proj"
    env_proj.mkdir()
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(
        f"""
[[projects]]
slug = "from-toml"
path = "{toml_proj.as_posix()}"
        """
    )
    monkeypatch.setenv("REACHY_DUCKY_PROJECT_ROOT", str(env_proj))
    cfg = AppConfig.load(path=toml_path)
    assert len(cfg.projects) == 1
    assert cfg.projects[0].slug == "from-toml"


def test_default_path_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``REACHY_DUCKY_CONFIG`` points ``default_path`` at a non-default location."""
    monkeypatch.setenv("REACHY_DUCKY_CONFIG", "/tmp/custom.toml")  # noqa: S108 — test path
    assert AppConfig.default_path() == Path("/tmp/custom.toml")  # noqa: S108


def test_default_path_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env override, ``default_path`` is under ``~/.reachy-ducky``."""
    monkeypatch.delenv("REACHY_DUCKY_CONFIG", raising=False)
    p = AppConfig.default_path()
    assert p.name == "config.toml"
    assert ".reachy-ducky" in str(p)
