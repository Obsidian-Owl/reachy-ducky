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
import threading

from .conversation import run_one_turn
from .daemon_client import DaemonClient
from .embodiment.motion_driver import ReachyMotionDriver
from .embodiment.state_machine import EmbodimentStateMachine
from .voice.audio_io import load_default_mic_source, load_default_speaker_sink
from .voice.openai_realtime import OpenAIRealtimeVoice
from .wake import load_default_wake_detector

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
        sm = EmbodimentStateMachine(driver=driver)
        voice = OpenAIRealtimeVoice(
            mic=load_default_mic_source(reachy_mini=reachy_mini),
            speaker=load_default_speaker_sink(reachy_mini=reachy_mini),
        )
        daemon = DaemonClient.from_env()
        wake = load_default_wake_detector()

        stop_checker = asyncio.create_task(_watch_stop(stop_event))
        try:
            while not stop_event.is_set():
                wake_waiter = asyncio.create_task(wake.event.wait())
                try:
                    await asyncio.wait(
                        {wake_waiter, stop_checker},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    # Cancel AND drain the waiter when it's still pending.
                    # ``cancel()`` alone only marks the task; dropping the
                    # reference before it observes the cancellation emits
                    # "Task was destroyed but it is pending" warnings and
                    # leaves un-retrieved ``CancelledError`` on the event
                    # loop. Awaiting with suppression is the canonical
                    # asyncio pattern for one-shot cancel + cleanup.
                    if not wake_waiter.done():
                        wake_waiter.cancel()
                        try:
                            await wake_waiter
                        except asyncio.CancelledError:
                            pass
                if stop_event.is_set():
                    break
                wake.event.clear()
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
