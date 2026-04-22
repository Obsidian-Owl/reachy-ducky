# Session handoff — `pr-reviewer` specialist (Phase B)

> **For Claude:** paste the prompt below as the opening message of a new session to pick up this work with the right context. Memory should auto-load the project summary alongside.

**Written:** 2026-04-22 at the close of the Phase A → Phase B transition session (v0.1.0 cleanup closed, all 6 Dependabot PRs triaged, memory updated).

**Goal:** Start the `pr-reviewer` specialist — the next real feature, exercises Pattern B brain infrastructure end-to-end without hardware, gives v0.1.0 a second demonstrable capability before release plumbing.

---

## Copy-paste prompt

```
Reachy Ducky: next step is the `pr-reviewer` specialist (Phase B brain feature).

## Load context before acting

- Project memory should auto-load — it has current state, closed milestones, open alerts, and process patterns from the 2026-04-22 session.
- Read `CLAUDE.md` for rules + quick-start.
- Read `docs/plans/2026-04-21-reachy-ducky-design.md` §12 (pr-reviewer scoped as Phase B) and the Phase A plan at `docs/plans/2026-04-21-reachy-ducky-phase-a-plan.md`.
- Template to mirror: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py` + `daemon/tests/test_specialist_plan_reviewer.py`. The new specialist should parallel this shape.

## Approach (use the superpowers skills in order)

1. `superpowers:brainstorming` — resolve these design questions first via focused Q&A:
   - Which PR surfaces does pr-reviewer read? Diff, review comments, CI status, linked issues, commit history?
   - Does it use the existing `github-mcp-server` (declared in `.mcp.json`) or a dedicated GitHub fetch path? Stay close to SDK.
   - Response shape: same `SpecialistResponse` as plan-reviewer, or extended (per-file concerns, CI health, diff summary)?
   - Invocation: explicit `/specialists/pr-reviewer` endpoint taking a PR URL? Any auto-trigger later?
   - Test strategy: mock-PR fixtures only, or also a `@pytest.mark.integration` live-PR smoke?
   - Security: pr-reviewer reads from GitHub only (read-only); does it need the same security gate the brain has, or does MCP scoping handle it?
2. `superpowers:writing-plans` — plan at `docs/plans/YYYY-MM-DD-pr-reviewer-specialist.md`.
3. `superpowers:subagent-driven-development` — execute the plan task-by-task.

## Guardrails (carried forward from 2026-04-22 session)

- **Never merge without reading Augment + Codex bot reviews.** Hard gates green ≠ ready to merge. Codex requires explicit `@codex review` comment to re-trigger on each push.
- **Quality-first local sweep before every push**: `uv run ruff check . && uv run ruff format --check . && uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests protocol/tests menubar/tests app/tests && uv run pyright && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src && uv run pytest -q --cov`. Coverage must stay above 90% floor.
- **Escalate design decisions.** Never silently choose between two valid approaches — use `AskUserQuestion`.
- **TDD per `.claude/rules/testing-standards.md`** — failing test first, watch it fail, implement, watch it pass, commit. Side-effect verification on action methods.
- **Don't touch hardware-tier code** (`app/embodiment/`, audio I/O, wake). pr-reviewer is daemon-side only.
- **Claude workflow self-protection**: claude-review FAILS on PRs touching `.github/workflows/claude*.yml` — expected, not blocking.
- **Issue hygiene**: `Closes #N` in PR body; close manually with an explanatory comment if superseded.

## Session goal

Don't try to finish pr-reviewer in one session. A good pass: brainstorm design, write plan, start the first 1–2 implementation tasks. Multi-session split is fine — the plan doc + milestones carry state.

## Out of scope for this session

- v0.1.0 release plumbing (#24–#30, #40, #47, #48) — after pr-reviewer is demonstrable.
- v0.1.0 hardware bring-up (#15, #16, #17, #20, #22, #23) — blocks on physical Reachy access.
- Dependabot runtime alerts (#48) — investigation-heavy, separate session.
```

---

## Session-close snapshot (2026-04-22)

What future-you should be able to verify before starting:

- `git log --oneline origin/main -5` → top commit is `eb7a72f chore(deps): bump pytest-asyncio 0.25.3 → 1.3.0 (supersedes #38) (#46)`
- `gh pr list --state open` → **empty**
- `gh issue list --milestone "v0.1.0 cleanup" --state all` → 5 closed, 0 open
- `uv run pytest -q --cov` → 499 passed, 3 skipped, 4 deselected, 90.61% coverage
- `gh api repos/Obsidian-Owl/reachy-ducky/dependabot/alerts --jq '[.[] | select(.state == "open")] | length'` → 11 (tracked in #48)

If any of those diverge, the repo has moved since this handoff was written — re-read `git log` and the project memory before starting the prompt above.
