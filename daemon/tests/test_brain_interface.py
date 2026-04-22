from __future__ import annotations

import pytest
from reachy_ducky_daemon.brain.interface import BrainInterface
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_protocol.messages import BrainRequest


def test_brain_interface_is_abstract() -> None:
    """BrainInterface cannot be instantiated directly — it is an ABC."""
    with pytest.raises(TypeError):
        BrainInterface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_mock_brain_echoes_prompt() -> None:
    """MockBrain.query echoes the utterance back in `resp.text` with a [mock] prefix."""
    brain = MockBrain()
    resp = await brain.query(BrainRequest(user_utterance="hello"))
    assert resp.text == "[mock] hello"


@pytest.mark.asyncio
async def test_mock_brain_records_calls() -> None:
    """MockBrain records each incoming request on `calls` so tests can assert invocation."""
    brain = MockBrain()
    await brain.query(BrainRequest(user_utterance="ping"))
    assert brain.calls[-1].user_utterance == "ping"
