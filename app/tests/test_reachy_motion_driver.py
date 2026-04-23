"""Unit tests for ``ReachyMotionDriver`` — the concrete SDK-backed driver.

Tests inject a fake ``_move_libraries`` to exercise the resolver logic
without downloading HuggingFace datasets. Hardware-tier verification
that the real libraries return the real move names (``neutral``,
``listening``, ``thinking``) is tracked in the hardware-testing plan
(#23 / #20); out of scope here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from reachy_ducky_app.embodiment.motion_driver import ReachyMotionDriver


class _FakeMove:
    """Opaque Move-compatible stand-in (doesn't need to quack as ``Move`` —
    we only verify it is what gets forwarded to the SDK's ``play_move``)."""


class _FakeMoveLibrary:
    """In-memory library stand-in: maps name→Move; raises ``ValueError`` like
    the real ``RecordedMoves.get`` when the name is missing."""

    def __init__(self, moves: dict[str, _FakeMove]) -> None:
        self._moves = moves

    def get(self, name: str) -> _FakeMove:
        if name not in self._moves:
            raise ValueError(f"not found: {name}")
        return self._moves[name]


def test_play_move_forwards_resolved_move_to_sdk() -> None:
    """play_move("listening") resolves from libraries and forwards the Move
    (not the string) to the SDK's play_move."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    listening_move = _FakeMove()
    driver._move_libraries = [  # noqa: SLF001 — test injection seam
        _FakeMoveLibrary({"listening": listening_move}),
    ]

    driver.play_move("listening")

    mini.play_move.assert_called_once_with(listening_move)


def test_play_move_searches_libraries_in_order() -> None:
    """Missing from library 1 → falls through to library 2; earlier wins on
    name collision."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    emotions_move = _FakeMove()
    dances_move = _FakeMove()
    pirouette_move = _FakeMove()
    driver._move_libraries = [  # noqa: SLF001
        _FakeMoveLibrary({"neutral": emotions_move}),
        _FakeMoveLibrary({"neutral": dances_move, "pirouette": pirouette_move}),
    ]

    # Found in the first library — earlier wins on collision.
    driver.play_move("neutral")
    mini.play_move.assert_called_once_with(emotions_move)

    mini.reset_mock()
    # Missing from first, found in second.
    driver.play_move("pirouette")
    mini.play_move.assert_called_once_with(pirouette_move)


def test_play_move_raises_when_name_missing_from_all_libraries() -> None:
    """Unknown name → ValueError with a clear message naming the missing
    move. SDK's ``play_move`` is NOT called."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    driver._move_libraries = [  # noqa: SLF001
        _FakeMoveLibrary({"neutral": _FakeMove()}),
        _FakeMoveLibrary({"wave": _FakeMove()}),
    ]

    with pytest.raises(ValueError, match="Move 'sprint' not found"):
        driver.play_move("sprint")

    mini.play_move.assert_not_called()


def test_move_libraries_are_lazy_loaded_on_first_play_move() -> None:
    """``__init__`` does NOT load HuggingFace datasets — that happens on the
    first ``play_move`` call. Unit tests that never play a move pay zero
    SDK / HF cost."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    # Fresh driver: move_libraries is None (not yet loaded).
    assert driver._move_libraries is None  # noqa: SLF001


def test_go_to_sleep_and_wake_up_bypass_move_resolution() -> None:
    """``goto_sleep`` and ``wake_up`` forward directly to the SDK — they are
    NOT move-library lookups. Touching _move_libraries stays lazy."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)

    driver.go_to_sleep()
    mini.goto_sleep.assert_called_once()

    driver.wake_up()
    mini.wake_up.assert_called_once()

    # Move libraries untouched — no HF download triggered.
    assert driver._move_libraries is None  # noqa: SLF001
