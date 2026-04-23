"""Tests for the wake-word detector interface, mock, and factory."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest
from reachy_ducky_app.wake import (
    MockWakeDetector,
    WakeDetector,
    load_default_wake_detector,
)


def test_wake_detector_is_abstract() -> None:
    """WakeDetector cannot be instantiated directly — it is an ABC."""
    with pytest.raises(TypeError):
        WakeDetector()  # type: ignore[abstract]


def test_mock_detector_feed_audio_returns_false_for_any_chunk() -> None:
    """MockWakeDetector.feed_audio ignores audio and returns False by design."""
    det = MockWakeDetector()
    assert det.feed_audio(np.zeros(16, dtype=np.int16)) is False


def test_wake_detector_event_is_asyncio_event() -> None:
    """WakeDetector.event is a real asyncio.Event — not an mp or threading analog."""
    detector = MockWakeDetector()
    assert isinstance(detector.event, asyncio.Event)
    assert not detector.event.is_set()


def test_mock_wake_detector_trigger_on_feed_sets_event() -> None:
    """Mock with trigger_on_feed=True sets the event the first time feed_audio is called."""
    detector = MockWakeDetector(trigger_on_feed=True)
    assert not detector.event.is_set()
    detector.feed_audio(np.zeros(16, dtype=np.int16))
    assert detector.event.is_set()


def test_mock_wake_detector_default_does_not_set_event() -> None:
    """Default MockWakeDetector preserves today's silent-mock semantics."""
    detector = MockWakeDetector()
    detector.feed_audio(np.zeros(16, dtype=np.int16))
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


def test_load_default_returns_mock_for_now() -> None:
    """Phase A lock-in: the default factory returns the mock detector."""
    det = load_default_wake_detector()
    assert isinstance(det, MockWakeDetector)
