"""Tests for the pure pick_primary_face helper.

``gaze_loop`` is hardware-tier (needs a real mini + mediapipe detector) and
is exercised via ``@pytest.mark.hardware`` on the robot; these unit tests
only cover the pure selection helper.
"""

from __future__ import annotations

from reachy_ducky_app.embodiment.gaze import pick_primary_face


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
