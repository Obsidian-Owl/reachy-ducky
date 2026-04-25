"""Wake-word detection: abstract contract + deterministic mock + factory."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from reachy_ducky_app.voice.audio_io import AudioFrame


class WakeDetector(ABC):
    """Consumes ``AudioFrame`` tuples; signals detection via :attr:`event`.

    Real implementations wrap an ONNX model. :class:`MockWakeDetector` here
    is pure Python and deterministic for tests.

    The detector exposes :attr:`event` — an ``asyncio.Event`` that
    :meth:`feed` sets when the wake word is detected. The wake pump in
    ``main._run_async`` awaits this event and cancels its own pull loop
    on detection.

    Constructing a ``WakeDetector`` outside an event loop is safe:
    Python 3.10+ ``asyncio.Event()`` is loop-agnostic until ``wait()``
    is called.
    """

    def __init__(self) -> None:
        self.event: asyncio.Event = asyncio.Event()

    @abstractmethod
    def feed(self, frame: AudioFrame) -> None:
        """Consume one ``AudioFrame`` and update internal state.

        Implementations MUST set ``self.event`` when the wake word is
        detected. Synchronous: must return promptly so the wake pump's
        async loop stays responsive. ONNX inference (~5 ms per 80 ms
        chunk on Pi-class CPUs) is well inside the audio frame budget.
        """

    @abstractmethod
    def reset(self) -> None:
        """Drop internal buffers and clear the event.

        Called between turns by ``main._run_async`` so the first frame
        of the next listening phase can't replay a stale hit from the
        prior phase's accumulator.
        """


class MockWakeDetector(WakeDetector):
    """Test double.

    ``feed`` is a no-op by default. Pass ``trigger_on_feed=True`` to set
    :attr:`event` on every ``feed`` call — useful for exercising the
    event-driven loop without ONNX or hardware.

    **Do NOT use ``trigger_on_feed=True`` in production wiring.** Pattern
    C's wake pump cancels itself on detection, but a continuously-firing
    detector inside the pump iteration would still re-set the event
    after the pump cancels. It's strictly a test hook.

    ``detect_in_text`` is a substring check so tests can simulate
    "heard the phrase".
    """

    def __init__(
        self,
        trigger_on: str = "hey ducky",
        *,
        trigger_on_feed: bool = False,
    ) -> None:
        super().__init__()
        self._trigger = trigger_on.lower()
        self._trigger_on_feed = trigger_on_feed

    def feed(self, frame: AudioFrame) -> None:
        del frame
        if self._trigger_on_feed:
            self.event.set()

    def reset(self) -> None:
        self.event.clear()

    def detect_in_text(self, text: str) -> bool:
        return self._trigger in text.lower()


def load_default_wake_detector() -> WakeDetector:
    """Factory for the production wake detector.

    Returns a real :class:`OpenWakeWordDetector` by default; returns
    :class:`MockWakeDetector` when ``REACHY_DUCKY_WAKE_MOCK=1`` is set
    in the environment (test/dev escape hatch only).

    Raises:
        RuntimeError: if the vendored weights are missing and no mock
            override is set.
    """
    import os

    if os.environ.get("REACHY_DUCKY_WAKE_MOCK") == "1":
        return MockWakeDetector()
    # OpenWakeWordDetector ships in Task 4 of #55; this branch exists so
    # Task 3 can be committed independently with a passing test suite.
    # Until Task 4 lands the import will fail loudly. The type: ignore
    # is intentional and removed by Task 4 when wake_onnx materialises.
    from reachy_ducky_app.wake_onnx import (  # type: ignore[import-not-found]  # noqa: PLC0415
        OpenWakeWordDetector,
    )

    detector: WakeDetector = OpenWakeWordDetector.from_vendored_weights()
    return detector
