"""Tests for :mod:`reachy_ducky_app.main` — the Reachy-side entry point.

These tests do NOT install the ``robot`` extra (``reachy_mini`` /
``reachy_mini_app``). The module is designed to be importable on Linux CI
and macOS dev venvs where those hardware-only deps are absent; tests
verify the construction path via a fake ``reachy_mini`` stand-in.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from reachy_ducky_app.main import ReachyDuckyApp, main
from reachy_ducky_app.wake import MockWakeDetector, WakeDetector


def test_reachy_ducky_app_imports_cleanly() -> None:
    """``ReachyDuckyApp`` + ``main`` import without the robot extra installed."""
    assert ReachyDuckyApp is not None
    assert callable(main)


def test_wake_triggered_placeholder_returns_false() -> None:
    """Baseline lock: the Phase A ``_wake_triggered`` stub always returns False."""
    app = ReachyDuckyApp()
    assert app._wake_triggered(MockWakeDetector()) is False


async def test_run_async_exits_immediately_when_stop_event_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-set stop_event: ``_run_async`` must return without running any turn."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[object] = []

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    assert run_one_turn_calls == []


async def test_run_async_calls_run_one_turn_when_wake_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wake trigger True on first tick => exactly one ``run_one_turn`` call."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[dict[str, object]] = []

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)

    stop = threading.Event()

    class _OneShotApp(ReachyDuckyApp):
        """Trigger wake exactly once, then stop the loop on the next tick."""

        def __init__(self) -> None:
            self._calls = 0

        def _wake_triggered(self, wake: WakeDetector) -> bool:
            del wake
            self._calls += 1
            if self._calls == 1:
                return True
            stop.set()
            return False

    app = _OneShotApp()
    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=1.0,
    )

    assert len(run_one_turn_calls) == 1
    assert "voice" in run_one_turn_calls[0]
    assert "sm" in run_one_turn_calls[0]
    assert "daemon" in run_one_turn_calls[0]
    assert run_one_turn_calls[0]["project_slug"] is None


def test_main_exits_with_stopped_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` pre-sets the stop event and returns cleanly (no hang)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    # If main() hangs, pytest's per-test timeout (or CI watchdog) will catch
    # it; belt-and-braces, the stop_event.set() inside main() guarantees the
    # loop body is skipped, so this returns synchronously.
    main()
