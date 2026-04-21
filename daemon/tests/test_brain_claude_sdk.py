from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest
from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain
from reachy_ducky_protocol.messages import BrainRequest


@pytest.mark.asyncio
async def test_claude_sdk_brain_joins_streamed_text() -> None:
    """ClaudeSDKBrain concatenates streamed text chunks into BrainResponse.text."""
    fake_chunks: list[dict[str, Any]] = [
        {"type": "text", "text": "hello "},
        {"type": "text", "text": "world"},
    ]

    async def fake_query(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        for chunk in fake_chunks:
            yield chunk

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain(system_prompt="you are Ducky")
        resp = await brain.query(BrainRequest(user_utterance="hi"))

    assert resp.text == "hello world"


@pytest.mark.asyncio
async def test_claude_sdk_brain_passes_user_prompt() -> None:
    """ClaudeSDKBrain forwards the user's utterance as the SDK prompt."""
    seen: dict[str, Any] = {}

    async def fake_query(prompt: str, options: Any) -> AsyncIterator[dict[str, Any]]:
        seen["prompt"] = prompt
        yield {"type": "text", "text": "ack"}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain()
        await brain.query(BrainRequest(user_utterance="what's on my branch?"))

    assert "what's on my branch?" in seen["prompt"]
