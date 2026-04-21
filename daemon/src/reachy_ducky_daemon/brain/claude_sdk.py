"""Claude Agent SDK brain implementation."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock
from claude_agent_sdk import query as sdk_query
from reachy_ducky_protocol.messages import BrainRequest, BrainResponse

from .interface import BrainInterface

DEFAULT_SYSTEM_PROMPT = (
    "You are Ducky, a read-only rubber-ducky development companion. "
    "You observe and answer; you do not write code. "
    "Be terse. Prefer concrete specifics over vague approval."
)


class ClaudeSDKBrain(BrainInterface):
    """Brain backed by claude_agent_sdk.query; concatenates streamed text."""

    def __init__(
        self,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._system_prompt = system_prompt
        self._model = model

    async def query(self, request: BrainRequest) -> BrainResponse:
        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            model=self._model,
        )
        parts: list[str] = []
        async for chunk in sdk_query(prompt=request.user_utterance, options=options):
            parts.extend(_extract_text(chunk))
        return BrainResponse(text="".join(parts))


def _extract_text(chunk: Any) -> list[str]:
    """Pull text from an SDK event.

    Handles two shapes:
      - Plain ``dict`` with ``{"type": "text", "text": ...}`` (test doubles).
      - Typed ``AssistantMessage`` whose ``content`` holds ``TextBlock``s (live SDK).
    Other event types (UserMessage, SystemMessage, ResultMessage, thinking,
    tool use, rate-limit) contribute no text.
    """
    if isinstance(chunk, dict):
        if chunk.get("type") == "text":
            text = chunk.get("text", "")
            return [text] if isinstance(text, str) else []
        return []
    if isinstance(chunk, AssistantMessage):
        return [block.text for block in chunk.content if isinstance(block, TextBlock)]
    return []
