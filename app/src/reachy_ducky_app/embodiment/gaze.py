"""Face-tracking gaze: pure selection helper + hardware-only live loop.

:func:`pick_primary_face` is pure Python and unit-testable. :func:`gaze_loop`
runs a mediapipe face detector against ``mini.media`` frames and drives
:meth:`MotionDriver.look_at_image`; it needs real hardware + a working
mediapipe install and is hardware-tier only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .motion_driver import MotionDriver


def pick_primary_face(
    detections: list[tuple[float, float, float]],
) -> tuple[float, float] | None:
    """Pick the highest-confidence face center.

    Args:
        detections: list of ``(u, v, confidence)`` tuples where ``u`` and
            ``v`` are image-normalised in ``[0, 1]``.

    Returns:
        ``(u, v)`` of the top-confidence detection, or ``None`` if
        ``detections`` is empty.

    Ties are broken by list order (``max`` returns the first maximum).
    Callers feeding mediapipe output should pre-filter by a confidence
    threshold.
    """
    if not detections:
        return None
    best = max(detections, key=lambda d: d[2])
    return (best[0], best[1])


async def gaze_loop(mini: object, driver: MotionDriver, *, fps: float = 5.0) -> None:
    """Hardware-only face-tracking loop.

    Captures frames from ``mini.media``, runs mediapipe face detection, and
    feeds the primary face to ``driver.look_at_image``. Runs until cancelled.

    Cancellation model: caller wraps in ``asyncio.create_task(...)`` and
    cancels the task for shutdown. ``try``/``finally`` closes the detector.

    ``mediapipe`` is imported lazily so this module stays importable on dev
    machines where the runtime detector may not yet be exercised.

    Raises:
        ValueError: if ``fps <= 0``. Guarding at entry gives hardware-tier
            callers a clear error instead of a downstream
            :class:`ZeroDivisionError` (``fps=0``) or a silent tight loop
            (``fps<0``).
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")

    import asyncio

    import mediapipe as mp

    detector = mp.solutions.face_detection.FaceDetection(min_detection_confidence=0.5)
    period = 1.0 / fps
    try:
        while True:
            frame = mini.media.get_frame()  # type: ignore[attr-defined]
            if frame is not None:
                results = detector.process(frame)
                dets: list[tuple[float, float, float]] = []
                for d in results.detections or []:
                    bb = d.location_data.relative_bounding_box
                    # mediapipe relative bboxes can extend outside [0, 1] for
                    # partial faces at the frame edge; clamp so the robot
                    # never tries to aim outside its motion envelope.
                    u = min(1.0, max(0.0, bb.xmin + bb.width / 2))
                    v = min(1.0, max(0.0, bb.ymin + bb.height / 2))
                    score = d.score[0] if d.score else 0.0
                    dets.append((u, v, score))
                primary = pick_primary_face(dets)
                if primary is not None:
                    h, w = frame.shape[:2]
                    driver.look_at_image(primary[0] * w, primary[1] * h)
            await asyncio.sleep(period)
    finally:
        detector.close()
