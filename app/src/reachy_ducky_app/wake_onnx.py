"""ONNX-backed wake-word detector. See docs/plans/2026-04-25-onnx-wake-detector-design.md."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import scipy.signal

from reachy_ducky_app.voice.audio_io import AudioFrame
from reachy_ducky_app.wake import WakeDetector

_OWW_SAMPLE_RATE = 16_000
_OWW_WINDOW_SAMPLES = 1280  # 80 ms @ 16 kHz — openWakeWord's expected chunk size

# Resolved at import time so monkeypatch-replacing these module attrs in
# tests is straightforward. ``openwakeword.utils.AudioFeatures`` defaults
# the melspec / embedding paths to its package's ``resources/models/``
# dir — which is empty on a fresh install (see #79 review). We must
# pass our vendored copies through to ``Model()`` explicitly, not just
# ``hey_jarvis.onnx``.
_VENDORED_WAKE_DIR: Path = Path(str(files("reachy_ducky_app.assets.wake")))
_VENDORED_MODEL_PATH: Path = _VENDORED_WAKE_DIR / "hey_jarvis.onnx"
_VENDORED_MELSPEC_PATH: Path = _VENDORED_WAKE_DIR / "melspectrogram.onnx"
_VENDORED_EMBEDDING_PATH: Path = _VENDORED_WAKE_DIR / "embedding_model.onnx"


class OpenWakeWordDetector(WakeDetector):
    """ONNX-backed wake detector.

    Inference is synchronous — runs inside ``feed()`` on the event loop.
    openWakeWord per-frame inference is ~5 ms on Pi-class CPUs (well
    inside an 80ms audio frame budget); staying on the event loop
    sidesteps the ``call_soon_threadsafe`` thread-safety contract that
    plagued earlier WakeDetector designs.

    Audio rate adaptation: the workspace's ``AudioFrame`` is 24 kHz
    int16 mono (set by ``ReachyMicSource``), but openWakeWord expects
    16 kHz. We resample inside ``feed()`` before buffering into 1280-
    sample windows.
    """

    def __init__(
        self,
        *,
        model: Any,  # duck-typed — accepts openwakeword.Model OR FakeOWWModel
        threshold: float = 0.5,
    ) -> None:
        super().__init__()
        self._model = model
        self._threshold = threshold
        self._buffer: npt.NDArray[np.int16] = np.zeros(0, dtype=np.int16)

    @classmethod
    def from_vendored_weights(
        cls,
        *,
        threshold: float = 0.5,
        vad_threshold: float = 0.3,
    ) -> OpenWakeWordDetector:
        """Construct using the vendored ONNX weights at install time."""
        for path, name in (
            (_VENDORED_MODEL_PATH, "wake"),
            (_VENDORED_MELSPEC_PATH, "melspectrogram"),
            (_VENDORED_EMBEDDING_PATH, "embedding"),
        ):
            if not path.is_file():
                msg = (
                    f"Vendored {name} model not found at {path}. Run "
                    "`uv sync` to install vendored weights, or set "
                    "REACHY_DUCKY_WAKE_MOCK=1 for the mock detector (tests only)."
                )
                raise RuntimeError(msg)
        from openwakeword.model import Model  # type: ignore[import-untyped]  # noqa: PLC0415

        # Pass melspec / embedding paths via kwargs so ``AudioFeatures``
        # uses our vendored copies instead of looking for them inside
        # ``site-packages/openwakeword/resources/models/`` (which is
        # empty on fresh installs — openwakeword lazy-downloads them).
        model = Model(
            wakeword_models=[str(_VENDORED_MODEL_PATH)],
            vad_threshold=vad_threshold,
            inference_framework="onnx",
            melspec_model_path=str(_VENDORED_MELSPEC_PATH),
            embedding_model_path=str(_VENDORED_EMBEDDING_PATH),
        )
        return cls(model=model, threshold=threshold)

    def feed(self, frame: AudioFrame) -> None:
        sample_rate, samples = frame
        if sample_rate != _OWW_SAMPLE_RATE:
            new_len = round(len(samples) * _OWW_SAMPLE_RATE / sample_rate)
            resampled = cast(
                npt.NDArray[np.float32],
                scipy.signal.resample(samples, new_len),
            )
            # FFT-based resample can overshoot int16 range on sharp
            # transients (e.g. consonants in "hey jarvis"). A naive
            # ``.astype(np.int16)`` wraps modularly (float 32801 → int16
            # -32735) which corrupts the wake input. Clip first.
            samples = np.clip(resampled, -32768, 32767).astype(np.int16)
        self._buffer = np.concatenate((self._buffer, samples))
        while len(self._buffer) >= _OWW_WINDOW_SAMPLES:
            chunk = self._buffer[:_OWW_WINDOW_SAMPLES]
            self._buffer = self._buffer[_OWW_WINDOW_SAMPLES:]
            scores = self._model.predict(chunk)
            if any(score >= self._threshold for score in scores.values()):
                self.event.set()
                return

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.int16)
        self.event.clear()
        if hasattr(self._model, "reset"):
            self._model.reset()
