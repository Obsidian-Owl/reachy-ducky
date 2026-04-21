"""Tests for the daemon Config, especially warn_if_exposed_without_auth."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from reachy_ducky_daemon.config import Config


def test_warn_fires_on_non_loopback_without_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """0.0.0.0 / LAN IP / Tailscale IP with no token -> loud warning."""
    cfg = Config(memory_root=tmp_path, host="0.0.0.0", auth_token=None)
    with caplog.at_level(logging.WARNING):
        cfg.warn_if_exposed_without_auth()
    assert any(
        "NO auth token" in rec.message and rec.levelno == logging.WARNING for rec in caplog.records
    )


def test_warn_silent_on_loopback(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """127.0.0.1 / localhost / ::1 produce no warning regardless of token state."""
    for host in ["127.0.0.1", "localhost", "::1"]:
        caplog.clear()
        cfg = Config(memory_root=tmp_path, host=host, auth_token=None)
        with caplog.at_level(logging.WARNING):
            cfg.warn_if_exposed_without_auth()
        assert not caplog.records, f"unexpected warning for loopback host {host}"


def test_warn_silent_with_token(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Non-loopback WITH a token is the intended config and stays silent."""
    cfg = Config(memory_root=tmp_path, host="0.0.0.0", auth_token="secret")
    with caplog.at_level(logging.WARNING):
        cfg.warn_if_exposed_without_auth()
    assert not caplog.records
