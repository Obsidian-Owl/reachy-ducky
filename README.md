# Reachy Ducky

A personal, embodied "rubber ducky" development companion for Reachy Mini.
Read-only desk robot that watches your agentic SWE workflow and talks with you.

See `docs/plans/2026-04-21-reachy-ducky-design.md` for the full design.

Status: Phase A MVP in progress.

## Run (Phase A)

- Mac daemon: `uv run reachy-ducky-daemon` (first run: `uv run reachy-ducky init`
  to write `~/.reachy-ducky/config.toml` + `.env`).
- Menu-bar (macOS-only in Phase A — `rumps` lives in the optional `macos`
  extra): `uv run reachy-ducky-menubar`.
- Reachy app: one-click install from the on-robot dashboard after publishing
  via HF Spaces. The publish path is **unverified** in Phase A — see
  `docs/testing/2026-04-21-phase-a-e2e-procedure.md` Known gaps §3. For
  running the app locally during development, see `app/README.md`.
- Wake-word detection is a no-op stub in Phase A (`MockWakeDetector`); saying
  "Hey Ducky" does nothing today. The smoke procedure below shows how to
  exercise a turn without wake.

See `docs/testing/2026-04-21-phase-a-e2e-procedure.md` for the full end-to-end
smoke procedure and known gaps.

## Development

- `uv sync --all-packages --group dev` — install the workspace plus dev tools
  (pytest, ruff, mypy, bandit). This also installs `reachy-mini` on every
  platform, so the SDK class-surface contract tests run in the default tier.
- `uv run pytest -q` — unit tests (default tier).
- `uv run pytest -m integration` — integration tests, opt-in; require live API
  env vars (`OPENAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, etc.).
- `uv run pytest -m hardware` — hardware tests, opt-in; require a connected
  Reachy Mini.

See `CLAUDE.md` for the full developer workflow (lint, type-check, pre-commit).

## Networking & auth

Reachy Ducky runs as two processes on two machines: a daemon on your Mac and an
app on your Reachy Mini. They talk over HTTP. The default bind is
`127.0.0.1:8765` (loopback-only), which won't work across devices — so for
real use you need a trusted channel.

### Recommended: Tailscale (zero-trust mesh)

1. Install Tailscale on your Mac and on your Reachy (`brew install tailscale` /
   `curl -fsSL https://tailscale.com/install.sh | sh`). Run `sudo tailscale up`
   on both. Both devices appear in your tailnet with MagicDNS names like
   `<your-mac>.<your-tailnet>.ts.net` (example only — substitute your own).
2. On the Mac, run the daemon bound to all interfaces so the tailnet can
   reach it:
   ```bash
   REACHY_DUCKY_DAEMON_HOST=0.0.0.0 uv run reachy-ducky-daemon
   ```
   Tailscale's ACLs (and your firewall) restrict who can reach :8765 — only
   devices on your tailnet.
3. On the Reachy, point the app at the Mac's MagicDNS name (and set the
   OpenAI key the Realtime voice needs — see `app/README.md`):
   ```bash
   DAEMON_URL=http://<your-mac>.<your-tailnet>.ts.net:8765
   OPENAI_API_KEY=sk-...
   ```

No shared secret, no token rotation. If a device is lost, revoke it in the
Tailscale admin console.

### Alternative: bearer token over LAN

If you'd rather not use Tailscale, you can run the daemon with a shared token.

1. On the Mac:
   ```bash
   export REACHY_DUCKY_AUTH_TOKEN="$(openssl rand -hex 32)"
   export REACHY_DUCKY_DAEMON_HOST=0.0.0.0
   uv run reachy-ducky-daemon
   ```
2. On the Reachy, set the same token and point at the Mac:
   ```bash
   DAEMON_URL=http://<your-mac>.local:8765
   DAEMON_AUTH_TOKEN=<paste the same token>
   OPENAI_API_KEY=sk-...
   ```
3. The daemon requires `Authorization: Bearer <token>` on every route except
   the three open paths `/health`, `/docs`, and `/openapi.json`. Treat the
   token like a password — do not commit it.

### Warning

If you bind the daemon to a non-loopback host **without** setting
`REACHY_DUCKY_AUTH_TOKEN`, the daemon logs a loud warning on startup
(`AppConfig.warn_if_exposed_without_auth` in
`daemon/src/reachy_ducky_daemon/config.py`). Anyone on the same network could
invoke your Claude subscription and read your code. Don't run exposed without
at least one of Tailscale or a token.

`/docs` and `/openapi.json` are always open for discoverability, even with a
token — acceptable inside Tailscale; reconsider if you ever bind publicly.

For vulnerability reporting, see `SECURITY.md`. Python 3.12+ is required
(workspace-wide). Licensed under Apache 2.0 — see `LICENSE`.
