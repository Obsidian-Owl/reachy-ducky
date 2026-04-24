"""OpenAI Realtime voice implementation.

Wraps the OpenAI Python SDK's realtime WebSocket session behind our
:class:`~reachy_ducky_app.voice.interface.VoiceInterface`. The SDK handles
STT + TTS + turn-taking + barge-in at the protocol level; our job is to
plumb the events through our narrow :class:`VoiceTurn` contract and to
shuttle :data:`AudioFrame` tuples between the :class:`MicSource` /
:class:`SpeakerSink` abstractions and the SDK's base64-framed audio
events.

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

4. **Mic in.** :data:`AudioFrame` tuples from the :class:`MicSource` are
   unpacked at the network boundary: int16 samples are ``.tobytes()``'d,
   base64-encoded, and sent via
   ``conn.input_audio_buffer.append(audio=<base64>)``. With the default
   server-VAD ``turn_detection``, the server commits the buffer and
   triggers transcription on its own — no explicit ``.commit()`` call is
   needed from our side. We run the mic pump as a background task so it
   runs concurrently with the event drain that waits for the final
   transcription event; when the drain loop returns (or raises), the
   pump is cancelled in ``finally``.

5. **Speaker out.** Assistant TTS audio arrives as a stream of
   ``ResponseAudioDeltaEvent`` (``response.output_audio.delta``) whose
   ``.delta`` attribute is a base64-encoded PCM16 mono 24 kHz payload.
   We decode each delta, wrap the int16 samples as an :data:`AudioFrame`
   tuple, and forward to :meth:`SpeakerSink.play` — the transport-tier
   implementation decides how to buffer, resample, and render.

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
from typing import TYPE_CHECKING, cast

import numpy as np
import numpy.typing as npt
import scipy.signal
from openai.types.realtime import (
    RealtimeErrorEvent,
    ResponseAudioDeltaEvent,
    ResponseDoneEvent,
)
from openai.types.realtime.conversation_item_input_audio_transcription_completed_event import (
    ConversationItemInputAudioTranscriptionCompletedEvent,
)
from openai.types.realtime.realtime_audio_config_input_param import (
    RealtimeAudioConfigInputParam,
)
from openai.types.realtime.realtime_audio_config_output_param import (
    RealtimeAudioConfigOutputParam,
)
from openai.types.realtime.realtime_audio_config_param import RealtimeAudioConfigParam
from openai.types.realtime.realtime_audio_formats_param import AudioPCM
from openai.types.realtime.realtime_session_create_request_param import (
    RealtimeSessionCreateRequestParam,
)

from .audio_io import AudioFrame, MicSource, SpeakerSink
from .interface import VoiceInterface, VoiceTurn

if TYPE_CHECKING:
    from openai.resources.realtime.realtime import AsyncRealtimeConnection

logger = logging.getLogger(__name__)

# OpenAI Realtime expects PCM16 mono at 24 kHz on both directions; ``AudioPCM``
# in the SDK pins ``rate: Literal[24000]`` so this is the canonical sample
# rate for both ``input_audio_buffer.append`` and ``response.output_audio.delta``.
_LLM_AUDIO_RATE = 24000


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
          unpacks each :data:`AudioFrame` tuple at the network
          boundary, ``.tobytes()`` + base64-encodes the int16 samples,
          and calls ``connection.input_audio_buffer.append(audio=<base64>)``.
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
                sample_rate, int16 = frame
                # Resample to OpenAI's 24 kHz if the MicSource yields a
                # different rate. Mirrors the reference's per-frame
                # resample pattern (openai_realtime.py:763-764 in
                # pollen-robotics/reachy_mini_conversation_app).
                # ``ReachyMicSource`` always yields 24 kHz, but this
                # guards future sources (sim, other hardware) against
                # chipmunk/slow playback if they yield at a different
                # rate.
                if sample_rate != _LLM_AUDIO_RATE:
                    resampled = cast(
                        npt.NDArray[np.float32],
                        scipy.signal.resample(
                            int16.astype(np.float32),
                            int(len(int16) * _LLM_AUDIO_RATE / sample_rate),
                        ),
                    )
                    int16 = np.clip(resampled, -32768, 32767).astype(np.int16)
                # OpenAI Realtime expects raw PCM16 little-endian bytes
                # in base64. The network boundary just serializes.
                pcm_bytes = int16.tobytes()
                b64 = base64.b64encode(pcm_bytes).decode("ascii")
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
            # Always retrieve the pump's result/exception, even when the
            # task already finished. If it raised (mic hardware failure)
            # and we reached this finally via an early transcript return,
            # the stored exception would otherwise be orphaned — Python
            # emits "Task exception was never retrieved" and the real
            # mic failure is silently swallowed. Re-raising here promotes
            # the mic error over a stale transcript, which is the correct
            # priority for hardware-level failures.
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
        base64-encoded PCM16 mono 24 kHz payload that we decode, wrap as
        an :data:`AudioFrame` tuple ``(24000, int16_array)``, and forward
        to :meth:`SpeakerSink.play`.

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
                pcm_bytes = base64.b64decode(event.delta)
                int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
                # NOTE: ``np.frombuffer`` produces a read-only view over
                # the decoded bytes. The downstream sink may need to
                # reshape or apply post-processing; ``.copy()`` makes
                # that safe and is cheap relative to the WebSocket I/O.
                frame: AudioFrame = (_LLM_AUDIO_RATE, int16.copy())
                await self._speaker.play(frame)
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
    (today the silent mock or the hardware-backed adapter, depending on
    whether a ``ReachyMini`` is supplied). Tests can pass
    :class:`MockMicSource` / :class:`MockSpeakerSink` directly.
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

        After the connection opens we issue a ``session.update`` to pin
        PCM16 at 24 kHz on both directions explicitly — matching the
        reference conversation app's
        ``openai_realtime.py:504-521`` pattern. The Realtime API would
        default to PCM16/24 kHz here too (it's the only ``AudioPCM`` rate
        the SDK type allows), but the explicit pin documents intent and
        protects against future SDK default drift.

        Lazy-imports :class:`openai.AsyncOpenAI` so unit tests that only
        exercise construction-shape don't pay the SDK's import cost.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        async with client.realtime.connect(model=self._model) as connection:
            # The SDK's ``AudioPCM`` pins ``rate: Literal[24000]`` — the
            # only PCM rate the Realtime API accepts. Passing the literal
            # ``24000`` directly satisfies the TypedDict; the
            # ``_LLM_AUDIO_RATE`` constant elsewhere in the module
            # documents the same value at the audio-conversion sites.
            session_config = RealtimeSessionCreateRequestParam(
                type="realtime",
                audio=RealtimeAudioConfigParam(
                    input=RealtimeAudioConfigInputParam(
                        format=AudioPCM(type="audio/pcm", rate=24000),
                    ),
                    output=RealtimeAudioConfigOutputParam(
                        format=AudioPCM(type="audio/pcm", rate=24000),
                    ),
                ),
            )
            await connection.session.update(session=session_config)
            yield OpenAIRealtimeVoiceTurn(
                connection,
                mic=self._mic,
                speaker=self._speaker,
            )
