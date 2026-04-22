from __future__ import annotations

import os

import pytest
from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain
from reachy_ducky_protocol.messages import BrainRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_claude_responds() -> None:
    """Live Claude end-to-end smoke: gated by REACHY_DUCKY_RUN_INTEGRATION."""
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")
    brain = ClaudeSDKBrain()
    resp = await brain.query(BrainRequest(user_utterance="Say the single word: pong"))
    assert "pong" in resp.text.lower()
