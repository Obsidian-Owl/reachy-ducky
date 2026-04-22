"""Pure mapping from conversational :class:`State` to a title-bar glyph.

Lives outside ``main.py`` so it has no ``rumps`` dependency and can be
tested on any platform.
"""

from __future__ import annotations

from reachy_ducky_protocol.messages import State

_MAP: dict[State, str] = {
    State.IDLE: "🦆",
    State.LISTENING: "🦆👂",
    State.THINKING: "🦆💭",
    State.MUTED: "🦆🔇",
}


def icon_for(state: State) -> str:
    """Return the title-bar glyph for a given conversational state."""
    return _MAP[state]
