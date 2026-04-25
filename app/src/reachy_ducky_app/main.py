"""Reachy Ducky app entry point.

Structurally satisfies ``reachy_mini_app.ReachyMiniApp``: the on-robot Pollen
daemon (distinct from our Mac daemon — this is the robot's process manager
hosting the dashboard at ``http://<robot>:8000``) calls
``.run(reachy_mini, stop_event)`` when the app is started from the
dashboard.

``reachy_mini`` and ``reachy_mini_app`` are plain base deps (see
``app/pyproject.toml``); the upstream ``gstreamer-msvc-runtime``
platform-marker bug is patched at the workspace root via
``[tool.uv] dependency-metadata``, so they install on Mac/Linux/Windows
dev venvs AND on the robot. This module still does NOT inherit from
``ReachyMiniApp`` at class-definition time — we match its structural
shape instead — to keep module-load cheap: importing ``ReachyMiniApp``
would pull in the WebRTC / media-pipeline side-effects at import time.
All actual robot-side usage lives inside :meth:`run` / :meth:`_run_async`,
which are called by the on-robot daemon and never at import time.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading

from reachy_ducky_protocol.messages import State

from .conversation import run_one_turn
from .daemon_client import DaemonClient
from .embodiment.motion_driver import ReachyMotionDriver
from .embodiment.state_machine import EmbodimentStateMachine
from .mute import MuteGate
from .voice.audio_io import MicSource, load_default_mic_source, load_default_speaker_sink
from .voice.openai_realtime import OpenAIRealtimeVoice
from .wake import WakeDetector, load_default_wake_detector

# How often the stop-bridge checks ``threading.Event.is_set()``. This is a
# memory-load poll only — the wake path itself awaits ``wake.event`` via
# ``asyncio.wait(FIRST_COMPLETED)`` and never spins. ``threading.Event``
# has no native async ``wait``; a 10 Hz check against a shared memory flag
# is far cheaper than the original 20 Hz wake-loop re-entry, and avoids
# pulling in ``janus`` / ``asyncio.to_thread`` plumbing we don't need yet.
_STOP_BRIDGE_POLL_SECONDS = 0.1


class ReachyDuckyApp:
    """Entry point matching ``reachy_mini_app.ReachyMiniApp``'s shape.

    ``reachy_mini_app`` installs alongside ``reachy_mini`` as a plain
    base dep, but we still deliberately do NOT inherit from
    ``reachy_mini_app.ReachyMiniApp`` at class-definition time: doing so
    would force the WebRTC / media-pipeline imports at module-load. The
    structural-shape pattern keeps module-load cheap for unit tests and
    non-robot dev runs. The on-robot dashboard instantiates this class
    and calls ``.run(reachy_mini, stop_event)`` structurally — no Python
    inheritance is required for the dispatch to work.
    """

    def run(self, reachy_mini: object, stop_event: threading.Event) -> None:
        """Start the app's main loop. Called by the on-robot Pollen daemon."""
        asyncio.run(self._run_async(reachy_mini, stop_event))

    async def _run_async(
        self,
        reachy_mini: object,
        stop_event: threading.Event,
    ) -> None:
        """Construct the piece graph and await wake events until stopped.

        The main loop blocks on ``asyncio.wait({wake.event.wait(),
        stop_checker}, return_when=FIRST_COMPLETED)`` — the wake path is
        pure event-driven (no polling), and the stop path is a tiny 10 Hz
        memory-load check (see :data:`_STOP_BRIDGE_POLL_SECONDS`) bridging
        the on-robot daemon's ``threading.Event`` into asyncio.

        The daemon client pools an ``httpx.AsyncClient`` across turns; we
        wrap the wake loop in ``try/finally`` so the pool is drained on
        any exit path — clean shutdown, cancellation, or a crash inside
        ``run_one_turn``.
        """
        driver = ReachyMotionDriver(reachy_mini)
        # Single shared gate: state machine toggles it on MUTED transitions
        # (Task A.1); the mic pump reads it on every frame (Task A.2). Both
        # consumers MUST see the same instance or the zero-at-source
        # invariant breaks.
        mute_gate = MuteGate()
        sm = EmbodimentStateMachine(driver=driver, mute_gate=mute_gate)
        # Hoist mic out of voice construction so the wake pump (Phase 1)
        # and the voice turn (Phase 2) can share a single mic instance
        # without OpenAIRealtimeVoice having to expose a public accessor.
        # Pattern C requires that exactly one consumer pull frames at a
        # time; sharing the instance here keeps that invariant local to
        # this loop body.
        mic = load_default_mic_source(reachy_mini=reachy_mini, mute_gate=mute_gate)
        voice = OpenAIRealtimeVoice(
            mic=mic,
            speaker=load_default_speaker_sink(reachy_mini=reachy_mini),
        )
        daemon = DaemonClient.from_env()
        wake = load_default_wake_detector()

        stop_checker = asyncio.create_task(_watch_stop(stop_event))
        try:
            while not stop_event.is_set():
                # Phase 1 — wake-listening. Wake pump owns the mic.
                wake.reset()
                pump_task = asyncio.create_task(_run_wake_pump(mic, wake))
                wake_waiter = asyncio.create_task(wake.event.wait())
                try:
                    await asyncio.wait(
                        {pump_task, stop_checker, wake_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not pump_task.done():
                        pump_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pump_task
                    if not wake_waiter.done():
                        wake_waiter.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await wake_waiter
                if stop_event.is_set():
                    break
                # Only enter Phase 2 if wake actually fired. The pump task
                # can also complete because the mic iterator ended (e.g.
                # ``MockMicSource`` yields nothing); in that case we loop
                # back to Phase 1 without firing a turn. ``ReachyMicSource``
                # never returns naturally, so on hardware the pump only
                # exits via wake-fire or cancellation.
                if not wake.event.is_set():
                    continue
                wake.event.clear()

                # Phase 2 — turn. Wake confirmed; transition the embodiment
                # state so the human gets the "I heard you" affordance, then
                # let voice take exclusive mic ownership for the turn.
                sm.transition(State.LISTENING)
                await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug=None)
        finally:
            stop_checker.cancel()
            try:
                await stop_checker
            except asyncio.CancelledError:
                pass
            await daemon.aclose()

    def _wake_triggered(self) -> bool:
        """Deprecated test-override seam; no longer called by :meth:`_run_async`.

        Kept so that subclassing to force-trigger a wake via method
        override remains a stable escape hatch for future refactors. The
        event-driven loop in :meth:`_run_async` drives turns via
        ``wake.event`` only, so the default ``False`` body is harmless.
        """
        return False


async def _run_wake_pump(mic: MicSource, wake: WakeDetector) -> None:
    """Pull frames from the mic and feed the wake detector until cancelled.

    Cancellation is the normal exit path — caller cancels this task once
    ``wake.event`` fires (or stop is requested). The detector signals
    detection by setting its event; the caller observes via the event,
    not via this coroutine's return value.

    Pattern C invariant: this coroutine is the only mic consumer during
    Phase 1. ``run_one_turn`` consumes the mic during Phase 2, after this
    task has been cancelled and awaited.
    """
    async for frame in mic.frames():
        wake.feed(frame)
        if wake.event.is_set():
            return


async def _watch_stop(stop_event: threading.Event) -> None:
    """Bridge ``threading.Event`` into asyncio so ``asyncio.wait`` can select it.

    10 Hz memory-load poll (see :data:`_STOP_BRIDGE_POLL_SECONDS`) — NOT
    a wake poll. The wake path is fully event-driven. We accept this
    small residual polling because ``threading.Event`` has no native
    async ``wait``; alternatives (``janus.Queue`` or
    ``asyncio.to_thread(stop_event.wait)``) are zero-poll but add
    complexity that isn't justified today.
    """
    while not stop_event.is_set():
        await asyncio.sleep(_STOP_BRIDGE_POLL_SECONDS)


def main() -> None:
    """Module-level entry for ``uv run reachy-ducky-app`` (local dev smoke).

    The production path is the on-robot dashboard instantiating
    :class:`ReachyDuckyApp` via its HF Space entry point. This ``main()``
    provides a dev-loop on a Mac without hardware — it will exit
    immediately because the ``stop_event`` is pre-set. Useful mainly for
    verifying that the import chain wires up.
    """
    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()  # immediate exit; dev-loop only
    app.run(reachy_mini=None, stop_event=stop)
