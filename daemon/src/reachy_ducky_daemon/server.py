"""FastAPI server scaffold for the Reachy Ducky daemon.

Exposes ``create_app`` for tests and ``main`` as the console-script entry
point. ``BearerAuthMiddleware`` protects every route except ``/health``
(kept open so Tailscale/LAN health checks work without the token).

Multi-project routing is delegated to :class:`BrainRegistry`: ``/brain/query``
and ``/specialists/*`` look the brain up by slug, falling back to the
registry's primary project when a request omits its slug.
"""

from __future__ import annotations

import hmac
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from reachy_ducky_protocol.messages import (
    BrainRequest,
    BrainResponse,
    HealthResponse,
    SpecialistRequest,
    SpecialistResponse,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .brain.registry import BrainRegistry
from .memory.layout import ensure_layout
from .specialists.plan_reviewer import PlanReviewer
from .specialists.pr_reviewer import PRReviewer

# /docs and /openapi.json are open by intent. In the Tailscale-primary
# deployment envelope the schema disclosure is an acceptable trade for
# discoverability. If you ever bind 0.0.0.0 outside Tailscale, reconsider.
# Exact match only: "/health/" (trailing slash), "/Health", "/HEALTH"
# all hit the Bearer gate. Fail-closed on path ambiguity is correct.
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
        presented = header.removeprefix("Bearer ").strip()
        if not presented or not hmac.compare_digest(presented, self._token):
            return JSONResponse(status_code=401, content={"detail": "invalid bearer token"})
        return await call_next(request)


def _peek_brain_class(registry: BrainRegistry) -> str:
    """Return a brain-class label for ``/health`` without forcing a lazy build.

    Preserves :class:`BrainRegistry`'s lazy-build property: if a primary brain
    has been materialised, report its class name; if a primary is configured
    but unbuilt, report ``"unbuilt"``; if no primary is configured, report
    ``"none"``. We intentionally do NOT call ``brain_for`` here — that would
    make ``/health`` construct a real ``ClaudeSDKBrain.with_tools(...)`` just
    to report its type.
    """
    primary = registry.primary_slug()
    if primary is None:
        return "none"
    if primary in registry.built_slugs():
        return type(registry.brain_for(primary)).__name__
    return "unbuilt"


def create_app(
    *,
    registry: BrainRegistry,
    memory_root: Path,
    auth_token: str | None = None,
) -> FastAPI:
    """Build the FastAPI app that serves the daemon's HTTP surface.

    Args:
        registry: Per-slug brain registry. ``/brain/query`` and
            ``/specialists/*`` route through here.
        memory_root: Directory ensure-layout is called on at startup.
        auth_token: Optional bearer token; ``None`` disables auth.
    """
    ensure_layout(memory_root)
    app = FastAPI(title="reachy-ducky-daemon")
    app.add_middleware(BearerAuthMiddleware, token=auth_token)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            brain=_peek_brain_class(registry),
            memory_ready=(memory_root / "ducky" / "soul.md").exists(),
            projects=registry.slugs(),
        )

    @app.post("/brain/query", response_model=BrainResponse)
    async def brain_query(req: BrainRequest) -> BrainResponse:
        slug = req.project_slug or registry.primary_slug()
        if slug is None:
            raise HTTPException(
                status_code=400,
                detail="no project_slug in request and no primary project configured",
            )
        try:
            brain = registry.brain_for(slug)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown project: {slug}") from None
        return await brain.query(req)

    @app.post("/specialists/plan-reviewer", response_model=SpecialistResponse)
    async def plan_reviewer_route(req: SpecialistRequest) -> SpecialistResponse:
        try:
            brain = registry.brain_for(req.project_slug)
            repo = registry.path_for(req.project_slug)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown project: {req.project_slug}",
            ) from None
        return await PlanReviewer(brain=brain, repo=repo).review()

    @app.post("/specialists/pr-reviewer", response_model=SpecialistResponse)
    async def pr_reviewer_route(req: SpecialistRequest) -> SpecialistResponse:
        # Validate project configuration BEFORE touching the brain factory so
        # misconfiguration rejections (404 unknown-slug, 400 missing github_repo)
        # stay cheap and side-effect-free. Brain construction is documented as
        # pure config assembly today (brain/registry.py), but the invariant
        # is only as solid as the docstring — order matters if that changes.
        try:
            project = registry.project_for(req.project_slug)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"unknown project: {req.project_slug}",
            ) from None
        if not project.github_repo:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"project '{req.project_slug}' has no github_repo configured — "
                    "pr-reviewer needs owner/repo to target the GitHub API"
                ),
            )
        # Project.__post_init__ already validated the 'owner/repo' shape.
        owner, repo_name = project.github_repo.split("/", 1)
        brain = registry.brain_for(req.project_slug)
        return await PRReviewer(
            brain=brain,
            repo=project.path,
            owner=owner,
            repo_name=repo_name,
        ).review(pr_number=req.pr_number)

    return app


def main() -> None:
    import logging

    import uvicorn

    from .brain.claude_sdk import ClaudeSDKBrain
    from .brain.interface import BrainInterface
    from .config import AppConfig
    from .project import Project

    logging.basicConfig(level=logging.INFO)
    cfg = AppConfig.load()
    cfg.warn_if_exposed_without_auth()

    def build_brain(project: Project) -> BrainInterface:
        return ClaudeSDKBrain.with_tools(
            cwd=project.path,
            memory_root=cfg.memory_root,
            github_repo=project.github_repo,
        )

    registry = BrainRegistry(projects=cfg.projects, build_brain=build_brain)
    app = create_app(
        registry=registry,
        memory_root=cfg.memory_root,
        auth_token=cfg.auth_token,
    )
    uvicorn.run(app, host=cfg.host, port=cfg.port)
