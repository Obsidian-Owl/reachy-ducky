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
    """

    def __init__(self, mini: object) -> None:
        # Typed as object — the concrete type comes from reachy_mini which
        # may not be installed on a dev machine. Callers pass a ReachyMini.
        self._mini = mini

    def play_move(self, name: str) -> None:
        self._mini.play_move(name)  # type: ignore[attr-defined]

    def go_to_sleep(self) -> None:
        self._mini.goto_sleep()  # type: ignore[attr-defined]

    def wake_up(self) -> None:
        self._mini.wake_up()  # type: ignore[attr-defined]

    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
        self._mini.look_at_image(u, v, duration=duration)  # type: ignore[attr-defined]
