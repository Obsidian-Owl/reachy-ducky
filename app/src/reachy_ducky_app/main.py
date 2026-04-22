"""Reachy Ducky app entry point.

Structurally satisfies ``reachy_mini_app.ReachyMiniApp``: the on-robot Pollen
daemon (distinct from our Mac daemon — this is the robot's process manager
hosting the dashboard at ``http://<robot>:8000``) calls
``.run(reachy_mini, stop_event)`` when the app is started from the
dashboard.

**Hardware-only.** ``reachy_mini`` and ``reachy_mini_app`` live in the
``robot`` optional extra (see ``app/pyproject.toml``) because their
transitive deps break on non-Linux dev machines. This module is
importable on any platform because we do NOT inherit from
``ReachyMiniApp`` at class-definition time — we match its structural
shape instead. All actual robot-side usage lives inside :meth:`run` /
:meth:`_run_async`, which are called by the on-robot daemon (where the
``robot`` extra is installed) and never at import time.
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
from .wake import WakeDetector, load_default_wake_detector


class ReachyDuckyApp:
    """Entry point matching ``reachy_mini_app.ReachyMiniApp``'s shape.

    We deliberately do NOT inherit from ``reachy_mini_app.ReachyMiniApp``
    at class-definition time: the ``reachy_mini_app`` package is in the
    ``robot`` optional extra (not installed on macOS / Linux CI dev
    venvs). The on-robot dashboard instantiates this class and calls
    ``.run(reachy_mini, stop_event)`` structurally — no Python
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
        """Construct the piece graph and poll for wake events until stopped.

        The daemon client pools an ``httpx.AsyncClient`` across turns; we
        wrap the wake loop in ``try/finally`` so the pool is drained on
        any exit path — clean shutdown, cancellation, or a crash inside
        ``run_one_turn``.
        """
        driver = ReachyMotionDriver(reachy_mini)
        sm = EmbodimentStateMachine(driver=driver)
        voice = OpenAIRealtimeVoice(
            mic=load_default_mic_source(),
            speaker=load_default_speaker_sink(),
        )
        daemon = DaemonClient.from_env()
        wake = load_default_wake_detector()

        try:
            while not stop_event.is_set():
                # Phase A simplification: wake detection is stubbed (the default
                # `MockWakeDetector.feed_audio` returns False unconditionally). A
                # future task swaps in the real ONNX-backed detector and wires it
                # to an audio pump that calls `wake.feed_audio(chunk)` on each
                # mic buffer; `_wake_triggered` is then the bridge between that
                # pump and the per-turn conversation loop.
                if self._wake_triggered(wake):
                    await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug=None)
                await asyncio.sleep(0.05)
        finally:
            await daemon.aclose()

    def _wake_triggered(self, wake: WakeDetector) -> bool:
        """Placeholder: returns False until the real audio pump is wired.

        Overridable in tests to simulate wake events without needing the
        ONNX model or mic hardware.
        """
        del wake
        return False


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
