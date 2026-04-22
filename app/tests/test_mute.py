"""Tests for the hard mic-gate (MuteGate)."""

from __future__ import annotations

import numpy as np
from reachy_ducky_app.mute import MuteGate


def test_gate_default_unmuted() -> None:
    """A freshly-constructed MuteGate starts unmuted."""
    gate = MuteGate()
    assert gate.muted is False


def test_gate_passes_audio_when_unmuted() -> None:
    """When unmuted, process() returns the chunk unchanged (same values)."""
    gate = MuteGate()
    chunk = np.array([1, 2, 3, -4, -5], dtype=np.int16)
    out = gate.process(chunk)
    assert np.array_equal(out, chunk)


def test_gate_zeros_audio_when_muted() -> None:
    """When muted, process() returns an all-zero array with the same shape+dtype."""
    gate = MuteGate()
    gate.set_muted(True)
    chunk = np.array([1, 2, 3, -4, -5], dtype=np.int16)
    out = gate.process(chunk)
    assert np.array_equal(out, np.zeros(5, dtype=np.int16))


def test_gate_toggle_flips_state() -> None:
    """toggle() flips the mute flag each call."""
    gate = MuteGate()
    gate.toggle()
    assert gate.muted is True
    gate.toggle()
    assert gate.muted is False


def test_gate_set_muted_true_then_false() -> None:
    """set_muted moves the gate between states deterministically."""
    gate = MuteGate()
    gate.set_muted(True)
    assert gate.muted is True
    gate.set_muted(False)
    assert gate.muted is False


def test_gate_preserves_dtype_and_shape() -> None:
    """process() output matches input in dtype and shape regardless of mute state."""
    chunk = np.arange(16, dtype=np.int16)

    gate = MuteGate()
    out_unmuted = gate.process(chunk)
    assert out_unmuted.dtype == np.int16
    assert out_unmuted.shape == chunk.shape

    gate.set_muted(True)
    out_muted = gate.process(chunk)
    assert out_muted.dtype == np.int16
    assert out_muted.shape == chunk.shape
