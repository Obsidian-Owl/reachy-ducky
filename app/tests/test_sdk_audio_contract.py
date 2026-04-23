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
import logging
import time

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

    For ``get_audio_sample`` we pin "callable with no args" rather than
    "exactly one parameter" — the real drift concern is the SDK growing
    a required argument we don't supply, not adding a keyword arg with
    a default (which keeps our call site working).
    """
    assert hasattr(MediaManager, "get_audio_sample"), (
        "MediaManager.get_audio_sample missing — ReachyMicSource can't source frames"
    )
    assert hasattr(MediaManager, "push_audio_sample"), (
        "MediaManager.push_audio_sample missing — ReachySpeakerSink can't sink frames"
    )
    sig = inspect.signature(MediaManager.get_audio_sample)
    params = list(sig.parameters.values())
    assert params and params[0].name == "self", (
        f"MediaManager.get_audio_sample signature unexpected: {sig}"
    )
    non_self_required = [
        p
        for p in params[1:]
        if p.default is inspect.Parameter.empty
        and p.kind
        not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
    ]
    assert not non_self_required, (
        f"get_audio_sample grew a required parameter we don't supply: "
        f"{[p.name for p in non_self_required]}"
    )


@pytest.mark.hardware
def test_get_audio_sample_returns_non_empty_buffer() -> None:
    """``get_audio_sample`` eventually yields a non-empty float32 buffer.

    The SDK documents ``Optional[NDArray[np.float32]]`` — ``None`` is a
    legitimate "GStreamer ring buffer has no data yet" response,
    especially on a fresh ``ReachyMini()`` where audio hasn't started
    flowing. Polls up to 2s so this pins "the method eventually yields"
    rather than "the method returns non-None on the first call"
    (which would race).
    """
    mini = ReachyMini()
    deadline = time.monotonic() + 2.0
    sample: np.ndarray | None = None
    while time.monotonic() < deadline:
        sample = mini.media.get_audio_sample()
        if sample is not None and sample.size > 0:
            break
    assert sample is not None, "get_audio_sample kept returning None for 2s — mic not primed?"
    assert sample.size > 0, f"get_audio_sample yielded empty array ({sample!r})"
    assert sample.dtype == np.float32, f"expected float32 per SDK contract, got {sample.dtype}"


@pytest.mark.hardware
def test_push_audio_sample_accepts_silent_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``push_audio_sample`` accepts a well-formed silent float32 frame.

    The SDK's MediaManager.push_audio_sample takes ``npt.NDArray[np.float32]``
    per the installed ``reachy_mini.media.media_manager`` source. The
    ``MicSource`` / ``SpeakerSink`` ABC contract stays PCM16-bytes (matches
    OpenAI Realtime API downstream); Tasks 1.2 / 1.3 handle
    float32↔PCM16 conversion at the Reachy adapter boundary.

    We assert not only that the call doesn't raise but also that it
    emits no warning — the SDK silently logs a warning and returns on
    wrong-shape input (media_manager.py:343-347), so a no-op silently
    passing "must not raise" would be the accomplishment-simulator
    anti-pattern ``testing-standards.md`` warns against.
    """
    mini = ReachyMini()
    # 40ms @ 24 kHz mono float32 = 960 samples.
    silent = np.zeros(960, dtype=np.float32)
    with caplog.at_level(logging.WARNING, logger="reachy_mini.media.media_manager"):
        mini.media.push_audio_sample(silent)
    assert not caplog.records, (
        f"push_audio_sample logged warnings (data dropped silently): "
        f"{[r.getMessage() for r in caplog.records]}"
    )
