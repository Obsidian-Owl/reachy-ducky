"""Single-turn conversation orchestration.

Composes the Reachy-side pieces into one flow::

    wake_trigger -> embodiment=LISTENING
                 -> voice.start_turn()  (awaits final user transcript)
                 -> embodiment=THINKING
                 -> daemon.brain_query(text)
                 -> embodiment=LISTENING (speak reply; robot looks at
                    the user as Ducky replies, not while thinking)
                 -> voice.speak_text(reply)
                 -> embodiment=IDLE

Muted state is honoured at entry: if the state machine is in
:data:`~reachy_ducky_protocol.messages.State.MUTED`, :func:`run_one_turn`
returns immediately without calling into voice or the daemon. This is
the embodiment-level mute (visible "off" posture). The separate
:class:`~reachy_ducky_app.mute.MuteGate` is the *audio-path* gate —
different concern, not conflated here.

The loop is intentionally one-turn, one-function. A future task wraps
it in a wake-word listen loop that calls :func:`run_one_turn`
repeatedly. Interrupt-while-speaking (barge-in mid-reply) is out of
scope for Phase A.
"""

from __future__ import annotations

from reachy_ducky_protocol.messages import State

from .daemon_client import DaemonClient
from .embodiment.state_machine import EmbodimentStateMachine
from .voice.interface import VoiceInterface


async def run_one_turn(
    *,
    voice: VoiceInterface,
    sm: EmbodimentStateMachine,
    daemon: DaemonClient,
    project_slug: str | None = None,
) -> None:
    """Run one user-Ducky turn. No-op if the state machine is MUTED."""
    if sm.state == State.MUTED:
        return

    sm.transition(State.LISTENING)
    turn = await voice.start_turn()
    user_text = await turn.get_user_text()

    sm.transition(State.THINKING)
    reply = await daemon.brain_query(user_text, project_slug=project_slug)

    # Speak while in LISTENING posture — the robot looks at the user
    # as Ducky replies, not while thinking.
    sm.transition(State.LISTENING)
    await turn.speak_text(reply.text)

    sm.transition(State.IDLE)
