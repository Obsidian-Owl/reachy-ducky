"""OpenAI Realtime voice implementation.

Wraps the OpenAI Python SDK's realtime WebSocket session behind our
:class:`~reachy_ducky_app.voice.interface.VoiceInterface`. The SDK handles
STT + TTS + turn-taking + barge-in at the protocol level; our job is to
plumb the events through our narrow :class:`VoiceTurn` contract and to
shuttle raw PCM between the :class:`MicSource` / :class:`SpeakerSink`
abstractions and the SDK's base64-framed audio events.

**Not run in unit tests.** The stateful session methods require a live
connection to OpenAI. Unit coverage is construction-shape only (API-key
plumbing, model plumbing, export surface) plus scripted-event drain /
mic-pump tests that drive the turn through a fake ``AsyncRealtimeConnection``.
One ``@pytest.mark.integration`` smoke (gated by
``REACHY_DUCKY_RUN_INTEGRATION=1`` + ``OPENAI_API_KEY``) opens a real
WebSocket session to validate the plumbing.

Verified SDK shape (``openai==1.109.1``)
-----------------------------------------
The installed SDK diverges from the Phase A plan sketch in six places,
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

4. **Mic in.** Raw PCM frames from the :class:`MicSource` are
   base64-encoded and sent via
   ``conn.input_audio_buffer.append(audio=<base64>)``. With the default
   server-VAD ``turn_detection``, the server commits the buffer and
   triggers transcription on its own — no explicit ``.commit()`` call is
   needed from our side. We run the mic pump as a background task so it
   runs concurrently with the event drain that waits for the final
   transcription event; when the drain loop returns (or raises), the
   pump is cancelled in ``finally``.

5. **Speaker out.** Assistant TTS audio arrives as a stream of
   ``ResponseAudioDeltaEvent`` (``response.output_audio.delta``) whose
   ``.delta`` attribute is a base64-encoded PCM payload. We decode each
   delta and forward the raw bytes to :meth:`SpeakerSink.play` — the
   transport-tier implementation decides how to buffer and render.

6. **Response create/cancel.** ``conn.response.create(response=params)`` —
   param field is ``output_modalities`` (not ``modalities`` as in the plan
   sketch). ``conn.response.cancel()`` — no arguments needed for the
   common barge-in case. The terminal server event for *any* response
   (completed / cancelled / failed / incomplete) is a single
   ``ResponseDoneEvent``; its ``response.status`` field disambiguates
   the four cases. Session-level problems arrive as ``RealtimeErrorEvent``
   with ``type == "error"``, decoupled from any specific response.

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

import asyncio
import base64
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from openai.types.realtime import (
    RealtimeErrorEvent,
    ResponseAudioDeltaEvent,
    ResponseDoneEvent,
)
from openai.types.realtime.conversation_item_input_audio_transcription_completed_event import (
    ConversationItemInputAudioTranscriptionCompletedEvent,
)

from .audio_io import MicSource, SpeakerSink
from .interface import VoiceInterface, VoiceTurn

if TYPE_CHECKING:
    from openai.resources.realtime.realtime import AsyncRealtimeConnection

logger = logging.getLogger(__name__)


class OpenAIRealtimeVoiceTurn(VoiceTurn):
    """One realtime WebSocket session = one turn.

    See module docstring for the verified SDK shape. ``connection`` is an
    ``openai.resources.realtime.realtime.AsyncRealtimeConnection`` yielded
    from ``async with AsyncRealtimeConnectionManager``. Its lifetime is
    owned by the enclosing :meth:`OpenAIRealtimeVoice.start_turn` context
    manager — this class does not close the connection itself; that would
    race with the outer ``async with`` and risk double-close.
    """

    def __init__(
        self,
        connection: AsyncRealtimeConnection,
        *,
        mic: MicSource,
        speaker: SpeakerSink,
    ) -> None:
        self._connection = connection
        self._mic = mic
        self._speaker = speaker
        self._interrupted = False

    @property
    def connection(self) -> AsyncRealtimeConnection:
        """Escape hatch to the underlying SDK connection for advanced callers."""
        return self._connection

    async def get_user_text(self) -> str:
        """Pump mic frames into the session; drain events for final transcript.

        Two concurrent tasks run:

        * A **mic pump** that iterates :meth:`MicSource.frames`,
          base64-encodes each raw PCM frame, and calls
          ``connection.input_audio_buffer.append(audio=<base64>)``.
          With server-VAD turn_detection (the SDK default) the server
          decides when speech has ended and commits the buffer itself.
        * The **event drain** on ``self._connection``, which returns the
          ``.transcript`` of the first
          ``ConversationItemInputAudioTranscriptionCompletedEvent``.

        The pump is a background :class:`asyncio.Task` and is cancelled
        in ``finally`` when the drain loop exits — whether that's because
        transcription completed, the connection closed without a final
        event, or an exception unwound through the drain. If the pump
        raises (e.g. the mic hardware disconnected) the exception is
        surfaced to the caller via the awaited task's result during
        cleanup.
        """

        async def _pump_mic() -> None:
            async for frame in self._mic.frames():
                b64 = base64.b64encode(frame).decode("ascii")
                await self._connection.input_audio_buffer.append(audio=b64)

        pump_task = asyncio.create_task(_pump_mic())
        try:
            async for event in self._connection:
                if isinstance(event, ConversationItemInputAudioTranscriptionCompletedEvent):
                    return event.transcript
                # If the mic pump has failed we want to see the error
                # rather than waiting forever for a transcript that can
                # never arrive — surface it promptly.
                if pump_task.done() and not pump_task.cancelled():
                    pump_exc = pump_task.exception()
                    if pump_exc is not None:
                        raise pump_exc
            # Iterator terminated (connection closed) without yielding a
            # final transcript. Let the pump run to completion so a mic
            # failure surfaces in place of ordinary silence — without
            # this yield the pump may never have been scheduled.
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            return ""
        finally:
            if not pump_task.done():
                pump_task.cancel()
                try:
                    await pump_task
                except asyncio.CancelledError:
                    pass

    async def speak_text(self, text: str) -> None:
        """Ask the SDK to TTS-and-play ``text`` as the assistant turn.

        Uses ``response.create`` with ``instructions=text`` and both audio +
        text output modalities. The SDK streams audio chunks over the
        WebSocket as ``response.output_audio.delta`` events
        (:class:`ResponseAudioDeltaEvent`); each ``.delta`` field is a
        base64-encoded PCM payload that we decode and forward to
        :meth:`SpeakerSink.play`.

        Drains the connection's event iterator until the terminal
        ``response.done`` (completed / cancelled / failed / incomplete)
        arrives. Returning earlier would let the enclosing ``start_turn``
        context manager close the websocket mid-stream, truncating audio.

        Best-effort semantics: a failed response or a session-level error
        event is logged at WARNING and returns cleanly, so the caller's
        state machine still progresses. ``interrupt()`` funnels through
        the same drain — the server emits ``response.done`` with
        ``status == "cancelled"``, which this loop observes and exits on.
        """
        await self._connection.response.create(
            response={
                "output_modalities": ["audio", "text"],
                "instructions": text,
            },
        )
        async for event in self._connection:
            if isinstance(event, ResponseAudioDeltaEvent):
                pcm = base64.b64decode(event.delta)
                await self._speaker.play(pcm)
                continue
            if isinstance(event, ResponseDoneEvent):
                status = event.response.status
                if status in ("failed", "incomplete"):
                    logger.warning(
                        "realtime response ended without completing",
                        extra={"status": status, "response_id": event.response.id},
                    )
                return
            if isinstance(event, RealtimeErrorEvent):
                logger.warning(
                    "realtime session error during speak_text",
                    extra={
                        "error_type": event.error.type,
                        "error_code": event.error.code,
                    },
                )
                return

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

    ``mic`` and ``speaker`` are required. Production code wires them via
    :func:`~reachy_ducky_app.voice.audio_io.load_default_mic_source` and
    :func:`~reachy_ducky_app.voice.audio_io.load_default_speaker_sink`
    (today a silent mock, eventually the Reachy hardware bindings).
    Tests can pass :class:`MockMicSource` /
    :class:`MockSpeakerSink` directly.
    """

    def __init__(
        self,
        model: str = "gpt-realtime",
        *,
        mic: MicSource,
        speaker: SpeakerSink,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._mic = mic
        self._speaker = speaker
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
            yield OpenAIRealtimeVoiceTurn(
                connection,
                mic=self._mic,
                speaker=self._speaker,
            )
