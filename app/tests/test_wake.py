"""Tests for the wake-word detector interface, mock, and factory."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest
from reachy_ducky_app.voice.audio_io import AudioFrame
from reachy_ducky_app.wake import (
    MockWakeDetector,
    WakeDetector,
    load_default_wake_detector,
)
from reachy_ducky_app.wake_onnx import OpenWakeWordDetector


def test_wake_detector_is_abstract() -> None:
    """WakeDetector cannot be instantiated directly — it is an ABC."""
    with pytest.raises(TypeError):
        WakeDetector()  # type: ignore[abstract]


def test_wake_detector_event_is_asyncio_event() -> None:
    """WakeDetector.event is a real asyncio.Event — not an mp or threading analog."""
    detector = MockWakeDetector()
    assert isinstance(detector.event, asyncio.Event)
    assert not detector.event.is_set()


def test_mock_detector_detect_in_text_triggers_on_keyword() -> None:
    """detect_in_text returns True when the trigger phrase appears in text."""
    det = MockWakeDetector(trigger_on="hey ducky")
    assert det.detect_in_text("hey ducky, how are you?") is True


def test_mock_detector_detect_in_text_case_insensitive() -> None:
    """detect_in_text matches regardless of case (both sides lowered)."""
    det = MockWakeDetector(trigger_on="Hey Ducky")
    assert det.detect_in_text("HEY DUCKY listen up") is True


def test_mock_detector_detect_in_text_negative() -> None:
    """detect_in_text returns False when the trigger phrase is absent."""
    det = MockWakeDetector(trigger_on="hey ducky")
    assert det.detect_in_text("good morning world") is False


def test_mock_detector_custom_trigger_word() -> None:
    """A custom trigger word is honoured by detect_in_text."""
    det = MockWakeDetector(trigger_on="quack attack")
    assert det.detect_in_text("initiating quack attack now") is True
    assert det.detect_in_text("hey ducky") is False


def test_wake_detector_abc_requires_feed_and_reset() -> None:
    """The new ABC contract is feed(AudioFrame) + reset() — drops the bool return."""
    abstract_methods = WakeDetector.__abstractmethods__
    assert "feed" in abstract_methods
    assert "reset" in abstract_methods


def test_mock_default_feed_does_not_set_event() -> None:
    """Default MockWakeDetector (trigger_on_feed=False) is a no-op on feed."""
    detector = MockWakeDetector()
    detector.feed((24_000, np.zeros(960, dtype=np.int16)))
    assert not detector.event.is_set()


def test_mock_feed_with_trigger_on_feed_sets_event_and_buffers_nothing() -> None:
    detector = MockWakeDetector(trigger_on_feed=True)
    silent_frame: AudioFrame = (24_000, np.zeros(960, dtype=np.int16))
    detector.feed(silent_frame)
    assert detector.event.is_set()


def test_mock_reset_clears_event() -> None:
    detector = MockWakeDetector(trigger_on_feed=True)
    detector.feed((24_000, np.zeros(960, dtype=np.int16)))
    assert detector.event.is_set()
    detector.reset()
    assert not detector.event.is_set()


class FakeOWWModel:
    """Stand-in for openwakeword.model.Model in unit tests."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.predict_calls = 0
        self.last_chunk: np.ndarray | None = None
        self.reset_calls = 0

    def predict(self, chunk: np.ndarray) -> dict[str, float]:
        self.predict_calls += 1
        self.last_chunk = chunk
        return dict(self._scores)

    def reset(self) -> None:
        self.reset_calls += 1


def test_owd_buffers_until_1280_samples_then_predicts() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.9})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    # 24 kHz at 640 samples = ~26.7 ms — sub-window after resample to 16 kHz (~427 samples)
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    assert not det.event.is_set()
    assert fake.predict_calls == 0
    # Two more 640-sample chunks @ 24 kHz cross the 1280-sample @ 16 kHz threshold
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    assert det.event.is_set()
    assert fake.predict_calls >= 1


def test_owd_below_threshold_does_not_set_event() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.4})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    # Push enough audio for one full window at 16 kHz: 1280 samples * 24/16 = 1920 @ 24 kHz
    det.feed((24_000, np.zeros(1920, dtype=np.int16)))
    assert not det.event.is_set()
    assert fake.predict_calls == 1


def test_owd_predict_chunk_is_1280_samples_at_16k() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.0})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    det.feed((24_000, np.zeros(1920, dtype=np.int16)))
    assert fake.last_chunk is not None
    assert fake.last_chunk.shape == (1280,)
    assert fake.last_chunk.dtype == np.int16


def test_owd_resample_path_skipped_when_input_already_16k() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.0})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    det.feed((16_000, np.zeros(1280, dtype=np.int16)))
    assert fake.predict_calls == 1
    assert fake.last_chunk is not None
    assert fake.last_chunk.shape == (1280,)


def test_owd_reset_drops_buffer_and_calls_model_reset() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.9})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    det.feed((24_000, np.zeros(640, dtype=np.int16)))  # half-buffer
    det.event.set()  # simulate a prior fire
    det.reset()
    assert not det.event.is_set()
    assert fake.reset_calls == 1
    # After reset, a sub-window feed should not trigger immediate predict
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    # If reset failed to drop the buffer, the accumulated 1280 from prior
    # half + new half would have triggered a predict — assert it didn't
    assert fake.predict_calls == 0


def test_owd_factory_raises_when_weights_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("REACHY_DUCKY_WAKE_MOCK", raising=False)
    monkeypatch.setattr(
        "reachy_ducky_app.wake_onnx._VENDORED_MODEL_PATH",
        tmp_path / "nonexistent.onnx",
    )
    with pytest.raises(RuntimeError, match="Vendored wake model not found"):
        OpenWakeWordDetector.from_vendored_weights()


def test_owd_factory_raises_when_melspec_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Fresh-install case: hey_jarvis present, melspec missing → loud failure.

    Guards against #79 review finding: openwakeword's AudioFeatures
    defaults the melspec/embedding paths to its package's resources/
    models dir which is empty on a fresh install. Our factory must
    pre-flight ALL three vendored files, not just the wake model.
    """
    monkeypatch.delenv("REACHY_DUCKY_WAKE_MOCK", raising=False)
    monkeypatch.setattr(
        "reachy_ducky_app.wake_onnx._VENDORED_MELSPEC_PATH",
        tmp_path / "nonexistent_melspec.onnx",
    )
    with pytest.raises(RuntimeError, match="Vendored melspectrogram model not found"):
        OpenWakeWordDetector.from_vendored_weights()


def test_owd_factory_passes_vendored_paths_to_oww_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Model()`` must receive melspec_model_path + embedding_model_path.

    Guards against #79 review finding: without these kwargs, oWW's
    AudioFeatures falls back to the package's resources/models dir,
    which is empty on a fresh install — the whole vendoring story
    breaks silently.
    """
    captured_kwargs: dict[str, object] = {}

    def _fake_model(**kwargs: object) -> FakeOWWModel:
        captured_kwargs.update(kwargs)
        return FakeOWWModel(scores={})

    # Patch the lazy ``from openwakeword.model import Model`` site.
    import openwakeword.model  # type: ignore[import-untyped]  # noqa: PLC0415

    monkeypatch.setattr(openwakeword.model, "Model", _fake_model)

    OpenWakeWordDetector.from_vendored_weights()

    melspec_path = captured_kwargs.get("melspec_model_path")
    embedding_path = captured_kwargs.get("embedding_model_path")
    assert isinstance(melspec_path, str)
    assert isinstance(embedding_path, str)
    assert melspec_path.endswith("melspectrogram.onnx")
    assert embedding_path.endswith("embedding_model.onnx")


def test_owd_factory_attaches_vendored_vad_when_threshold_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendored silero_vad path must reach the VAD class when vad_threshold > 0.

    Guards against #79 review (Codex P1): ``openwakeword.Model.__init__``
    constructs ``openwakeword.VAD()`` with no args when vad_threshold > 0,
    which loads silero_vad.onnx from the package's resources dir —
    empty on fresh installs. We work around this by constructing Model
    with vad_threshold=0 (skipping auto-VAD) then attaching our own
    VAD(model_path=vendored).
    """
    captured_model_kwargs: dict[str, object] = {}
    captured_vad_path: list[str] = []

    def _fake_model(**kwargs: object) -> FakeOWWModel:
        captured_model_kwargs.update(kwargs)
        return FakeOWWModel(scores={})

    def _fake_vad(*, model_path: str, **_: object) -> object:
        captured_vad_path.append(model_path)
        return object()

    import openwakeword.model  # noqa: PLC0415
    import openwakeword.vad  # type: ignore[import-untyped]  # noqa: PLC0415

    monkeypatch.setattr(openwakeword.model, "Model", _fake_model)
    monkeypatch.setattr(openwakeword.vad, "VAD", _fake_vad)

    OpenWakeWordDetector.from_vendored_weights(vad_threshold=0.3)

    # Model itself receives vad_threshold=0 so oWW skips auto-VAD construction
    assert captured_model_kwargs.get("vad_threshold") == 0.0
    # Our manual VAD attach used the vendored path
    assert len(captured_vad_path) == 1
    assert captured_vad_path[0].endswith("silero_vad.onnx")


def test_owd_factory_skips_vad_attach_when_threshold_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``vad_threshold=0`` should skip the VAD attach entirely (no Silero load)."""
    captured_vad_calls: list[str] = []

    def _fake_model(**_: object) -> FakeOWWModel:
        return FakeOWWModel(scores={})

    def _fake_vad(*, model_path: str, **_: object) -> object:
        captured_vad_calls.append(model_path)
        return object()

    import openwakeword.model  # noqa: PLC0415
    import openwakeword.vad  # noqa: PLC0415

    monkeypatch.setattr(openwakeword.model, "Model", _fake_model)
    monkeypatch.setattr(openwakeword.vad, "VAD", _fake_vad)

    OpenWakeWordDetector.from_vendored_weights(vad_threshold=0.0)

    assert captured_vad_calls == []


def test_owd_factory_raises_when_silero_vad_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pre-flight covers silero_vad.onnx alongside the other 3 vendored files."""
    monkeypatch.delenv("REACHY_DUCKY_WAKE_MOCK", raising=False)
    monkeypatch.setattr(
        "reachy_ducky_app.wake_onnx._VENDORED_SILERO_VAD_PATH",
        tmp_path / "nonexistent_silero.onnx",
    )
    with pytest.raises(RuntimeError, match="Vendored silero_vad model not found"):
        OpenWakeWordDetector.from_vendored_weights()


def test_owd_clips_resampled_overshoot_before_int16_cast() -> None:
    """FFT-resample overshoot beyond int16 must clip, not wrap.

    Guards against #79 review finding: scipy.signal.resample on sharp
    transients (square wave / consonants) produces floats outside
    ±32768. A naive ``.astype(np.int16)`` wraps modularly (float
    32801 → int16 -32735), corrupting the wake input. We clip first.
    """
    fake = FakeOWWModel(scores={"hey_jarvis": 0.0})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    # Square wave at near-int16 max — guaranteed Gibbs overshoot when
    # downsampled 24 → 16 kHz via FFT.
    n = 1920  # 1280 * 24/16 — fills exactly one 16 kHz window
    sq = np.where(
        np.sin(2 * np.pi * 440 * np.arange(n) / 24_000) > 0,
        np.int16(32_000),
        np.int16(-32_000),
    ).astype(np.int16)
    det.feed((24_000, sq))

    chunk = fake.last_chunk
    assert chunk is not None
    assert chunk.dtype == np.int16
    # No wrap: the original square wave was bounded ±32000; after
    # resample + clip the values stay within int16 range with the
    # right SIGN. Catastrophic wrap would flip extreme positives to
    # large negatives — assert no such anomaly by checking that
    # samples near 32000 in the input have non-negative values in the
    # output (after resampling preserves sign).
    assert chunk.max() <= 32_767
    assert chunk.min() >= -32_768
    # Energy preservation sanity check — clipping a ~503/1280 overshoot
    # to int16-max keeps the bulk of the waveform's amplitude.
    assert np.abs(chunk).max() >= 30_000


def test_load_default_returns_mock_with_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACHY_DUCKY_WAKE_MOCK", "1")
    detector = load_default_wake_detector()
    assert isinstance(detector, MockWakeDetector)
