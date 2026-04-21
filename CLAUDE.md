# Reachy Ducky — Development Guide

**For**: Claude Code and human collaborators. Keep this file short; link out to rules.

---

## Quick Start

```bash
uv sync --all-packages --group dev   # install workspace + dev tools
uv run reachy-ducky init             # first-run wizard → ~/.reachy-ducky/{config.toml,.env}
uv run pytest -q                     # unit tests (default tier)
uv run ruff check --fix .            # lint + autofix
uv run ruff format .                 # format
uv run mypy --strict daemon/src app/src menubar/src protocol/src
uv run bandit -r daemon/src app/src menubar/src protocol/src -ll
lefthook run pre-commit --all-files
npx gitnexus analyze --skip-agents-md   # refresh code index (incremental by default)
```

First-time daemon setup is `uv run reachy-ducky init` — it prompts for daemon host/port, an optional auth token, an optional GitHub PAT, and at least one project (slug + git-repo path + optional `github_repo`). Writes `~/.reachy-ducky/config.toml`, a 0600 `~/.reachy-ducky/.env` for any secrets, and seeds the memory tree. Re-running is safe: the wizard detects existing config and asks before overwriting.

On the robot only: `uv sync --all-packages --extra robot` additionally installs `reachy-mini` from the app package's `robot` extra.

### Prereqs for the daemon's Pattern B brain

The thinking brain spawns external MCP servers:

- **Node.js 20+** — required for `npx -y github-mcp-server` (declared in `.mcp.json`).
- **`GITHUB_PERSONAL_ACCESS_TOKEN`** env var — `repo:read` + `pull_requests:read` + `issues:read` scopes are sufficient for read-only operation.
- **`REACHY_DUCKY_AUTH_TOKEN`** — bearer token if exposing the daemon over LAN; see `.claude/rules/` and the design doc §5.

Live-Claude integration tests run via `.github/workflows/integration.yml` (PR-with-`integration`-label or weekly cron) using the `CLAUDE_CODE_OAUTH_TOKEN` org secret.

Integration tests (live Claude / OpenAI / HF): `uv run pytest -m integration` with relevant env vars.
Hardware tests (Reachy Mini): `uv run pytest -m hardware`, requires connected robot.

---

## Project

A read-only rubber-ducky development companion for Reachy Mini Wireless.
Split-brain: realtime voice on the robot, Claude Agent SDK thinking brain
on a Mac daemon. See `docs/plans/2026-04-21-reachy-ducky-design.md` for
the canonical design and `docs/plans/2026-04-21-reachy-ducky-phase-a-plan.md`
for the Phase A implementation plan.

## Layout

```
daemon/      Mac-side service: brain, tools, memory, specialists, HTTP server
app/         Reachy Mini side: voice, wake, mute, embodiment
menubar/     Mac menu-bar status + mute toggle
protocol/    Shared Pydantic messages between daemon ↔ app
```

## Core Principles

1. **Read-only.** This app observes; it does not write user code. Tools enforce this.
2. **TDD.** Write the failing test, watch it fail, implement, watch it pass, commit.
3. **Stay close to upstream SDKs.** Do not invent abstractions the SDK doesn't need.
4. **Pluggable where it earns community re-use** (brain, voice, motion). Not elsewhere.
5. **Escalate design choices.** When there's more than one valid approach, stop and ask — do not silently choose.

---

## Code Intelligence Tools

| Step | Tool | When |
|------|------|------|
| 1 | `Grep` | Known symbol or string |
| 2 | `GitNexus impact()` | Before editing shared contracts: `BrainInterface`, `VoiceInterface`, `MotionDriver`, `protocol/*` |
| 3 | `GitNexus cypher()` | "All implementors of X" |
| 4 | `GitNexus detect_changes()` (MCP) | Check scope of unstaged/staged diffs during editing |

**Always pass `repo: "reachy-ducky"`** to every GitNexus MCP call.

**Do NOT use routinely**: `gitnexus_query()`, `gitnexus_context()` — noisy and token-expensive. See `.claude/rules/tool-selection.md`.

**Auggie**: deferred until repo grows. Not wired.

---

## Rules (progressive disclosure — read when domain applies)

- `.claude/rules/quality-escalation.md` — **READ FIRST** for any design decision
- `.claude/rules/tool-selection.md` — full tool ladder + anti-patterns
- `.claude/rules/python-standards.md` — type hints, Pydantic v2, ruff/mypy
- `.claude/rules/testing-standards.md` — TDD, no-skip, side-effect verification

---

## GitOps

- Feature branch per milestone: `m0-scaffold`, `m1-protocol`, …
- PR into `main`; CI must be green before merge.
- Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`).
- **No `--no-verify`. No force-push. No `git reset --hard` on shared branches.**

---

## Memory (daemon)

Runtime memory for Ducky lives at `$REACHY_DUCKY_MEMORY_ROOT` (default `~/.reachy-ducky/memory/`):
- `ducky/` — SOUL.md + core blocks (agent self, editable by Ducky)
- `human/` — user profile, feedback, preferences
- `projects/<slug>/` — per-project context + `branches/`

Served to the thinking brain via Basic Memory MCP. See design doc §8.
