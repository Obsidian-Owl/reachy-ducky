"""FastAPI server scaffold for the Reachy Ducky daemon.

Exposes ``create_app`` for tests and ``main`` as the console-script entry
point. ``BearerAuthMiddleware`` protects every route except ``/health``
(kept open so Tailscale/LAN health checks work without the token).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from reachy_ducky_protocol.messages import HealthResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .brain.interface import BrainInterface
from .config import Config
from .memory.layout import ensure_layout

_OPEN_PATHS = frozenset({"/health", "/docs", "/openapi.json"})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """When a token is configured, require ``Authorization: Bearer <token>`` on every
    route except ``/health`` (kept open so Tailscale/LAN health checks still work)."""

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._token is None or request.url.path in _OPEN_PATHS:
            return await call_next(request)
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"detail": "missing bearer token"})
        if header.removeprefix("Bearer ").strip() != self._token:
            return JSONResponse(status_code=401, content={"detail": "invalid bearer token"})
        return await call_next(request)


def create_app(
    *,
    brain: BrainInterface,
    memory_root: Path,
    auth_token: str | None = None,
) -> FastAPI:
    ensure_layout(memory_root)
    app = FastAPI(title="reachy-ducky-daemon")
    app.add_middleware(BearerAuthMiddleware, token=auth_token)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            brain=type(brain).__name__,
            memory_ready=(memory_root / "ducky" / "soul.md").exists(),
        )

    return app


def main() -> None:
    import logging

    import uvicorn

    from .brain.claude_sdk import ClaudeSDKBrain

    logging.basicConfig(level=logging.INFO)
    cfg = Config.from_env()
    cfg.warn_if_exposed_without_auth()
    app = create_app(
        brain=ClaudeSDKBrain(),
        memory_root=cfg.memory_root,
        auth_token=cfg.auth_token,
    )
    uvicorn.run(app, host=cfg.host, port=cfg.port)
