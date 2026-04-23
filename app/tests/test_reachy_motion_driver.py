"""Unit tests for ``ReachyMotionDriver`` — the concrete SDK-backed driver.

Tests inject fakes into ``_emotions_library`` / ``_dances_library`` to
exercise the resolver logic without downloading HuggingFace datasets.
Hardware-tier verification that the real libraries return the real move
names (``neutral``, ``listening``, ``thinking``) is tracked in the
hardware-testing plan (#23 / #20); out of scope here.
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
    # Inject emotions; dances stays lazy (None).
    driver._emotions_library = _FakeMoveLibrary({"listening": listening_move})  # noqa: SLF001

    driver.play_move("listening")

    mini.play_move.assert_called_once_with(listening_move)


def test_play_move_searches_libraries_in_order() -> None:
    """Emotions tried first; dances only on emotions miss. Earlier wins on
    name collision."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    emotions_move = _FakeMove()
    dances_move = _FakeMove()
    pirouette_move = _FakeMove()
    driver._emotions_library = _FakeMoveLibrary({"neutral": emotions_move})  # noqa: SLF001
    driver._dances_library = _FakeMoveLibrary(  # noqa: SLF001
        {"neutral": dances_move, "pirouette": pirouette_move}
    )

    # Found in emotions — earlier wins on collision.
    driver.play_move("neutral")
    mini.play_move.assert_called_once_with(emotions_move)

    mini.reset_mock()
    # Missing from emotions, found in dances.
    driver.play_move("pirouette")
    mini.play_move.assert_called_once_with(pirouette_move)


def test_play_move_raises_when_name_missing_from_all_libraries() -> None:
    """Unknown name → ValueError with a clear message naming the missing
    move. SDK's ``play_move`` is NOT called."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    driver._emotions_library = _FakeMoveLibrary({"neutral": _FakeMove()})  # noqa: SLF001
    driver._dances_library = _FakeMoveLibrary({"wave": _FakeMove()})  # noqa: SLF001

    with pytest.raises(ValueError, match="Move 'sprint' not found"):
        driver.play_move("sprint")

    mini.play_move.assert_not_called()


def test_libraries_are_lazy_init() -> None:
    """``__init__`` does NOT load HuggingFace datasets — each library is
    constructed on its own first access. Unit tests that never play a
    move pay zero SDK / HF cost."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    # Fresh driver: both libraries are None (not yet loaded).
    assert driver._emotions_library is None  # noqa: SLF001
    assert driver._dances_library is None  # noqa: SLF001


def test_play_move_reuses_loaded_libraries_across_calls() -> None:
    """Second ``play_move`` call reuses the library loaded on the first
    — no re-import, no re-construction. Catches regressions where a
    refactor accidentally resets the library attr per call."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    lib = _FakeMoveLibrary({"a": _FakeMove(), "b": _FakeMove()})
    driver._emotions_library = lib  # noqa: SLF001 — test injection seam

    driver.play_move("a")
    lib_after_first = driver._emotions_library  # noqa: SLF001

    driver.play_move("b")
    assert driver._emotions_library is lib_after_first  # noqa: SLF001


def test_go_to_sleep_and_wake_up_bypass_move_resolution() -> None:
    """``goto_sleep`` and ``wake_up`` forward directly to the SDK — they are
    NOT move-library lookups. Library attrs stay lazy (both None)."""
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)

    driver.go_to_sleep()
    mini.goto_sleep.assert_called_once()

    driver.wake_up()
    mini.wake_up.assert_called_once()

    # Move libraries untouched — no HF download triggered.
    assert driver._emotions_library is None  # noqa: SLF001
    assert driver._dances_library is None  # noqa: SLF001


def test_emotions_hit_does_not_load_dances() -> None:
    """A successful emotions lookup must not touch the dances library.

    This is the partial-cache resilience property (Codex PR #67): users
    with emotions cached but dances unavailable (no network + cache
    miss) must still be able to play emotions moves. Verifies dances
    library construction is NOT triggered when emotions has the move.
    """
    mini = MagicMock()
    driver = ReachyMotionDriver(mini)
    emotions_move = _FakeMove()
    driver._emotions_library = _FakeMoveLibrary({"neutral": emotions_move})  # noqa: SLF001
    # _dances_library intentionally left None — mimicking dances unavailable.

    driver.play_move("neutral")

    mini.play_move.assert_called_once_with(emotions_move)
    # Dances library was never touched.
    assert driver._dances_library is None  # noqa: SLF001
