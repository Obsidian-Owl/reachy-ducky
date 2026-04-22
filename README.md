# Reachy Ducky

A personal, embodied "rubber ducky" development companion for Reachy Mini.
Read-only desk robot that watches your agentic SWE workflow and talks with you.

See `docs/plans/2026-04-21-reachy-ducky-design.md` for the full design.

Status: Phase A MVP in progress.

## Run (Phase A)

- Mac daemon: `uv run reachy-ducky-daemon` (first run: `uv run reachy-ducky init`
  to write `~/.reachy-ducky/config.toml` + `.env`).
- Menu-bar (macOS only): `uv run reachy-ducky-menubar`. On Linux this raises
  `ImportError` by design — `rumps` lives in the optional `macos` extra.
- Reachy app: one-click install from the on-robot dashboard after publishing
  via HF Spaces. The publish path is **unverified** in Phase A — see
  `docs/testing/2026-04-21-phase-a-e2e-procedure.md` Known gaps §3.
- Wake-word detection is a no-op stub in Phase A (`MockWakeDetector`); saying
  "Hey Ducky" does nothing today. The smoke procedure above shows how to
  exercise a turn without wake.

See `docs/testing/2026-04-21-phase-a-e2e-procedure.md` for the full end-to-end
smoke procedure and known gaps.

## Development

- `uv sync --all-packages --group dev` — install the workspace plus dev tools
  (pytest, ruff, mypy, bandit). On the robot only, add `--extra robot` to pull
  `reachy-mini`.
- `uv run pytest -q` — unit tests (default tier).
- `uv run pytest -m integration` — integration tests, opt-in; require live API
  env vars (`OPENAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, etc.).
- `uv run pytest -m hardware` — hardware tests, opt-in; require a connected
  Reachy Mini.

See `CLAUDE.md` for the full developer workflow (lint, type-check, pre-commit).

Licensed under Apache 2.0.
