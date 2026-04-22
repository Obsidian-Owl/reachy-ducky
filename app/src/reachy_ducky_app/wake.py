"""Wake-word detection: abstract contract + deterministic mock + factory.

Phase A ships with :class:`MockWakeDetector`; Task 8.2+ swaps in an ONNX-backed
real implementation (e.g. openWakeWord or a community Hugging Face Space
model) without touching callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import numpy.typing as npt


class WakeDetector(ABC):
    """Consumes raw audio chunks, returns True once per wake-word hit.

    Real implementations wrap an ONNX model. :class:`MockWakeDetector` here
    is pure Python and deterministic for tests.
    """

    @abstractmethod
    def feed_audio(self, audio_chunk: npt.NDArray[np.int16]) -> bool:
        """Return True when the wake word is detected in this chunk."""


class MockWakeDetector(WakeDetector):
    """Test double.

    ``feed_audio`` always returns False; ``detect_in_text`` is a simple
    substring check so tests can simulate "heard the phrase".
    """

    def __init__(self, trigger_on: str = "hey ducky") -> None:
        self._trigger = trigger_on.lower()

    def feed_audio(self, audio_chunk: npt.NDArray[np.int16]) -> bool:
        del audio_chunk  # mock doesn't analyse audio
        return False

    def detect_in_text(self, text: str) -> bool:
        return self._trigger in text.lower()


def load_default_wake_detector() -> WakeDetector:
    """Factory for the production wake detector.

    Phase A ships a mock; a later task swaps in the ONNX-backed real one.
    Separating load-time selection from class definition keeps the mock
    directly constructible in unit tests without going through a factory
    that might eventually spawn subprocesses.
    """
    # TODO: swap in the ONNX-backed detector (openWakeWord / community HF
    # Space model) once Task 8.2+ lands.
    return MockWakeDetector()
