"""SDK contract — pin the ReachyMini audio surface used by #23 impls.

Class-level introspection runs in the default unit tier (``reachy-mini``
installs on every machine since PR #65). Instance-level live-robot
checks are gated on ``@pytest.mark.hardware`` and run locally via
``uv run pytest -m hardware`` against a LAN-reachable Reachy Mini —
they catch upstream audio-API renames / signature changes before they
hit real hardware.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest
from reachy_mini import ReachyMini  # type: ignore[import-untyped]
from reachy_mini.media.media_manager import MediaManager  # type: ignore[import-untyped]


def test_reachy_mini_media_property_exists() -> None:
    """``ReachyMini.media`` property is declared at class scope.

    Class-level only — ``media`` is a ``@property`` so the descriptor lives
    on the class even without instantiation. Proves the access path
    ``mini.media.<method>`` is still valid without needing a live robot.
    """
    assert hasattr(ReachyMini, "media"), (
        "ReachyMini.media property missing — can't source or sink frames"
    )


def test_media_manager_exposes_audio_sample_methods() -> None:
    """``MediaManager`` class declares the two audio methods our impls call.

    This is the critical default-tier drift guard: ``mini.media`` returns
    a ``MediaManager`` instance, so renames on ``MediaManager.get_audio_sample`` /
    ``push_audio_sample`` would silently break our ``ReachyMicSource`` /
    ``ReachySpeakerSink`` implementations. Introspecting the class directly
    (not an instance) keeps this in the default tier with no live-robot
    requirement — catches the rename in CI before it hits hardware.
    """
    assert hasattr(MediaManager, "get_audio_sample"), (
        "MediaManager.get_audio_sample missing — ReachyMicSource can't source frames"
    )
    assert hasattr(MediaManager, "push_audio_sample"), (
        "MediaManager.push_audio_sample missing — ReachySpeakerSink can't sink frames"
    )
    # Also pin the parameterless call shape of get_audio_sample — we
    # rely on calling it with no arguments. Signature check is class-
    # level safe (``inspect.signature`` doesn't need an instance).
    sig = inspect.signature(MediaManager.get_audio_sample)
    # self is the only parameter; no caller-supplied args.
    assert len(sig.parameters) == 1, (
        f"MediaManager.get_audio_sample unexpectedly accepts "
        f"{list(sig.parameters)}; ReachyMicSource calls it with no args"
    )


@pytest.mark.hardware
def test_get_audio_sample_returns_non_empty_buffer() -> None:
    """get_audio_sample returns a non-empty PCM buffer.

    Exact dtype/shape asserted in the refinement pass after Step 1
    introspection. Today we pin only ``truthy``/``non-empty``.
    """
    mini = ReachyMini()
    sample = mini.media.get_audio_sample()
    assert sample is not None, "get_audio_sample returned None"
    # bytes → len > 0; np.ndarray → size > 0
    length = len(sample) if hasattr(sample, "__len__") else sample.size
    assert length > 0, f"get_audio_sample returned empty buffer ({sample!r})"


@pytest.mark.hardware
def test_push_audio_sample_accepts_silent_frame() -> None:
    """push_audio_sample accepts a well-formed silent PCM frame.

    The SDK's MediaManager.push_audio_sample takes ``npt.NDArray[np.float32]``
    per the installed ``reachy_mini.media.media_manager`` source. A PCM16
    bytes payload would fail here — the ReachyMicSource/ReachySpeakerSink
    impls in Tasks 1.2/1.3 handle format conversion at the boundary so
    the MicSource/SpeakerSink ABC's PCM16-bytes contract (set by the
    OpenAI Realtime API downstream) stays intact.
    """
    mini = ReachyMini()
    # 40ms @ 24 kHz mono float32 = 960 samples. If sample rate or dtype
    # is different on real hardware, this test fails with a clear error
    # and Task 1.5's refinement pass will tighten the shape.
    silent = np.zeros(960, dtype=np.float32)
    mini.media.push_audio_sample(silent)  # must not raise


@pytest.mark.hardware
def test_get_audio_sample_signature_is_parameterless() -> None:
    """Signature is () → buffer; we rely on calling without arguments."""
    sig = inspect.signature(ReachyMini().media.get_audio_sample)
    assert len(sig.parameters) == 0, (
        f"get_audio_sample unexpectedly accepts {list(sig.parameters)}; "
        "ReachyMicSource calls it with no arguments"
    )
