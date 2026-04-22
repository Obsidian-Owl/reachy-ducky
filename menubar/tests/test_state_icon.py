from __future__ import annotations

import pytest
from reachy_ducky_menubar.state_icon import icon_for
from reachy_ducky_protocol.messages import State


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (State.IDLE, "🦆"),
        (State.LISTENING, "🦆👂"),
        (State.THINKING, "🦆💭"),
        (State.MUTED, "🦆🔇"),
    ],
    ids=[s.value for s in (State.IDLE, State.LISTENING, State.THINKING, State.MUTED)],
)
def test_icon_for_each_state(state: State, expected: str) -> None:
    """Every State value has a deterministic icon."""
    assert icon_for(state) == expected


def test_icon_for_covers_every_state() -> None:
    """The icon map is exhaustive — adding a new State without an icon fails fast."""
    for state in State:
        # Should not raise KeyError for any member.
        icon_for(state)
