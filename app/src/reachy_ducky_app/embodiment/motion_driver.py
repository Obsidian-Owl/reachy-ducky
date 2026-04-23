"""Pluggable motion backend for the Reachy-side app.

``MotionDriver`` is the ABC the :class:`EmbodimentStateMachine` talks to.
``MockMotionDriver`` records calls for unit tests. ``ReachyMotionDriver``
wraps a real ``reachy_mini.ReachyMini`` instance. ``reachy_mini`` is a
plain base dep (see ``app/pyproject.toml``), so the driver is importable
everywhere; the ``reachy_mini: object`` parameter shape is duck-typed so
unit tests can pass a ``MockReachyMini`` without depending on the live SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MotionDriver(ABC):
    """Pluggable motion backend.

    ``ReachyMotionDriver`` wraps the real SDK; ``MockMotionDriver`` records
    calls for side-effect verification in unit tests.
    """

    @abstractmethod
    def play_move(self, name: str) -> None:
        """Play a named move from the emotion library."""

    @abstractmethod
    def go_to_sleep(self) -> None:
        """Drive the robot into the visible "off" posture."""

    @abstractmethod
    def wake_up(self) -> None:
        """Bring the robot out of the "off" posture."""

    @abstractmethod
    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
        """Gaze at image-space coordinates (pixels)."""


class MockMotionDriver(MotionDriver):
    """Records calls for side-effect verification."""

    def __init__(self) -> None:
        self.moves: list[str] = []
        self.went_to_sleep: bool = False
        self.woke_up: bool = False
        self.gazes: list[tuple[float, float]] = []

    def play_move(self, name: str) -> None:
        self.moves.append(name)

    def go_to_sleep(self) -> None:
        self.went_to_sleep = True

    def wake_up(self) -> None:
        self.woke_up = True

    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
        del duration  # recorded via (u, v) only
        self.gazes.append((u, v))


class ReachyMotionDriver(MotionDriver):
    """Real driver. Hardware-only.

    ``reachy_mini`` is a plain base dep on ``app/pyproject.toml`` —
    installs cross-platform, including Mac dev machines (the upstream
    ``gstreamer-msvc-runtime`` platform-marker bug is patched at the
    workspace root via ``[tool.uv] dependency-metadata``). The caller
    constructs a ``ReachyMini`` and passes it in.

    Named moves (``"neutral"``, ``"listening"``, etc.) are resolved to
    SDK ``Move`` objects via ``RecordedMoves`` — the SDK ships two
    default HuggingFace datasets (emotions + dances libraries). Libraries
    are lazy-loaded on the first ``play_move`` call so tests that only
    exercise ``go_to_sleep`` / ``wake_up`` don't trigger HF downloads.
    Emotions library is searched first; same-name collisions resolve to
    the emotions version (important because the state-machine's move
    names — ``neutral``, ``listening``, ``thinking`` — come from the
    emotions library).
    """

    def __init__(self, mini: object) -> None:
        # Typed as object — the concrete type comes from reachy_mini which
        # may not be installed on a dev machine. Callers pass a ReachyMini.
        self._mini = mini
        # Lazy: resolved on first play_move() call via _get_move. Tests
        # inject a fake list here; production code triggers HF download
        # on first access (cached to disk for subsequent sessions).
        self._move_libraries: list[object] | None = None

    def _get_move(self, name: str) -> object:
        """Resolve ``name`` to an SDK ``Move`` via RecordedMoves libraries.

        Searches emotions library first, falls through to dances library.
        Raises ``ValueError`` with a clear message if the name is in
        neither — the SDK's own ``play_move`` would fail less helpfully.

        The ``except ValueError`` narrows on miss by-contract with the
        current SDK — ``RecordedMoves.get()`` raises ``ValueError`` only
        when the name isn't in the dataset. Revisit this catch when
        bumping ``reachy-mini`` (tracked in #60) if a future version
        adds input validation that also raises ``ValueError``.

        Dataset loading failures (HF cache corruption, network errors
        in ``snapshot_download``) propagate from the FIRST
        ``RecordedMoves(...)`` call and are NOT caught — those are
        single-root-cause environment problems, not per-dataset
        conditions. Silently falling through would hide the real issue.
        """
        if self._move_libraries is None:
            from reachy_mini.motion.recorded_move import (  # type: ignore[import-untyped]
                RecordedMoves,
            )

            self._move_libraries = [
                RecordedMoves("pollen-robotics/reachy-mini-emotions-library"),
                RecordedMoves("pollen-robotics/reachy-mini-dances-library"),
            ]
        for lib in self._move_libraries:
            try:
                return lib.get(name)  # type: ignore[attr-defined]
            except ValueError:
                continue
        raise ValueError(
            f"Move '{name}' not found in emotions library "
            f"(pollen-robotics/reachy-mini-emotions-library) or dances "
            f"library (pollen-robotics/reachy-mini-dances-library)"
        )

    def play_move(self, name: str) -> None:
        move = self._get_move(name)
        self._mini.play_move(move)  # type: ignore[attr-defined]

    def go_to_sleep(self) -> None:
        self._mini.goto_sleep()  # type: ignore[attr-defined]

    def wake_up(self) -> None:
        self._mini.wake_up()  # type: ignore[attr-defined]

    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
        self._mini.look_at_image(u, v, duration=duration)  # type: ignore[attr-defined]
