"""Claude Agent SDK brain implementation.

Two construction paths:

1. :meth:`ClaudeSDKBrain.__init__` (``system_prompt=``, ``model=``) — the
   Task 2.2 text-stream path. Each :meth:`query` builds a minimal
   :class:`ClaudeAgentOptions` with just the system prompt and model. No
   tools, no MCP servers, no hooks.
2. :meth:`ClaudeSDKBrain.with_tools` — the Task 3.5 Pattern B path. Calls
   :func:`~reachy_ducky_daemon.brain.options.build_brain_options` to assemble
   the full tool surface (Read/Glob/Grep/Bash/Task + plans MCP + optional
   github-mcp-server + PreToolUse security gate + write-tool lockdown +
   ``permission_mode='dontAsk'``) and stores the pre-built options on the
   instance. :meth:`query` forwards them unchanged.

:meth:`query` is a single implementation — it uses the pre-built options if
present, otherwise synthesises the minimal text-stream shape. The two paths
differ only in *which* options :func:`sdk_query` sees, never in how text is
extracted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock
from claude_agent_sdk import query as sdk_query
from reachy_ducky_protocol.messages import BrainRequest, BrainResponse

from .interface import BrainInterface
from .options import DEFAULT_BRAIN_SYSTEM_PROMPT, build_brain_options

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
        *,
        _options: ClaudeAgentOptions | None = None,
    ) -> None:
        """Construct a brain.

        Public callers pass ``system_prompt`` / ``model`` for the text-stream
        path, or call :meth:`with_tools` for the Pattern B path. The
        ``_options`` kwarg is a private plumbing channel for :meth:`with_tools`
        to hand a pre-built :class:`ClaudeAgentOptions` in; do not pass it
        directly.
        """
        self._system_prompt = system_prompt
        self._model = model
        self._prebuilt_options = _options

    @classmethod
    def with_tools(
        cls,
        *,
        cwd: Path,
        memory_root: Path,
        github_repo: str | None = None,
        system_prompt: str = DEFAULT_BRAIN_SYSTEM_PROMPT,
        model: str = "claude-sonnet-4-6",
    ) -> ClaudeSDKBrain:
        """Construct a brain with the Pattern B tool surface wired.

        Delegates to :func:`~reachy_ducky_daemon.brain.options.build_brain_options`
        and stores the resulting :class:`ClaudeAgentOptions` on the instance so
        :meth:`query` forwards it verbatim to :func:`sdk_query` — full tool
        surface, plans MCP, optional github-mcp-server, PreToolUse security
        gate, write-tool lockdown, ``permission_mode='dontAsk'``.

        Args:
            cwd: Project root the brain's Read/Glob/Grep/Bash are scoped to.
            memory_root: Additional read-scoped directory (memory tree).
            github_repo: If provided, adds the external github-mcp-server +
                ``mcp__github__*`` tool glob. Must match ``owner/repo``.
            system_prompt: Overrides
                :data:`~reachy_ducky_daemon.brain.options.DEFAULT_BRAIN_SYSTEM_PROMPT`.
            model: Overrides the default ``claude-sonnet-4-6``.

        Returns:
            A :class:`ClaudeSDKBrain` whose :meth:`query` uses the pre-built
            options.
        """
        options = build_brain_options(
            cwd=cwd,
            memory_root=memory_root,
            github_repo=github_repo,
            system_prompt=system_prompt,
            model=model,
        )
        return cls(_options=options)

    async def query(self, request: BrainRequest) -> BrainResponse:
        """Stream SDK events for ``request`` and join the text blocks.

        Uses the pre-built options when constructed via :meth:`with_tools`;
        otherwise synthesises the minimal text-stream shape from
        ``system_prompt`` / ``model``.
        """
        options = self._prebuilt_options or ClaudeAgentOptions(
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
