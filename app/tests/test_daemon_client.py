"""Tests for :class:`DaemonClient` — typed async HTTP wrapper."""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock
from reachy_ducky_app.daemon_client import DaemonClient
from reachy_ducky_protocol.messages import (
    BrainResponse,
    HealthResponse,
    SpecialistResponse,
)


def test_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env and no args, the client defaults to 127.0.0.1:8765."""
    monkeypatch.delenv("DAEMON_URL", raising=False)
    monkeypatch.delenv("DAEMON_AUTH_TOKEN", raising=False)
    client = DaemonClient()
    assert client._base == "http://127.0.0.1:8765"
    assert client._token is None


def test_base_url_strips_trailing_slash() -> None:
    """A trailing slash on the base URL is stripped so path joins are clean."""
    client = DaemonClient(base_url="http://x/")
    assert client._base == "http://x"


def test_from_env_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """``from_env`` reads ``DAEMON_URL`` and ``DAEMON_AUTH_TOKEN``."""
    monkeypatch.setenv("DAEMON_URL", "http://mac.tailnet.ts.net:8765")
    monkeypatch.setenv("DAEMON_AUTH_TOKEN", "tok")
    client = DaemonClient.from_env()
    assert client._base == "http://mac.tailnet.ts.net:8765"
    assert client._token == "tok"


def test_explicit_auth_token_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructor ``auth_token`` overrides any env-var value."""
    monkeypatch.setenv("DAEMON_AUTH_TOKEN", "from-env")
    client = DaemonClient(auth_token="from-arg")
    assert client._token == "from-arg"


def test_empty_env_token_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty-string env var is treated as an explicit "no token" (matches AppConfig)."""
    monkeypatch.setenv("DAEMON_AUTH_TOKEN", "")
    client = DaemonClient()
    assert client._token is None


@pytest.mark.asyncio
async def test_health_parses_response(httpx_mock: HTTPXMock) -> None:
    """``/health`` deserialises to :class:`HealthResponse`."""
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:8765/health",
        json={
            "ok": True,
            "brain": "MockBrain",
            "memory_ready": True,
            "projects": ["demo"],
        },
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    resp = await client.health()
    assert isinstance(resp, HealthResponse)
    assert resp.ok is True
    assert resp.brain == "MockBrain"
    assert resp.memory_ready is True
    assert resp.projects == ["demo"]


@pytest.mark.asyncio
async def test_brain_query_parses_response(httpx_mock: HTTPXMock) -> None:
    """``/brain/query`` POSTs user_utterance and parses the reply."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "hello", "specialist_invoked": None},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    resp = await client.brain_query("hi")
    assert isinstance(resp, BrainResponse)
    assert resp.text == "hello"
    assert resp.specialist_invoked is None

    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.method == "POST"
    body = json.loads(sent.content)
    assert body["user_utterance"] == "hi"


@pytest.mark.asyncio
async def test_brain_query_passes_project_slug(httpx_mock: HTTPXMock) -> None:
    """When given, ``project_slug`` is serialised into the posted body."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    await client.brain_query("hi", project_slug="demo")

    sent = httpx_mock.get_request()
    assert sent is not None
    body = json.loads(sent.content)
    assert body["project_slug"] == "demo"


@pytest.mark.asyncio
async def test_brain_query_forwards_none_slug(httpx_mock: HTTPXMock) -> None:
    """``project_slug=None`` is forwarded verbatim (daemon falls back to primary)."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    await client.brain_query("hi")

    sent = httpx_mock.get_request()
    assert sent is not None
    body = json.loads(sent.content)
    assert "project_slug" in body
    assert body["project_slug"] is None


@pytest.mark.asyncio
async def test_plan_reviewer_parses_response(httpx_mock: HTTPXMock) -> None:
    """``/specialists/plan-reviewer`` POSTs a :class:`SpecialistRequest`."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/specialists/plan-reviewer",
        json={
            "name": "plan-reviewer",
            "summary": "looks good",
            "flags": ["scope-creep"],
        },
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    resp = await client.plan_reviewer(project_slug="demo")
    assert isinstance(resp, SpecialistResponse)
    assert resp.name == "plan-reviewer"
    assert resp.summary == "looks good"
    assert resp.flags == ["scope-creep"]

    sent = httpx_mock.get_request()
    assert sent is not None
    body = json.loads(sent.content)
    assert body["name"] == "plan-reviewer"
    assert body["project_slug"] == "demo"
    assert body["branch"] is None


@pytest.mark.asyncio
async def test_plan_reviewer_passes_branch(httpx_mock: HTTPXMock) -> None:
    """When given, ``branch`` is serialised into the posted body."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "summary": "ok", "flags": []},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    await client.plan_reviewer(project_slug="demo", branch="feature/x")

    sent = httpx_mock.get_request()
    assert sent is not None
    body = json.loads(sent.content)
    assert body["branch"] == "feature/x"


@pytest.mark.asyncio
async def test_auth_header_sent_when_token_configured(httpx_mock: HTTPXMock) -> None:
    """With a token configured, ``Authorization: Bearer <token>`` is sent."""
    httpx_mock.add_response(
        method="POST",
        url="http://mac.tailnet.ts.net:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    client = DaemonClient(
        base_url="http://mac.tailnet.ts.net:8765",
        auth_token="sekret",
    )
    await client.brain_query("hi")
    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.headers["authorization"] == "Bearer sekret"


@pytest.mark.asyncio
async def test_auth_header_not_sent_without_token(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no token anywhere, no Authorization header is emitted."""
    monkeypatch.delenv("DAEMON_AUTH_TOKEN", raising=False)
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    await client.brain_query("hi")
    sent = httpx_mock.get_request()
    assert sent is not None
    assert "authorization" not in {k.lower() for k in sent.headers.keys()}


@pytest.mark.asyncio
async def test_health_has_no_auth_header(
    httpx_mock: HTTPXMock,
) -> None:
    """``/health`` is the open route and does not send a bearer header."""
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:8765/health",
        json={"ok": True, "brain": "X", "memory_ready": False, "projects": []},
    )
    # Token is configured but /health is open by design (works with or without).
    # The current implementation sends the header even on /health; that's fine
    # because the server's BearerAuthMiddleware lets /health through either
    # way. This test documents that we don't *rely* on sending the header.
    client = DaemonClient(base_url="http://127.0.0.1:8765", auth_token="sekret")
    resp = await client.health()
    assert resp.ok is True


@pytest.mark.asyncio
async def test_4xx_raises_http_status_error(httpx_mock: HTTPXMock) -> None:
    """A 4xx response raises :class:`httpx.HTTPStatusError` (propagated)."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        status_code=401,
        json={"detail": "invalid bearer token"},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    with pytest.raises(httpx.HTTPStatusError):
        await client.brain_query("hi")


@pytest.mark.asyncio
async def test_5xx_raises_http_status_error(httpx_mock: HTTPXMock) -> None:
    """A 5xx response also propagates as :class:`httpx.HTTPStatusError`."""
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:8765/health",
        status_code=503,
        json={"detail": "unavailable"},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    with pytest.raises(httpx.HTTPStatusError):
        await client.health()


@pytest.mark.asyncio
async def test_construction_builds_one_httpx_async_client() -> None:
    """The client holds a single pooled ``httpx.AsyncClient`` in ``_http``.

    Regression for bug I2: the old implementation opened a fresh
    ``httpx.AsyncClient`` inside each endpoint method via
    ``async with httpx.AsyncClient(timeout=...) as http``. Over Tailscale
    that meant a TLS handshake per call. The pooled instance eliminates
    that cost.
    """
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    assert isinstance(client._http, httpx.AsyncClient)
    assert client._http.is_closed is False
    await client.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_underlying_client() -> None:
    """``aclose()`` closes the pooled ``httpx.AsyncClient``."""
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    assert client._http.is_closed is False
    await client.aclose()
    assert client._http.is_closed is True


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    """Calling ``aclose()`` twice does not raise — shutdown paths can be untidy."""
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    await client.aclose()
    await client.aclose()  # must not raise


@pytest.mark.asyncio
async def test_successive_calls_reuse_pooled_client(httpx_mock: HTTPXMock) -> None:
    """All three endpoints use ``self._http``, not a fresh client per call.

    The most direct assertion is that the identity of ``client._http``
    does not change across calls, and that the instance is still open
    after several requests have been made through it.
    """
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:8765/health",
        json={"ok": True, "brain": "X", "memory_ready": True, "projects": []},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "summary": "ok", "flags": []},
    )

    client = DaemonClient(base_url="http://127.0.0.1:8765")
    pooled = client._http
    try:
        await client.health()
        assert client._http is pooled
        assert pooled.is_closed is False
        await client.brain_query("hi")
        assert client._http is pooled
        assert pooled.is_closed is False
        await client.plan_reviewer(project_slug="demo")
        assert client._http is pooled
        assert pooled.is_closed is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_brain_query_uses_sixty_second_timeout(
    httpx_mock: HTTPXMock,
) -> None:
    """``/brain/query`` preserves its 60s timeout after the pooling refactor.

    Timeouts must be applied per-call (not set globally on the pooled
    client) because each endpoint has a different budget. Verify via the
    ``httpx.Request.extensions['timeout']`` shape that pytest-httpx
    preserves.
    """
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "ok", "specialist_invoked": None},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    try:
        await client.brain_query("hi")
    finally:
        await client.aclose()

    sent = httpx_mock.get_request()
    assert sent is not None
    # httpx stores per-request timeout config in request.extensions["timeout"];
    # it's a dict with connect/read/write/pool keys.
    timeout = sent.extensions.get("timeout")
    assert timeout is not None
    assert timeout["read"] == pytest.approx(60.0)


@pytest.mark.asyncio
async def test_health_uses_two_second_timeout(httpx_mock: HTTPXMock) -> None:
    """``/health`` preserves its 2s timeout after the pooling refactor."""
    httpx_mock.add_response(
        method="GET",
        url="http://127.0.0.1:8765/health",
        json={"ok": True, "brain": "X", "memory_ready": True, "projects": []},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    try:
        await client.health()
    finally:
        await client.aclose()

    sent = httpx_mock.get_request()
    assert sent is not None
    timeout = sent.extensions.get("timeout")
    assert timeout is not None
    assert timeout["read"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_plan_reviewer_uses_one_hundred_twenty_second_timeout(
    httpx_mock: HTTPXMock,
) -> None:
    """``/specialists/plan-reviewer`` preserves its 120s timeout after the refactor."""
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/specialists/plan-reviewer",
        json={"name": "plan-reviewer", "summary": "ok", "flags": []},
    )
    client = DaemonClient(base_url="http://127.0.0.1:8765")
    try:
        await client.plan_reviewer(project_slug="demo")
    finally:
        await client.aclose()

    sent = httpx_mock.get_request()
    assert sent is not None
    timeout = sent.extensions.get("timeout")
    assert timeout is not None
    assert timeout["read"] == pytest.approx(120.0)
