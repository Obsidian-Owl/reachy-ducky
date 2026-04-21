from __future__ import annotations

from reachy_ducky_protocol.messages import BrainRequest, BrainResponse

from .interface import BrainInterface


class MockBrain(BrainInterface):
    def __init__(self) -> None:
        self.calls: list[BrainRequest] = []

    async def query(self, request: BrainRequest) -> BrainResponse:
        self.calls.append(request)
        return BrainResponse(text=f"[mock] {request.user_utterance}")
