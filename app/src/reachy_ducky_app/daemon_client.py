"""Async HTTP client for the Reachy Ducky Mac daemon.

The daemon exposes ``/health`` (open), ``/brain/query`` (bearer-auth when
``REACHY_DUCKY_AUTH_TOKEN`` is set on the daemon side), and
``/specialists/plan-reviewer`` (same auth). This client wraps them with
typed Pydantic-in, Pydantic-out.

Configuration from env:

- ``DAEMON_URL`` — default ``http://127.0.0.1:8765``.
- ``DAEMON_AUTH_TOKEN`` — if set (and non-empty), sent as
  ``Authorization: Bearer <token>``. Presence wins: setting the env var
  to an empty string is an explicit "no token" (mirrors the daemon's
  :class:`~reachy_ducky_daemon.config.AppConfig` semantics).

Per-endpoint timeouts (applied per-call, not on the pooled client):

- ``/health`` — 2s (cheap, cacheable).
- ``/brain/query`` — 60s (LLM round-trip).
- ``/specialists/plan-reviewer`` — 120s (subagent invocation can take a
  while with tool use).

Connection pooling
------------------
A single :class:`httpx.AsyncClient` is constructed in ``__init__`` and
reused across all endpoint calls. This avoids a TLS handshake per call
(measurable latency over Tailscale). Call :meth:`aclose` on shutdown to
drain the pool cleanly.
"""

from __future__ import annotations

import os

import httpx
from reachy_ducky_protocol.messages import (
    BrainRequest,
    BrainResponse,
    HealthResponse,
    SpecialistRequest,
    SpecialistResponse,
)


class DaemonClient:
    """Typed async HTTP client for the daemon's three public endpoints.

    Holds one pooled :class:`httpx.AsyncClient` for the life of the
    instance. Callers must :meth:`aclose` on shutdown.
    """

    def __init__(
        self,
        base_url: str | None = None,
        auth_token: str | None = None,
    ) -> None:
        resolved_base = base_url or os.environ.get("DAEMON_URL") or "http://127.0.0.1:8765"
        self._base: str = resolved_base.rstrip("/")
        # Presence-wins semantics (mirrors the daemon's own AppConfig):
        #   explicit arg > env-var (even "" clears) > None.
        if auth_token is not None:
            self._token: str | None = auth_token
        else:
            env_token = os.environ.get("DAEMON_AUTH_TOKEN")
            self._token = env_token or None
        # Per-call timeouts diverge by endpoint, so the pooled client has
        # no global timeout — each endpoint method sets its own.
        self._http: httpx.AsyncClient = httpx.AsyncClient()

    @classmethod
    def from_env(cls) -> DaemonClient:
        """Build a client from ``DAEMON_URL`` / ``DAEMON_AUTH_TOKEN``."""
        return cls()

    async def aclose(self) -> None:
        """Close the pooled httpx client. Idempotent."""
        if not self._http.is_closed:
            await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def health(self) -> HealthResponse:
        """GET ``/health``. Returns the daemon's health envelope."""
        r = await self._http.get(
            f"{self._base}/health",
            headers=self._headers(),
            timeout=2.0,
        )
        r.raise_for_status()
        return HealthResponse.model_validate(r.json())

    async def brain_query(
        self,
        text: str,
        *,
        project_slug: str | None = None,
    ) -> BrainResponse:
        """POST ``/brain/query`` with a user utterance and get Ducky's reply."""
        req = BrainRequest(user_utterance=text, project_slug=project_slug)
        r = await self._http.post(
            f"{self._base}/brain/query",
            json=req.model_dump(),
            headers=self._headers(),
            timeout=60.0,
        )
        r.raise_for_status()
        return BrainResponse.model_validate(r.json())

    async def plan_reviewer(
        self,
        *,
        project_slug: str,
        branch: str | None = None,
    ) -> SpecialistResponse:
        """POST ``/specialists/plan-reviewer`` and get a review envelope."""
        req = SpecialistRequest(name="plan-reviewer", project_slug=project_slug, branch=branch)
        r = await self._http.post(
            f"{self._base}/specialists/plan-reviewer",
            json=req.model_dump(),
            headers=self._headers(),
            timeout=120.0,
        )
        r.raise_for_status()
        return SpecialistResponse.model_validate(r.json())
