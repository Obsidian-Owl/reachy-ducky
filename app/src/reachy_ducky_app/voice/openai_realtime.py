"""OpenAI Realtime voice implementation.

Wraps the OpenAI Python SDK's realtime WebSocket session behind our
:class:`~reachy_ducky_app.voice.interface.VoiceInterface`. The SDK handles
STT + TTS + turn-taking + barge-in at the protocol level; our job is to
plumb the events through our narrow :class:`VoiceTurn` contract.

**Not run in unit tests.** The stateful session methods require a live
connection to OpenAI. Unit coverage is construction-shape only (API-key
plumbing, model plumbing, export surface). One ``@pytest.mark.integration``
smoke (gated by ``REACHY_DUCKY_RUN_INTEGRATION=1`` + ``OPENAI_API_KEY``)
opens a real WebSocket session to validate the plumbing.

Verified SDK shape (``openai==1.109.1``)
-----------------------------------------
The installed SDK diverges from the Phase A plan sketch in four places,
so this module adapts:

1. **Session open.** The plan sketch calls
   ``client.beta.realtime.sessions.create(model=...)``. In 1.109.1 that is
   a *REST* call returning a ``SessionCreateResponse`` carrying an ephemeral
   client-secret for browser/WebRTC flows. It does **not** open a live
   session. The correct path is ``client.realtime.connect(model=...)``,
   which returns an ``AsyncRealtimeConnectionManager`` (async context
   manager). Entering it yields an ``AsyncRealtimeConnection`` — the live
   WebSocket session with ``.response``, ``.session``, ``.input_audio_buffer``,
   ``.conversation``, ``.output_audio_buffer`` namespace resources and
   ``.send / .recv / .close`` primitives.

2. **Event iteration.** The plan sketch iterates ``self._session.events()``.
   The installed SDK's ``AsyncRealtimeConnection`` is itself async-iterable
   (``async for event in conn``) and also exposes ``await conn.recv()``.
   We iterate the connection directly.

3. **Final user transcript.** Final transcript arrives as a
   ``ConversationItemInputAudioTranscriptionCompletedEvent`` whose
   ``type`` literal is ``"conversation.item.input_audio_transcription.completed"``
   and whose ``.transcript`` attribute carries the text.

4. **Response create/cancel.** ``conn.response.create(response=params)`` —
   param field is ``output_modalities`` (not ``modalities`` as in the plan
   sketch). ``conn.response.cancel()`` — no arguments needed for the
   common barge-in case.

``fastrtc`` is not wired. The ``openai`` SDK handles the WebSocket transport
directly; ``fastrtc`` is only needed for browser-style full-duplex WebRTC,
which is not the Phase A app's topology (mic + speaker on the robot talk
to OpenAI over a WebSocket from the robot's own process). The ``fastrtc``
pin in ``pyproject.toml`` remains for future work (e.g., streaming audio
to a menu-bar preview) but is unused here.

Lifecycle
---------
:meth:`OpenAIRealtimeVoice.start_turn` is an ``@asynccontextmanager`` that
opens a fresh ``AsyncRealtimeConnection`` per turn via
``async with client.realtime.connect(...)``. The outer ``async with`` in
the caller drives the connection's ``__aexit__`` — closing the websocket
whether the turn body returned normally or raised. Callers therefore use::

    async with voice.start_turn() as turn:
        await turn.get_user_text()
        await turn.speak_text(reply)
    # websocket closed here, unconditionally

This structural cleanup is what fixed bug I1 — an earlier revision
returned a bare ``VoiceTurn`` and leaked the websocket on every turn.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from openai.types.realtime.conversation_item_input_audio_transcription_completed_event import (
    ConversationItemInputAudioTranscriptionCompletedEvent,
)

from .interface import VoiceInterface, VoiceTurn

if TYPE_CHECKING:
    from openai.resources.realtime.realtime import AsyncRealtimeConnection


class OpenAIRealtimeVoiceTurn(VoiceTurn):
    """One realtime WebSocket session = one turn.

    See module docstring for the verified SDK shape. ``connection`` is an
    ``openai.resources.realtime.realtime.AsyncRealtimeConnection`` yielded
    from ``async with AsyncRealtimeConnectionManager``. Its lifetime is
    owned by the enclosing :meth:`OpenAIRealtimeVoice.start_turn` context
    manager — this class does not close the connection itself; that would
    race with the outer ``async with`` and risk double-close.
    """

    def __init__(self, connection: AsyncRealtimeConnection) -> None:
        self._connection = connection
        self._interrupted = False

    @property
    def connection(self) -> AsyncRealtimeConnection:
        """Escape hatch to the underlying SDK connection for advanced callers."""
        return self._connection

    async def get_user_text(self) -> str:
        """Iterate server events until the final user transcript arrives.

        Returns the ``.transcript`` attribute of the first
        ``conversation.item.input_audio_transcription.completed`` event.
        Blocks until that event arrives; the SDK's async iterator handles
        framing and keep-alives.
        """
        async for event in self._connection:
            if isinstance(event, ConversationItemInputAudioTranscriptionCompletedEvent):
                return event.transcript
        # Iterator terminated (connection closed) without yielding a final
        # transcript. Surface the empty string rather than raising so the
        # caller can decide what to do with silence.
        return ""

    async def speak_text(self, text: str) -> None:
        """Ask the SDK to TTS-and-play ``text`` as the assistant turn.

        Uses ``response.create`` with ``instructions=text`` and both audio +
        text output modalities. The SDK streams audio chunks over the
        WebSocket; the transport layer (robot's speaker) handles playback.
        """
        await self._connection.response.create(
            response={
                "output_modalities": ["audio", "text"],
                "instructions": text,
            },
        )

    async def interrupt(self) -> None:
        """Cancel the in-flight assistant response for barge-in."""
        self._interrupted = True
        await self._connection.response.cancel()


class OpenAIRealtimeVoice(VoiceInterface):
    """Factory for :class:`OpenAIRealtimeVoiceTurn`.

    Reads ``OPENAI_API_KEY`` from the environment at construction unless
    ``api_key=`` is passed explicitly. Fails fast with :class:`ValueError`
    if neither source is populated — catching a missing credential at
    startup beats a cryptic WebSocket handshake error on first use.
    """

    def __init__(
        self,
        model: str = "gpt-realtime",
        *,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY not set and no api_key= argument provided")

    @asynccontextmanager
    async def start_turn(self) -> AsyncIterator[OpenAIRealtimeVoiceTurn]:
        """Open a fresh realtime WebSocket and yield a :class:`VoiceTurn`.

        The SDK's ``client.realtime.connect(model=...)`` returns an
        ``AsyncRealtimeConnectionManager``; entering it yields the live
        :class:`AsyncRealtimeConnection`. We hold that manager as an
        ``async with`` here, so when the caller's ``async with`` closes,
        our ``finally``-equivalent drives the SDK manager's ``__aexit__``
        and the websocket is released.

        Lazy-imports :class:`openai.AsyncOpenAI` so unit tests that only
        exercise construction-shape don't pay the SDK's import cost.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        async with client.realtime.connect(model=self._model) as connection:
            yield OpenAIRealtimeVoiceTurn(connection)
