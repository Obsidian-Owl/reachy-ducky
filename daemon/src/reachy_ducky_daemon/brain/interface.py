from __future__ import annotations

from abc import ABC, abstractmethod

from reachy_ducky_protocol.messages import BrainRequest, BrainResponse


class BrainInterface(ABC):
    """Abstract brain. Implementations: ClaudeSDKBrain, CodexBrain (future), MockBrain."""

    @abstractmethod
    async def query(self, request: BrainRequest) -> BrainResponse: ...
