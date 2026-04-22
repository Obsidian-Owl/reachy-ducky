"""Tests for the pure pick_primary_face helper and ``gaze_loop``'s fps guard.

``gaze_loop`` is hardware-tier (needs a real mini + mediapipe detector) and
is exercised via ``@pytest.mark.hardware`` on the robot; these unit tests
cover the pure selection helper and the entry-guard validation of ``fps``.
"""

from __future__ import annotations

import pytest
from reachy_ducky_app.embodiment.gaze import gaze_loop, pick_primary_face
from reachy_ducky_app.embodiment.motion_driver import MockMotionDriver


def test_pick_primary_face_returns_none_for_empty_list() -> None:
    """No detections -> None (nothing to gaze at)."""
    assert pick_primary_face([]) is None


def test_pick_primary_face_single_detection_returns_it() -> None:
    """A single detection's (u, v) is returned verbatim."""
    assert pick_primary_face([(0.5, 0.5, 0.9)]) == (0.5, 0.5)


def test_pick_primary_face_picks_highest_confidence() -> None:
    """Among several detections the top-score one wins regardless of position."""
    detections: list[tuple[float, float, float]] = [
        (0.10, 0.20, 0.55),
        (0.42, 0.60, 0.98),  # highest score, middle-positioned
        (0.80, 0.80, 0.70),
    ]
    assert pick_primary_face(detections) == (0.42, 0.60)


def test_pick_primary_face_ties_broken_by_list_order() -> None:
    """Equal confidences: the first detection wins (documented + locked in)."""
    detections: list[tuple[float, float, float]] = [
        (0.30, 0.30, 0.80),
        (0.70, 0.70, 0.80),
    ]
    assert pick_primary_face(detections) == (0.30, 0.30)


def test_pick_primary_face_ignores_position_only_score_matters() -> None:
    """A far-off-center face with higher confidence beats a centered weaker one."""
    detections: list[tuple[float, float, float]] = [
        (0.50, 0.50, 0.40),  # centered but low score
        (0.05, 0.05, 0.85),  # far corner, high score
    ]
    assert pick_primary_face(detections) == (0.05, 0.05)


def test_pick_primary_face_accepts_boundary_values() -> None:
    """Detections at the image corners (0.0 and 1.0) are returned unchanged."""
    detections: list[tuple[float, float, float]] = [
        (0.0, 0.0, 0.5),
        (1.0, 1.0, 0.5),
    ]
    # Tie on score -> first wins.
    assert pick_primary_face(detections) == (0.0, 0.0)

    # Bump the second's confidence: it wins, and the corner coords round-trip.
    detections_with_winner: list[tuple[float, float, float]] = [
        (0.0, 0.0, 0.5),
        (1.0, 1.0, 0.9),
    ]
    assert pick_primary_face(detections_with_winner) == (1.0, 1.0)


@pytest.mark.asyncio
async def test_gaze_loop_rejects_zero_fps() -> None:
    """``fps=0`` must raise before any work (previously ZeroDivisionError)."""
    driver = MockMotionDriver()
    with pytest.raises(ValueError, match="must be positive"):
        await gaze_loop(None, driver, fps=0)


@pytest.mark.asyncio
async def test_gaze_loop_rejects_negative_fps() -> None:
    """Negative ``fps`` must raise (previously: tight loop, no error)."""
    driver = MockMotionDriver()
    with pytest.raises(ValueError, match="must be positive"):
        await gaze_loop(None, driver, fps=-1)


@pytest.mark.asyncio
async def test_gaze_loop_guard_accepts_small_positive_fps() -> None:
    """Positive ``fps`` is not rejected by the guard.

    ``mini=None`` means the loop eventually fails with ``AttributeError``
    when it hits ``mini.media.get_frame()`` — that's fine. The guard's
    job is to *not* reject positive floats with the "must be positive"
    ValueError; any downstream exception proves the guard let us through.
    """
    driver = MockMotionDriver()
    with pytest.raises(Exception) as exc_info:
        await gaze_loop(None, driver, fps=0.1)
    # The guard must not be the thing that raised here.
    assert not (
        isinstance(exc_info.value, ValueError) and "must be positive" in str(exc_info.value)
    )
