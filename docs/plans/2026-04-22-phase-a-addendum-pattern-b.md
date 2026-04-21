# Phase A — Pattern B Addendum (revised Milestones 2, 3, 5)

> Supersedes the corresponding sections in `2026-04-21-reachy-ducky-phase-a-plan.md`. See `2026-04-22-pattern-b-redesign.md` for the design rationale.

## What's still in force from the original plan

- Milestone 0 (scaffolding) — done.
- Milestone 1 (protocol messages) — done.
- Milestone 2 Tasks 2.1, 2.2, 2.3 — done. `BrainInterface`, `MockBrain`, `ClaudeSDKBrain` (text-stream version), gated integration test all survive.
- Milestone 4 (memory layout + Basic Memory MCP config) — unchanged.
- Milestone 6 (FastAPI server + auth middleware + endpoints) — unchanged.
- Milestone 7 (menu-bar app) — unchanged.
- Milestones 8–12 (voice, wake, mute, embodiment, app wiring, smoke test) — unchanged.
- Milestone 13 (README + Tailscale auth doc) — unchanged.

## What changes

### Milestone 2 — extended (Task 2.4 new)

Tasks 2.1–2.3 stay as committed (`9d37984`, `3942761`, `36c9b43`). Add **Task 2.4** to extend `ClaudeSDKBrain` with the Pattern B configuration surface. The text-stream `query()` from 2.2 stays as the no-tools fallback; the new code path uses `ClaudeAgentOptions(tools=…, mcp_servers=…, hooks=…, permission_mode="dontAsk", disallowed_tools=["Write","Edit"])`.

### Milestone 3 — rewritten (replaces all 4 original tasks)

Original M3 tasks (`GitTool`, `GhTool`, `FsTool`, `PlansTool` as Python wrappers) are revoked. The first one was implemented in `0ab8345` and will be reverted before the new milestone begins.

New M3 tasks:

- **Task 3.1 (revised) — Revert GitTool.** Delete the daemon's `tools/` package and `test_tools_git.py`. One commit, clean subtraction. (Note for trail: only the implementer files are removed; `protocol/`, `memory/`, `brain/` survive.)
- **Task 3.2 — PreToolUse security gate hook.** New module `daemon/src/reachy_ducky_daemon/brain/security_gate.py`. Pure function `security_gate(input: HookInput) -> HookOutput` that:
  - For `Bash` calls: rejects any command not matching the read-only allowlist regex.
  - For `Read`/`Glob`/`Grep` calls: rejects any path matching the secret blocklist.
  Tested with table-driven unit tests against synthetic `HookInput` payloads. No live Claude needed.
- **Task 3.3 — In-process plans MCP server.** New module `daemon/src/reachy_ducky_daemon/brain/plans_mcp.py`. Uses `@tool` decorator + `create_sdk_mcp_server(name="plans", tools=[find_plans, read_plan])`. Tools are pure-Python (filesystem reads via stdlib `pathlib`); tested directly against tmp_path fixtures.
- **Task 3.4 — Brain options factory.** New module `daemon/src/reachy_ducky_daemon/brain/options.py`. Exports `build_brain_options(*, cwd, memory_root, github_repo=None) -> ClaudeAgentOptions` that assembles tools list + `mcp_servers` dict + hook wiring + system prompt. Tests are config-shape assertions (no live Claude).
- **Task 3.5 — `ClaudeSDKBrain` Pattern B integration.** Update `ClaudeSDKBrain` to accept and apply the brain options from 3.4 when constructed in "tools mode" (a flag or alternate constructor — TBD by implementer with escalation). The default text-stream path from Task 2.2 stays for backward-compat; the new tool-enabled path is opt-in. New tests use `MockBrain`-style spies on the SDK's tool dispatch.
- **Task 3.6 — `.mcp.json` declaration.** Add `.mcp.json` at repo root declaring `github-mcp-server` (`npx -y github-mcp-server --read-only --toolsets pull_requests,issues,actions,repos`). Update CLAUDE.md quick-start with Node 20+ prereq + `GITHUB_PERSONAL_ACCESS_TOKEN` env var note.

### Milestone 5 — rewritten (Task 5.1 hybrid)

Original Task 5.1's `PlanReviewer` shape (Python tool-belt + brain text query) is revoked. New shape:

- **Task 5.1 (revised) — `PlanReviewer` (hybrid: Python pre-load + AgentDefinition follow-up).** Python wrapper:
  - Reads all plan files via direct subprocess `git ls-files` + filesystem reads (deterministic; no LLM in the loop).
  - Captures the branch diff via subprocess `git diff main...HEAD` (deterministic).
  - Constructs an `AgentDefinition(description=..., prompt="ALWAYS read the plan and diff above. Compare. Report drift only.", tools=["Read","Grep","Glob"], max_turns=3, model="claude-sonnet-4-6")`.
  - Dispatches via the brain's subagent-execution path (`Task` tool primitive or whatever the SDK exposes).
  - Returns `SpecialistResponse(name="plan-reviewer", summary=...)`.
  Tests cover the deterministic Python pre-load step (full unit coverage) + a single integration test that runs live Claude (gated by env var, same shape as Task 2.3).

### CI workflow addition (independent of milestone numbering)

Add `.github/workflows/integration.yml` triggered on:
- PR with the `integration` label
- Weekly cron (`0 6 * * 1`)
- Manual `workflow_dispatch`

Uses `secrets.CLAUDE_CODE_OAUTH_TOKEN` to authenticate the SDK against the user's Claude Max subscription. Runs only the `@pytest.mark.integration` tier. Skips on every push to main (cost discipline).

## Task list (revised)

| # | Task | Status |
|---|---|---|
| 2.1 | BrainInterface + MockBrain | ✅ committed |
| 2.2 | ClaudeSDKBrain (text-stream) | ✅ committed |
| 2.3 | Gated integration test | ✅ committed |
| 2.4 | ClaudeSDKBrain config-surface extension | ⏳ deferred to after M3 (3.5 may absorb) |
| 3.1 (revised) | Revert GitTool | next |
| 3.2 | Security gate PreToolUse hook | next |
| 3.3 | In-process plans MCP server | next |
| 3.4 | Brain options factory | next |
| 3.5 | ClaudeSDKBrain Pattern B integration | next |
| 3.6 | `.mcp.json` + Node prereq docs | next |
| (ci) | `integration.yml` workflow with `CLAUDE_CODE_OAUTH_TOKEN` | next |
| 4.x | Memory (unchanged) | per original plan |
| 5.1 (revised) | PlanReviewer (hybrid) | per addendum |
| 6.x | Server (unchanged) | per original plan |
| 7–13 | App / menu-bar / smoke / docs (unchanged) | per original plan |

## Open implementation decisions

These will be flagged by implementers via `quality-escalation` when they hit them, not pre-decided here:

- **Task 3.5:** Should `ClaudeSDKBrain` get a second constructor (`ClaudeSDKBrain.with_tools(...)`) or a single constructor with an optional `options: ClaudeAgentOptions | None`? Both have trade-offs.
- **Task 5.1:** The SDK's exact API for dispatching an `AgentDefinition` from inside `query()` may differ between SDK versions. Implementer should inspect 0.1.64 and document.
- **Task 3.6 / `.mcp.json`:** GitHub PAT vs OAuth-via-`gh-cli`-helper. Defer to implementer to surface the cleanest config.
