# Pattern B Redesign — Decision Record

**Date:** 2026-04-22
**Status:** Approved; supersedes the original tool-belt design in §7 of the design doc and Milestones 2/3/5 of the Phase A plan.
**Trigger:** Mid-Milestone-3 stress-test by user: "Are there standard tools the Claude Agent SDK offers we could use here? Rather than reinvent the wheel."

## TL;DR

We were building handcrafted Python wrappers (`GitTool`, `GhTool`, `FsTool`, `PlansTool`) for the daemon's read-only tool surface. Two parallel research waves confirmed this is reinventing the wheel for the brain's exploratory queries — but is correct for deterministic specialists. We pivot to a **hybrid architecture**:

- **The brain (`/brain/query`)** gets the SDK's built-in tools + official MCP servers (Pattern B — Claude orchestrates).
- **Specialists (`PlanReviewer`, etc.)** stay Python-orchestrated workflows that pre-assemble context and invoke a constrained `AgentDefinition` subagent (Pattern A — predictable steps).

This aligns with Anthropic's own [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) guidance: workflow when steps are predictable, agent when open-ended.

## Why we pivoted

The original design's tool wrappers reproduce capabilities the SDK and MCP ecosystem already provide:

| Original handcrafted | Already exists upstream |
|---|---|
| `GitTool` (subprocess + allowlist) | Built-in `Bash` tool; gated via `PreToolUse` hook with command allowlist (`mcp-server-git` is still labeled "early development") |
| `GhTool` | [`github/github-mcp-server`](https://github.com/github/github-mcp-server) — official, 29k stars, native `--read-only` flag, granular `--toolsets` |
| `FsTool` | Built-in `Read` / `Glob` / `Grep`; scoped via `cwd` + `add_dirs` + `PreToolUse` hook for path/secret blocklist |
| `PlansTool` | No upstream equivalent — but belongs in an **in-process MCP server** (`create_sdk_mcp_server` + `@tool`), not a Python pre-assembler |

Maintaining ~400 LOC of subprocess wrappers + tests forever, when Anthropic ships first-class equivalents, is the kind of "stay close to the SDK" anti-instinct that earlier feedback flagged.

## Research findings that informed the call

### What the SDK actually exposes (Wave 1)

- `claude-agent-sdk` 0.1.64 ships the same toolset as Claude Code: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `Task`/`Agent` (subagent dispatch), and others.
- **The real toolset restrictor is `ClaudeAgentOptions.tools=[...]`, not `allowed_tools=[...]`.** `allowed_tools` is an auto-approve rule; non-listed tools remain visible and callable. (Open issue [#361](https://github.com/anthropics/claude-agent-sdk-python/issues/361).)
- **Locked-down idiom:** `permission_mode="dontAsk"` + `tools=[...]` + `disallowed_tools=["Write","Edit","Bash"]` (or whatever inversion of the surface) as belt-and-suspenders.
- **`AgentDefinition`** is the SDK's first-class subagent construct: fixed prompt, restricted tools, scoped context.
- **`create_sdk_mcp_server` + `@tool` decorator** lets us define an in-process MCP server in Python without spawning an external process — perfect home for project-specific helpers like `find_plans`.

### Pattern B viability stress-test (Wave 2)

- **Anthropic's guidance still says workflow first.** "Find the simplest solution possible, and only increase complexity when needed." Read-only investigators with predictable steps belong in workflows.
- **Reliability concern:** non-determinism is real and well-measured. Same prompt, different tool sequences across runs. Reliability degrades super-linearly with task complexity. → For specialists where step order is load-bearing, keep orchestration in Python.
- **Token overhead:** ~10-15k tokens/turn for MCP tool definitions even before the query. Caching mitigates but first-query-per-session is slow.
- **Latency:** 5-step investigator query = 5-15s end-to-end. Marginal for a "desk companion replies in seconds" UX.
- **Testability:** no mature native harness. Live Claude calls are needed for meaningful brain tests; specialists with deterministic Python can stay unit-testable.
- **Distribution friction:** community contributors need Node 20+ for `npx github-mcp-server` + a GitHub token.
- **No clearly scaled production reference app** for "persistent daemon, MCP-driven investigator" — we'd be early.

### Sources
- [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
- [Agent SDK Permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
- [Agent SDK Hooks](https://code.claude.com/docs/en/agent-sdk/hooks)
- [Agent SDK MCP](https://code.claude.com/docs/en/agent-sdk/mcp)
- [Agent SDK Subagents](https://code.claude.com/docs/en/agent-sdk/subagents)
- [github/github-mcp-server](https://github.com/github/github-mcp-server)
- [modelcontextprotocol/servers - git](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
- [modelcontextprotocol/servers - filesystem](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md)
- [Issue #361 — `allowed_tools` ignored](https://github.com/anthropics/claude-agent-sdk-python/issues/361)

## New architecture

### The brain (`/brain/query`) — Pattern B

`ClaudeSDKBrain` evolves from a no-tools text-stream wrapper into a configured agent.

```python
# Conceptual shape (final code in M2 of the revised plan)
class ClaudeSDKBrain(BrainInterface):
    def __init__(
        self,
        *,
        cwd: Path,
        memory_root: Path,
        github_repo: str | None = None,   # for github-mcp-server scoping
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._options = ClaudeAgentOptions(
            tools=[
                "Read", "Glob", "Grep",          # built-in, scoped by cwd + add_dirs
                "Bash",                           # gated by PreToolUse hook (git read-only allowlist)
                "mcp__github__*",                 # github-mcp-server --read-only
                "mcp__plans__*",                  # in-process plans MCP
                "Task",                           # so brain can dispatch its own subagents
            ],
            disallowed_tools=["Write", "Edit"],   # belt-and-suspenders; never write code
            permission_mode="dontAsk",
            cwd=cwd,
            add_dirs=[memory_root],
            mcp_servers={
                "github": {"command": "npx", "args": ["-y", "github-mcp-server", "--read-only", ...]},
                "plans": create_sdk_mcp_server(name="plans", tools=[find_plans, read_plan]),
            },
            hooks={
                "PreToolUse": [HookMatcher(matcher="Bash|Read|Glob|Grep", hooks=[security_gate])],
            },
            system_prompt=system_prompt,
            model=model,
        )
```

The `security_gate` hook:
- For `Bash`: rejects any command not matching the git-read-only allowlist regex (`git status|diff|log|show|branch|rev-parse|ls-files|ls-tree|describe|rev-list`).
- For `Read`/`Glob`/`Grep`: rejects any path matching the secret blocklist (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets/**`, `credentials*`).

### Specialists — Pattern A (workflow)

`PlanReviewer` (and future specialists) stay deterministic:

```python
class PlanReviewer:
    async def review(self) -> SpecialistResponse:
        # 1) Python pre-loads context — deterministic, testable
        plan_text = self._read_all_plans()           # subprocess git ls-files + read
        diff = self._git_diff_against_main()         # subprocess git diff
        # 2) Dispatch a constrained subagent
        agent_def = AgentDefinition(
            description="Plan-vs-implementation drift reviewer",
            prompt="ALWAYS read the plan and diff above. Compare. Report drift only.",
            tools=["Read", "Grep", "Glob"],          # for follow-up reads if needed
            model="claude-sonnet-4-6",
        )
        resp = await self._brain.query_with_agent(
            agent=agent_def,
            user_prompt=f"## Plans\n{plan_text}\n\n## Diff\n{diff}",
            max_turns=3,
        )
        return SpecialistResponse(name="plan-reviewer", summary=resp.text)
```

The Python side guarantees the plan + diff are *always* read; the subagent gets `Read`/`Grep`/`Glob` for follow-up exploration but cannot skip the pre-loaded context.

### What gets deleted

- `daemon/src/reachy_ducky_daemon/tools/git.py` (committed in `0ab8345`).
- `daemon/src/reachy_ducky_daemon/tools/__init__.py`.
- `daemon/tests/test_tools_git.py`.
- Plan tasks 3.2 (`GhTool`), 3.3 (`FsTool`), 3.4 (`PlansTool` as Python wrapper) are reshaped (see plan patches).

### What gets added

- `daemon/src/reachy_ducky_daemon/brain/security_gate.py` — the `PreToolUse` hook function (Bash allowlist + path blocklist).
- `daemon/src/reachy_ducky_daemon/brain/plans_mcp.py` — in-process MCP server via `create_sdk_mcp_server` exposing `find_plans` and `read_plan`.
- `daemon/src/reachy_ducky_daemon/brain/options.py` — `build_brain_options(...)` factory that assembles `ClaudeAgentOptions`.
- `.mcp.json` at repo root — declares the `github-mcp-server` config so contributors get it for free.
- `.github/workflows/integration.yml` — separate workflow for live-Claude tests; runs on PR with `integration` label and weekly cron; uses `CLAUDE_CODE_OAUTH_TOKEN` secret.

### What survives unchanged

- `BrainInterface` ABC.
- `MockBrain` test double.
- `protocol/` Pydantic messages (extra="forbid", frozen).
- `memory/` layout + Basic Memory MCP wiring (deferred, but config still valid).
- HTTP server scaffolding (M6) — endpoints unchanged.
- Reachy app side (M7-M11) — unchanged.

## Costs we accept

- **Token overhead per brain query:** ~10-15k tokens for tool definitions. Caching makes it tolerable; first-query-per-session is slower (cache miss). User has chosen to manage cost at the API-key level rather than per-call `max_budget_usd`.
- **Distribution friction:** contributors need Node 20+, `npx github-mcp-server`, and a GitHub token. Documented in README; mitigated by `.mcp.json` declaration.
- **Brain testability:** live Claude required for meaningful tests. Brain unit tests stay shape-only (mock brain); brain integration tests run live in a separate CI job using `CLAUDE_CODE_OAUTH_TOKEN`.
- **First reachy-mcp-driven Claude project of this shape:** no canonical reference app to crib from. Pioneer cost.

## Open follow-ups (not blocking the replan)

- The brain's first per-session call is slow (cache miss). Consider a daemon-startup warm-up ping. Defer to Phase B.
- `.mcpb` (MCP Bundle) format is the future for one-click MCP installs but Claude Code support is still rolling out (April 2026). Revisit when broadly supported.
- `pytest-claude-agent-sdk` (community spy framework) for shape assertions on tool-call sequences. Add when brain tests grow beyond "did it return text."
