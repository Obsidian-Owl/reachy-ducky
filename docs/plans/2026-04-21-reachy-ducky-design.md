# Reachy Ducky — Design Doc

**Date:** 2026-04-21
**Status:** Design complete; ready for implementation planning
**Author:** Dan McCarthy (with Claude, via superpowers:brainstorming)

## 1. Goal

A Reachy Mini app that is a personal, embodied "rubber ducky" for development work — a desk companion that watches the user code (with read-only access), answers questions, and flags concerns during SDD-style agentic software engineering flows (Claude Code, Codex CLI).

**Does not write code.** Read-only observer + conversational partner.

## 2. User & context

- Single user, across many projects.
- Heavy SDD workflow: plans/specs in the branch, then implementation, then tests, then PR.
- Primary agentic harnesses: Claude Code and Codex CLI.
- Target hardware: Reachy Mini Wireless (RPi CM4, 4-mic array, 6-DOF head, 2 antennas, body yaw, speaker, camera; no public LED-eye API).
- Mac is always present — the user's dev machine. Ducky leans on it.

## 3. Architecture (high level)

```
┌──────────────────────────┐          ┌──────────────────────────┐
│   Reachy Mini (CM4)      │          │      User's Mac          │
│                          │          │                          │
│  ┌────────────────────┐  │   LAN    │  ┌────────────────────┐  │
│  │  Reachy app        │◀─┼──────────┼─▶│  Ducky daemon      │  │
│  │  (Python, HF Space)│  │          │  │  (Python)          │  │
│  │  - voice I/O       │  │          │  │  - thinking brain  │  │
│  │  - motion/LEDs     │  │          │  │  - subagents       │  │
│  │  - wake / PTT      │  │          │  │  - memory          │  │
│  │  - hard mute       │  │          │  │  - read-only tools │  │
│  └────────────────────┘  │          │  └────────────────────┘  │
│                          │          │           │              │
└──────────────────────────┘          │           ▼              │
                                      │  ┌────────────────────┐  │
                                      │  │  Claude Agent SDK  │  │
                                      │  │  (default)         │  │
                                      │  │  via OAuth or API  │  │
                                      │  └────────────────────┘  │
                                      └──────────────────────────┘
                                                  │
                                                  ▼
                                             Claude API
```

**Split-brain rationale:**

- **Voice brain** (on Reachy, via the Reachy app): handles ears, mouth, turn-taking, barge-in, small talk. Uses a realtime model (OpenAI Realtime by default) over `fastrtc`. Pay-per-token; fast; vendor optimized for audio.
- **Thinking brain** (on the Mac daemon): Claude Agent SDK. Invoked as a tool by the voice brain when a question needs real reasoning over code, plans, diffs, or PRs. Benefits from prompt caching and the user's Claude Max subscription (or API key — see §5).

The Mac daemon holds the code, the memory, the subagents, and the subscription auth. The robot holds the body.

## 4. Interaction model (dual-mode)

- **Conversational mode (B):** user summons Ducky, they talk. On-demand.
- **Passive observer mode (C):** Ducky watches and pipes up — LED-indicator/motion/voice escalation tied to severity (see §9).

Built incrementally: phase A ships conversational only; B adds event-driven observation; C adds Claude Code / Codex hook integration.

## 5. Thinking brain — pluggable

**Default:** Claude Agent SDK (Python), called from the Mac daemon. Strong SWE reasoning, MCP ecosystem, prompt caching, first-class subagents, hooks.

**Pluggable by design.** `BrainInterface` abstraction so Codex CLI can be swapped in. Motivated by (a) the user uses both, (b) making the app re-usable for the Reachy Mini community.

**Brain is a configured agent, not a text wrapper.** `ClaudeSDKBrain` constructs `ClaudeAgentOptions` with:
- **Tools:** built-in `Read` / `Glob` / `Grep` (scoped via `cwd` + `add_dirs`) + `Bash` (gated by `PreToolUse` hook) + `mcp__github__*` (from `github/github-mcp-server --read-only`) + `mcp__plans__*` (in-process MCP via `create_sdk_mcp_server`).
- **Lock-down:** `permission_mode="dontAsk"` + explicit `tools=[...]` (the real restrictor) + `disallowed_tools=["Write","Edit"]` belt-and-suspenders against [SDK issue #361](https://github.com/anthropics/claude-agent-sdk-python/issues/361) where `allowed_tools` is just an auto-approve rule.
- **PreToolUse security hook:** rejects any `Bash` not matching the git read-only allowlist; rejects any `Read`/`Glob`/`Grep` path matching the secret blocklist (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets/**`, `credentials*`).

See [`docs/plans/2026-04-22-pattern-b-redesign.md`](2026-04-22-pattern-b-redesign.md) for the full rationale and trade-off analysis.

### Auth posture

- **Personal use:** OAuth via locally-installed Claude Code (`claude login`). Claude Agent SDK inherits credentials. Bills against Claude Max subscription. Mechanically works.
- **Policy grey zone:** Anthropic docs say the Agent SDK "should use API key authentication" for product-shaped use. Solo/individual use reads as "ordinary, individual usage" and is permitted.
- **Phase-C daemon mode** (always-on passive observation) is the most enforcement-visible pattern. Re-evaluate ToS fit before enabling.
- **Community distribution:** default to API key in the distributed config; OAuth-via-CC is an opt-in "personal use only" path documented clearly in README.

## 6. Voice brain — pluggable

**Default:** OpenAI Realtime (`gpt-realtime`) over `fastrtc`. Reference Reachy conversation app uses this; mature turn-taking, barge-in, streaming STT+TTS.

**Pluggable:** `VoiceInterface` abstraction so Gemini Live can be swapped in later (enables native multimodal if we ever wire the camera for vision).

**Stitching** (all assembled by us — SDK ships no primitives):

- Wake word: community Space model (`fcollonval/reachy_mini_wake_word` or `luisomoreau/hey_reachy_wake_word_detection`)
- VAD / barge-in: whatever the realtime model provides
- Hard-mute: local gate on the mic stream; honored before any frame leaves the device
- Interruption: mid-TTS cancellation via the realtime API's own primitives

## 7. Observation layers

Read-only tool surface assembled from SDK built-ins + official MCP servers + a project-specific in-process MCP + a `PreToolUse` security-gate hook. **No handcrafted Python subprocess wrappers.** See the [Pattern B redesign](2026-04-22-pattern-b-redesign.md) for why.

- **Git:** built-in `Bash` tool, gated by a `PreToolUse` hook that rejects any command not matching the read-only allowlist (`git status|diff|log|show|branch|rev-parse|ls-files|ls-tree|describe|rev-list`). Chosen over `mcp-server-git` because that server is still labeled "early development; subject to change."
- **GitHub:** [`github/github-mcp-server`](https://github.com/github/github-mcp-server) (official, 29k stars) launched with `--read-only` + `--toolsets pull_requests,issues,actions,repos`. Spawned via `npx -y` and declared in `.mcp.json` so contributors get it for free.
- **Filesystem:** built-in `Read` / `Glob` / `Grep`, scoped to the project via `cwd` + `add_dirs`. Same `PreToolUse` hook that gates Bash also rejects any path matching the secret blocklist (`.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets/**`, `credentials*`).
- **Plan/spec discovery:** in-process MCP server built with `create_sdk_mcp_server(name="plans", tools=[...])` — exposes `find_plans` (returns paths under `docs/plans/**`, `specs/**`, root `AGENTS.md` / `CLAUDE.md` / `SPEC.md`, `*.plan.md`) and `read_plan(path)`. Project conventions belong in the tool surface, not in a Python pre-assembler.

**Specialists** (PlanReviewer, etc.) follow Pattern A — Python pre-loads deterministic context (e.g., the diff via subprocess `git diff`), then dispatches a constrained `AgentDefinition` subagent (`tools=["Read","Grep","Glob"]`, `max_turns=3`) for follow-up reads. Step ordering you can't trust the agent to follow stays in Python.

### Phase progression

- **Phase A (MVP):** snapshot-on-demand. User invites Ducky into context ("look at what I'm working on"); Ducky reads current state via the tool belt.
- **Phase B:** event-driven. `fswatch` / `chokidar` on watched repos + git-ref polling. Pipes up when branches gain commits, tests change, PRs get comments.
- **Phase C:** deep session integration. Hook into Claude Code SessionStart / PostToolUse / Stop events and Codex CLI equivalents so Ducky sees agent tool actions as they happen. Enables `agent-trace-critic`.
- **Phase D (deferred, opt-in):** transcript ingestion (Claude Code JSONL). Highest signal, highest noise, biggest privacy surface. Not in MVP roadmap; gated behind explicit opt-in per project.

## 8. Memory architecture

**Three-layer, lean.**

```
~/reachy-ducky/memory/
  ducky/
    soul.md              # Ducky's identity, stances, running threads (SOUL.md pattern)
    core-blocks/         # 2–3 editable "core blocks" Ducky rewrites itself
      stances.md
      running-jokes.md
      open-threads.md
  human/
    user.md              # Dan: role, preferences, working style
    feedback.md          # accumulated corrections + validated approaches
    preferences.md
  projects/<slug>/
    project.md
    people.md
    decisions.md
    concerns.md          # what Ducky is currently worried about
    branches/<branch>.md # ephemeral per-branch notes
```

Plus **ephemeral working-set memory** via Claude Agent SDK's `BetaAbstractMemoryTool` — scoped per branch/worktree, compacted/wiped on branch switch.

### Memory backends

- **Basic Memory MCP** (Markdown + SQLite + FastEmbed, Obsidian-compatible) serves the Markdown hierarchy — gives semantic recall for free while keeping files human-readable and git-able.
- **SOUL.md pattern** for `ducky/soul.md` — the agent edits it via tool calls, loaded into every system prompt.
- **Graphiti (temporal KG)** deferred — revisit if cross-project temporal recall starts hurting. Adds a Neo4j/FalkorDB dependency that's over-engineered for a single-user companion today.

### Project switching

- **Auto-detect** from the active Claude Code / Codex git repo, with **user override** ("pin to project X", "switch to project Y").
- On switch: Ducky preloads `ducky/`, `human/`, `projects/<current>/` only. Other projects stay cold.
- Small embodiment cue on switch (motion + menu-bar color shift — see §11).

### Memory writes

- **Ducky autonomous writes** for low-stakes observations (running threads, feedback patterns, project facts learned in conversation). Follows the "auto memory" pattern — implicit unless high-stakes.
- **Explicit user writes** ("remember that X", "forget Y") always honored.
- **High-stakes writes** (changing a stated preference, overwriting a decision) — Ducky asks before committing. "I think you now prefer X over Y — update the memory?"

## 9. Subagent specialists & interruption policy

### Roster

Read-only, dispatched from the main brain when a question warrants deep analysis. Each is an `AgentDefinition` (SDK first-class subagent) with restricted `tools=["Read","Grep","Glob"]` and a small `max_turns` budget; each is invoked by a Python wrapper that pre-loads load-bearing context (so step ordering doesn't depend on agent compliance).

- **plan-reviewer** — reads branch plan/spec + current diff, flags deviations.
- **test-gap-assessor** — reads plan + new code + tests, reports behaviors not covered, or tests that don't match the plan's concerns.
- **scope-creep-detector** — flags changes outside the plan's stated scope.
- **pr-reviewer** — reads PR diff + comments + CI state, summarizes risk and open threads.
- **agent-trace-critic (phase C)** — reads recent Claude Code/Codex tool actions, flags "your agent just did X, was that intentional?" moments.

### Interruption policy (phase C)

**Ambient-first, severity-tiered, per-project configurable.**

Three escalation levels:

| Level | Channel | Triggers |
|---|---|---|
| Ambient hint | `play_move("curious")` + menu-bar color shift | CI finished, a forming thought, low-confidence observation |
| Soft nudge | Head tilt + short verbal at a natural pause | Plan/diff drift, scope creep, test gap on new behavior, new PR comment on active branch |
| Urgent interrupt | `play_move("surprised")` + antenna twitch + speaks over anything | Destructive not-in-plan action (`rm`, `git reset --hard`, force push, `--no-verify`, deleted tests), secret pattern in diff, irreversible shared-state action |

**Focus mode:** user says "shush" or "heads down" → only urgent survives; soft-nudge and ambient queue silently until user asks.

**Per-project overrides:** production repos = strict ambient-only except urgent; hobby repos = chattier.

## 10. Privacy / redaction

- **Repo allowlist.** Ducky reads only repos the user opts in. New repos require explicit "watch this project" onboarding; unknown repos are invisible.
- **Secret redaction pre-send (landed 2026-04-23).** `gitleaks stdin` runs over every specialist-assembled prompt before it reaches `brain.query()`; matches are spliced with `[REDACTED:<RuleID>]` and surface as `redacted:<rule_id>` flags on the `SpecialistResponse`. Fail-closed: a broken `gitleaks` install aborts the review with a `redaction-failed` flag — no brain call fires. Shared helper: `daemon/src/reachy_ducky_daemon/specialists/redaction.py`. Gitleaks config precedence mirrors lefthook's pre-commit hook, so "what's a secret" stays unified across commit-time and brain-time. See `docs/plans/2026-04-23-secret-redaction-specialists.md` and closed issue #50.
- **Hard blocklist by default:** `.env*`, `*.pem`, `*.key`, `id_rsa*`, `secrets/**`, `credentials*`. Extensible per-project.
- **Transcript ingestion (phase D)**: opt-in per project, never default.

## 11. Embodiment & wake UX

### Wake / attention

- **Wake word** ("Hey Ducky") via community Space model. Hands-free conversational mode.
- **Push-to-talk** — Mac keyboard shortcut + menu-bar app. Zero false triggers.
- **Hard mute** — local mic gate with a visible indicator. Unambiguous "I'm not listening."
- Muted state = `goto_sleep()` + optional `disable_motors()`, menu-bar shows muted glyph.

### State → embodiment mapping (SDK-validated)

| State | Motion | Menu bar | Notes |
|---|---|---|---|
| Idle | DIY slow breathing loop (low-freq head Z oscillation) | neutral | No public idle/breathing primitive; author on background thread |
| Listening | `play_move("listening")` + `look_at_image(face_pixel)` | "listening" | Face-tracking is DIY via MediaPipe + `look_at_image` |
| Thinking | `play_move("thinking")` + slow head sway | "thinking" | Background sway via `set_target` |
| Ambient hint | `play_move("curious")` + optional audio chime | amber dot | Low-bandwidth "I have a thought" |
| Soft nudge | `create_head_pose(roll=10°)` tilt toward user | amber dot | Precedes verbal |
| Urgent | `play_move("surprised")` + antenna twitch | red dot | Precedes urgent verbal |
| Muted | `goto_sleep()` (+ optionally `disable_motors()`) | muted glyph | Unambiguous off |
| Project switch | Custom authored move | per-project color | Custom move recorded via SDK |

**Menu-bar app** is the primary visual signaling channel because the SDK has no LED-eye API. Always reliable, at-a-glance, redundant with motion.

### SDK primitives we'll use

`ReachyMini()`, `set_target`, `goto_target`, `look_at_world`, `look_at_image`, `create_head_pose`, `play_move`, `async_play_move`, `goto_sleep`, `wake_up`, `set_target_antenna_joint_positions`, `get_current_head_pose`, `mini.media.get_frame()`, `mini.media.get_audio_sample`, `mini.media.push_audio_sample`, `mini.media.get_DoA`, `mini.imu`.

### Emotion library

Pre-recorded moves in `pollen-robotics/reachy-mini-emotions-library` reused wherever possible: `listening, thinking, curious, agreeing, uncertain, focused, surprised, neutral`.

## 12. MVP cutline (phase A)

Ship-first scope, aimed at roughly one to two weeks of focused work.

1. **Mac daemon skeleton.** Python. Pluggable `BrainInterface` (Claude Agent SDK default, OAuth via locally-installed `claude`).
2. **Basic Memory MCP** integration + SOUL.md seed + initial `human/` and one `projects/<slug>/` tree.
3. **Read-only tool belt:** git, `gh`, filesystem reads, plan/spec readers.
4. **One specialist:** `plan-reviewer` (highest-signal for SDD).
5. **Reachy Mini app skeleton.** `VoiceInterface` with OpenAI Realtime + `fastrtc`. Wake word from community Space. Hard-mute with menu-bar indicator. On-demand conversational mode only.
6. **Embodiment minimum:** `play_move` on state transitions (listening/thinking/muted), `look_at_image` gaze to user. No idle breathing yet; no urgent/soft-nudge tiers yet (that's phase C).

**Deferred to phase B:** `fswatch`/git-ref event watcher, specialists `test-gap-assessor` and `scope-creep-detector`.

**Landed in phase B (2026-04-23):** `pr-reviewer` — see `docs/plans/2026-04-23-pr-reviewer-specialist.md`.

**Deferred to phase C:** Claude Code / Codex hooks, `agent-trace-critic`, interruption policy with severity tiers, per-project overrides.

**Deferred indefinitely:** phase D transcript ingestion, Graphiti temporal KG, ESP32 eye mod, camera-based vision (Gemini Live swap).

## 13. Known gaps & open questions

- **No public LED-eye API** in SDK v1.6.3 — we substitute with motion + menu bar. Power users can add the `algoryn-nl/reachy-mini-esp32-eyes` community hardware mod; we'll document it as optional.
- **No public motion-blender primitive** — overlapping motions overwrite, not superimpose. Idle breathing + head-tracking + speech-reactive wobble (all live in the reference conversation app as app-internal code) we'll have to approximate ourselves.
- **Face tracking is DIY** — use MediaPipe + `look_at_image`, borrow patterns from `reachy_mini_toolbox` and `hand_tracker_v2`.
- **Thermal / 24-7 reliability on CM4** — undocumented by Pollen. Validate on hardware before committing UX to always-on listening.
- **Anthropic ToS grey zone for OAuth+daemon.** Personal use should be fine; distributed version defaults to API key. Re-check policy before enabling phase C always-on.
- **No Reachy-community Claude-native reference app exists.** We'd be first — also means less prior art to borrow from.

## 14. Distribution

- **Two artifacts:**
  1. `reachy-ducky-daemon` — Python package running on the user's Mac. Installed via `pip`. Default brain = Claude Agent SDK; `codex` backend as opt-in.
  2. `reachy-ducky-app` — Reachy Mini app, published to Hugging Face Spaces following `reachy-mini-app-assistant create|publish`. Installed one-click from the on-robot dashboard.
- **License:** Apache 2.0 (matches the Reachy ecosystem and Basic Memory's permissiveness).
- **Repo:** single monorepo (`reachy-ducky/`) with `daemon/` and `app/` subpackages; shared `protocol/` for the daemon ↔ app wire.

## 15. References

- Reachy Mini SDK: https://github.com/pollen-robotics/reachy_mini
- Reachy Mini docs: https://huggingface.co/docs/reachy_mini/index
- Reference conversation app: https://github.com/pollen-robotics/reachy_mini_conversation_app
- Emotion library: https://huggingface.co/datasets/pollen-robotics/reachy-mini-emotions-library
- Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview
- Claude Code legal/compliance: https://code.claude.com/docs/en/legal-and-compliance
- Basic Memory: https://docs.basicmemory.com
- SOUL.md pattern: https://moto-westai.github.io/blog/2026/02/21/the-soul-md-pattern/
- Graphiti (deferred): https://github.com/getzep/graphiti
- Wake-word community Spaces: https://huggingface.co/spaces/fcollonval/reachy_mini_wake_word, https://huggingface.co/spaces/luisomoreau/hey_reachy_wake_word_detection
