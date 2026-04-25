"""Tests for the wake-word detector interface, mock, and factory."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from reachy_ducky_app.voice.audio_io import AudioFrame
from reachy_ducky_app.wake import (
    MockWakeDetector,
    WakeDetector,
)


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
