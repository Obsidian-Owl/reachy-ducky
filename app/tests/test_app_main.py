"""Tests for :mod:`reachy_ducky_app.main` — the Reachy-side entry point.

These tests do NOT install the ``robot`` extra (``reachy_mini`` /
``reachy_mini_app``). The module is designed to be importable on Linux CI
and macOS dev venvs where those hardware-only deps are absent; tests
verify the construction path via a fake ``reachy_mini`` stand-in.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from reachy_ducky_app.main import ReachyDuckyApp, main
from reachy_ducky_app.voice.audio_io import MicSource, MockMicSource, MockSpeakerSink, SpeakerSink
from reachy_ducky_app.wake import MockWakeDetector


def test_reachy_ducky_app_imports_cleanly() -> None:
    """``ReachyDuckyApp`` + ``main`` import without the robot extra installed."""
    assert ReachyDuckyApp is not None
    assert callable(main)


def test_wake_triggered_placeholder_returns_false() -> None:
    """Baseline lock: the Phase A ``_wake_triggered`` stub always returns False."""
    app = ReachyDuckyApp()
    assert app._wake_triggered() is False


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


async def test_wake_event_drives_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_run_async`` awaits the wake Event — setting it triggers exactly one turn.

    The event-driven contract: the loop blocks on ``wake.event.wait()`` via
    ``asyncio.wait(FIRST_COMPLETED)``. Firing the event once must drive
    exactly one ``run_one_turn``, not be busy-polled.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[dict[str, object]] = []
    stop = threading.Event()

    injected_wake = MockWakeDetector()

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)
        # One turn is enough; set stop so the loop exits on the next cycle.
        stop.set()

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )

    async def _fire_wake_soon() -> None:
        # Let _run_async reach its asyncio.wait; then set the event.
        await asyncio.sleep(0)
        injected_wake.event.set()

    app = ReachyDuckyApp()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_fire_wake_soon())
        tg.create_task(
            asyncio.wait_for(
                app._run_async(reachy_mini=object(), stop_event=stop),
                timeout=1.0,
            )
        )

    assert len(run_one_turn_calls) == 1
    assert "voice" in run_one_turn_calls[0]
    assert "sm" in run_one_turn_calls[0]
    assert "daemon" in run_one_turn_calls[0]
    assert run_one_turn_calls[0]["project_slug"] is None


async def test_wake_loop_exits_cleanly_without_firing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop event causes clean exit when the wake event never fires.

    Verifies the stop-bridge wins when ``wake.event`` stays unset: zero
    turns run, and ``_run_async`` returns promptly after ``stop_event.set()``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    run_one_turn_calls: list[object] = []

    async def _spy_run_one_turn(**kwargs: object) -> None:
        run_one_turn_calls.append(kwargs)

    injected_wake = MockWakeDetector()

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _spy_run_one_turn)
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )

    stop = threading.Event()

    async def _stop_soon() -> None:
        await asyncio.sleep(0.15)  # > the 0.1s stop-bridge tick
        stop.set()

    app = ReachyDuckyApp()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(_stop_soon())
        tg.create_task(
            asyncio.wait_for(
                app._run_async(reachy_mini=object(), stop_event=stop),
                timeout=1.0,
            )
        )

    assert run_one_turn_calls == []
    assert not injected_wake.event.is_set()


def test_main_exits_with_stopped_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main()`` pre-sets the stop event and returns cleanly (no hang)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    # If main() hangs, pytest's per-test timeout (or CI watchdog) will catch
    # it; belt-and-braces, the stop_event.set() inside main() guarantees the
    # loop body is skipped, so this returns synchronously.
    main()


async def test_run_async_closes_daemon_client_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On shutdown, the app calls ``daemon.aclose()`` to release the pool.

    Regression for bug I2: pools the httpx.AsyncClient across calls, so
    it must be drained on stop. We mock ``DaemonClient.from_env`` to
    return a spy whose ``aclose`` is an :class:`AsyncMock`, immediately
    stop the event, and assert ``aclose`` was awaited exactly once.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    fake_daemon = MagicMock()
    fake_daemon.aclose = AsyncMock()
    monkeypatch.setattr(
        "reachy_ducky_app.main.DaemonClient.from_env",
        MagicMock(return_value=fake_daemon),
    )

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    fake_daemon.aclose.assert_awaited_once()


async def test_run_async_constructs_voice_with_default_mic_and_speaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The app wires the default audio_io factories into OpenAIRealtimeVoice.

    Guards the commit-3 wiring: ``_run_async`` must call
    :func:`load_default_mic_source` and :func:`load_default_speaker_sink`
    rather than hard-coding Mock impls directly. When the hardware-backed
    factory bodies land, ``main.py`` should require zero changes.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    mic_factory_calls = 0
    speaker_factory_calls = 0

    def _spy_mic_factory() -> MicSource:
        nonlocal mic_factory_calls
        mic_factory_calls += 1
        return MockMicSource()

    def _spy_speaker_factory() -> SpeakerSink:
        nonlocal speaker_factory_calls
        speaker_factory_calls += 1
        return MockSpeakerSink()

    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        _spy_mic_factory,
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_speaker_sink",
        _spy_speaker_factory,
    )

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()

    await asyncio.wait_for(
        app._run_async(reachy_mini=object(), stop_event=stop),
        timeout=0.5,
    )

    assert mic_factory_calls == 1
    assert speaker_factory_calls == 1


async def test_run_async_closes_daemon_client_even_when_turn_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``run_one_turn`` raises, ``daemon.aclose()`` must still be awaited.

    Guards that the ``try/finally`` wraps the whole wake loop, so the
    pool is drained on crash as well as clean shutdown. Drives the loop
    via a pre-set ``wake.event`` so the first ``asyncio.wait`` immediately
    selects the wake branch.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    fake_daemon = MagicMock()
    fake_daemon.aclose = AsyncMock()
    monkeypatch.setattr(
        "reachy_ducky_app.main.DaemonClient.from_env",
        MagicMock(return_value=fake_daemon),
    )

    class _BoomError(RuntimeError):
        pass

    async def _blowing_up(**kwargs: object) -> None:
        raise _BoomError("turn failed")

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _blowing_up)

    injected_wake = MockWakeDetector()
    injected_wake.event.set()
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: injected_wake,
    )

    stop = threading.Event()
    app = ReachyDuckyApp()

    with pytest.raises(_BoomError):
        await asyncio.wait_for(
            app._run_async(reachy_mini=object(), stop_event=stop),
            timeout=0.5,
        )

    fake_daemon.aclose.assert_awaited_once()
