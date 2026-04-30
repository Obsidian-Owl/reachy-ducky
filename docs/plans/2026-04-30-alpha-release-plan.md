# Alpha Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut a credible `v0.1.0-alpha` release that proves the live Reachy Mini loop: wake word, voice turn, daemon query, spoken reply, mute, and documented setup.

**Architecture:** Treat alpha as release hardening, not a new feature wave. The code path is already present on `main` plus the current `live-mode-entry` branch; the plan finishes that branch, validates hardware, resolves release-blocking security and distribution work, refreshes docs, and tags a release. Alpha is scoped to one configured primary project; on-robot project selection (#22) is deferred.

**Tech Stack:** Python 3.12, uv workspace, FastAPI daemon, OpenAI Realtime voice, openWakeWord ONNX wake detector, Reachy Mini SDK, GitHub Actions, Hugging Face Spaces, GitHub CLI.

---

## Alpha Cutline

Alpha is successful when a technical early user can:

- Run `uv run reachy-ducky init` and start the Mac daemon.
- Run the live Reachy Mini app path from a Mac with `uv run reachy-ducky-app-live`, or install the app from the published Hugging Face Space.
- Say `hey jarvis`, speak a short development question, and hear a reply sourced through the Mac daemon.
- Mute and unmute without frames leaving the robot while muted.
- Follow README and smoke-test docs without reading old plan files.
- Inspect known alpha limitations in docs before using the release.

## Explicit Non-Goals

- On-robot multi-project selection (#22). Alpha uses the daemon primary project fallback.
- Custom `hey ducky` wake model (#75). Alpha uses openWakeWord's pretrained `hey jarvis`.
- Mac daemon auto-discovery from the robot (#58). Alpha documents `DAEMON_URL`.
- Rich wake/listening menubar phase strings (#76).
- PyPI or Homebrew distribution unless issue #28 explicitly chooses one before release.
- Passive observation, Codex/Claude hooks, interruption tiers, or transcript ingestion.

## File Map

- `README.md`: root status, alpha run path, accurate wake/audio docs, release install notes.
- `app/README.md`: Hugging Face Space install instructions and alpha wake-word wording.
- `docs/testing/2026-04-21-phase-a-e2e-procedure.md`: refresh from stale Phase A procedure into current alpha smoke procedure.
- `docs/testing/2026-04-30-alpha-smoke-log.md`: create during hardware validation with exact commands and outcomes.
- `.github/workflows/release.yml`: tag-based GitHub Release workflow.
- `.github/workflows/hf-space-sync.yml`: tag-based production Hugging Face Space sync workflow.
- `.github/workflows/hf-space-preview.yml`: optional PR-preview Space workflow if #26 stays in alpha scope.
- `docs/release/2026-04-30-alpha-install.md`: offline/manual robot install and upgrade path.
- `pyproject.toml`, `app/pyproject.toml`, `daemon/pyproject.toml`, `uv.lock`: dependency changes for Dependabot alert triage when needed.

## Current Baseline

- Local branch: `live-mode-entry`, two commits ahead of `main`.
- `main`: PR #79 merged ONNX wake detector and Pattern C handoff.
- Local unit gate observed on 2026-04-30: `uv run pytest -q` passed with `662 passed, 10 deselected`.
- Release plumbing open issues: #24, #26, #28, #29, #30, #40, #48.
- Hardware bring-up open issue: #22, deferred for alpha by explicit scope decision.
- Current open Dependabot alerts observed on 2026-04-30:

| Alert | Severity | Package | Current locked version | Patched version | Alpha action |
|---|---|---|---|---|---|
| #12 | medium | pip | 26.0.1 | none | Document risk or remove from release artifact if it is tooling-only. |
| #11 | high | pillow | 11.3.0 | 12.2.0 | Upgrade or pin direct dep to `pillow>=12.2.0`. |
| #10 | medium | pytest | 8.4.2 | 9.0.3 | Upgrade dev group to `pytest>=9.0.3,<10`. |
| #9 | high | gradio | 5.13.2 | 6.6.0 | Upgrade upstream path or pin direct dep if compatible. |
| #8 | medium | gradio | 5.13.2 | 6.6.0 | Same as #9. |
| #7 | high | gradio | 5.13.2 | 6.7.0 | Target `gradio>=6.7.0`. |
| #6 | low | gradio | 5.13.2 | 6.6.0 | Same as #9. |
| #5 | high | pillow | 11.3.0 | 12.1.1 | Covered by `pillow>=12.2.0`. |
| #4 | high | starlette | 0.48.0 | 0.49.1 | Upgrade FastAPI or pin direct dep to `starlette>=0.49.1`. |
| #3 | low | gradio | 5.13.2 | none | Document risk if no patched release. |
| #2 | medium | gradio | 5.13.2 | 5.31.0 | Covered by `gradio>=6.7.0`. |
| #1 | high | gradio | 5.13.2 | none | Document risk if no patched release. |

## Milestone 1 - Finish `live-mode-entry`

### Task 1: Refresh alpha docs on the live-mode branch

**Files:**
- Modify: `README.md`
- Modify: `app/README.md`
- Modify: `docs/testing/2026-04-21-phase-a-e2e-procedure.md`

- [ ] **Step 1: Update root README status**

Replace the stale status and Phase A wake text with:

```markdown
Status: Alpha candidate in release hardening.

## Run (alpha)

- First-time setup: `uv run reachy-ducky init`. The wizard writes
  `~/.reachy-ducky/config.toml`, secrets to `~/.reachy-ducky/.env`,
  and seeds the memory tree.
- Mac daemon: `set -a; source ~/.reachy-ducky/.env; set +a`
  then `uv run reachy-ducky-daemon`.
- Live hardware dev run from the Mac: `uv run reachy-ducky-app-live`.
  This connects to a LAN Reachy Mini and loads `~/.reachy-ducky/.env`.
- Robot dashboard install: publish the app Space, then install from the
  Reachy dashboard. The dashboard path is part of alpha release
  validation, not assumed.

Alpha wake word is `hey jarvis`, using vendored openWakeWord ONNX
weights. A custom `hey ducky` model is deferred to #75.
```

- [ ] **Step 2: Update `app/README.md` wake wording**

Replace "Say `Hey Ducky`" with:

```markdown
Say `hey jarvis` for the alpha wake word, ask a question, get an
answer. A custom `hey ducky` wake model is tracked separately in #75.
```

- [ ] **Step 3: Refresh the E2E procedure header**

Replace the opening stale warning in `docs/testing/2026-04-21-phase-a-e2e-procedure.md` with:

```markdown
> This procedure was refreshed for the alpha release path on 2026-04-30.
> The old Phase A limitations around stub wake and silent mock audio no
> longer describe `main` after PR #68 and PR #79.
```

- [ ] **Step 4: Add live-mode command sequence to the E2E procedure**

Add this Mac-side live path before the dashboard install path:

````markdown
### Live hardware run from the Mac

```bash
cd /Users/dmccarthy/Projects/reachy-ducky
uv sync --all-packages --group dev
uv run reachy-ducky init

# Terminal 1
set -a; source ~/.reachy-ducky/.env; set +a
REACHY_DUCKY_DAEMON_HOST=0.0.0.0 uv run reachy-ducky-daemon

# Terminal 2
uv run reachy-ducky-app-live
```

Expected:

- The app prints `Connected. Say 'hey jarvis' to start a turn.`
- Saying `hey jarvis` transitions to listening.
- A spoken question reaches `/brain/query`.
- The robot speaks the daemon reply.
- Ctrl-C exits with `Stopped.`
````

- [ ] **Step 5: Run docs-adjacent checks**

Run:

```bash
uv run ruff check README.md app/README.md docs/testing/2026-04-21-phase-a-e2e-procedure.md
uv run ruff format --check README.md app/README.md docs/testing/2026-04-21-phase-a-e2e-procedure.md
```

Expected: both commands pass.

- [ ] **Step 6: Commit docs refresh**

```bash
git add README.md app/README.md docs/testing/2026-04-21-phase-a-e2e-procedure.md
git commit -m "docs: refresh alpha live-mode run instructions"
```

### Task 2: Push `live-mode-entry` and open PR

**Files:**
- Existing branch files from `live-mode-entry`
- Modify only if tests or review require it.

- [ ] **Step 1: Run local quality gate**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict daemon/src app/src menubar/src protocol/src
uv run mypy --strict daemon/tests protocol/tests menubar/tests app/tests
uv run pyright
uv run bandit -ll -r daemon/src app/src menubar/src protocol/src
uv run pytest -q --cov
```

Expected: all pass, coverage stays at or above the configured 90 percent floor.

- [ ] **Step 2: Push branch**

```bash
git push -u origin live-mode-entry
```

- [ ] **Step 3: Create PR body**

Write `/tmp/reachy-ducky-live-mode-pr.md` with:

```markdown
## Summary

- add `reachy-ducky-app-live` for Mac-side live hardware runs
- load `~/.reachy-ducky/.env` before live voice startup
- prompt for `OPENAI_API_KEY` in `reachy-ducky init`
- generate a daemon auth token by default
- refresh alpha run docs

## Alpha scope

Alpha uses the configured primary project. On-robot project selection
is deferred to #22.

## Verification

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy --strict daemon/src app/src menubar/src protocol/src`
- `uv run mypy --strict daemon/tests protocol/tests menubar/tests app/tests`
- `uv run pyright`
- `uv run bandit -ll -r daemon/src app/src menubar/src protocol/src`
- `uv run pytest -q --cov`
```

- [ ] **Step 4: Open PR**

```bash
gh pr create \
  --base main \
  --head live-mode-entry \
  --title "feat(app): add alpha live-mode entrypoint" \
  --body-file /tmp/reachy-ducky-live-mode-pr.md
```

- [ ] **Step 5: Re-fetch review and CI**

```bash
gh pr view live-mode-entry --json number,state,mergeStateStatus,reviewDecision,statusCheckRollup
gh pr checks live-mode-entry
```

Expected: required CI passes. If `claude-review` fails for a known workflow-touch limitation, record the exact failure link in the PR and confirm required checks are green.

## Milestone 2 - Hardware Alpha Smoke

### Task 3: Run live hardware smoke and capture evidence

**Files:**
- Create: `docs/testing/2026-04-30-alpha-smoke-log.md`
- Modify: source files only if the smoke reveals a bug.

- [ ] **Step 1: Create smoke log template**

Create `docs/testing/2026-04-30-alpha-smoke-log.md` with:

````markdown
# Alpha Smoke Log - 2026-04-30

## Environment

- Branch:
- Commit:
- Mac:
- Reachy Mini:
- Network:
- Daemon URL:
- Wake word: hey jarvis
- Project source: configured primary project

## Commands

```bash
uv sync --all-packages --group dev
uv run reachy-ducky init
set -a; source ~/.reachy-ducky/.env; set +a
REACHY_DUCKY_DAEMON_HOST=0.0.0.0 uv run reachy-ducky-daemon
uv run reachy-ducky-app-live
```

## Results

- Daemon `/health`:
- Wake fired:
- User transcript captured:
- Daemon `/brain/query` returned:
- Spoken reply heard:
- Mute prevented turn:
- Shutdown clean:

## Failures And Fixes

- None.
````

- [ ] **Step 2: Start daemon**

```bash
set -a; source ~/.reachy-ducky/.env; set +a
REACHY_DUCKY_DAEMON_HOST=0.0.0.0 uv run reachy-ducky-daemon
```

Expected: uvicorn starts and `/health` returns `{"ok": true, ...}`.

- [ ] **Step 3: Start live app**

```bash
uv run reachy-ducky-app-live
```

Expected: app prints `Connected. Say 'hey jarvis' to start a turn.`

- [ ] **Step 4: Exercise golden path**

Say:

```text
hey jarvis
What is the status of this branch?
```

Expected:

- Wake detector fires once.
- Robot transitions to listening, then thinking, then listening.
- Daemon receives `/brain/query`.
- Robot speaks a relevant reply.
- App returns to idle after the reply.

- [ ] **Step 5: Exercise mute**

Use the available mute path for the current build. If only the state-machine test path is available, run:

```bash
uv run pytest -q app/tests/test_mute_integration.py app/tests/test_main_wake_pump.py
```

Expected: tests pass and the smoke log records that live UI mute propagation is outside alpha unless wired by the current branch.

- [ ] **Step 6: Run hardware-marked tests**

```bash
uv run pytest -m hardware -q app/tests/test_main_hardware.py app/tests/test_wake_hardware.py app/tests/test_sdk_audio_contract.py
```

Expected: hardware tests pass on a LAN-reachable Reachy Mini. Any failure gets fixed before continuing or documented as an explicit alpha limitation.

- [ ] **Step 7: Commit smoke log**

```bash
git add docs/testing/2026-04-30-alpha-smoke-log.md
git commit -m "docs: capture alpha hardware smoke evidence"
```

## Milestone 3 - Security And Dependency Readiness

### Task 4: Triage #48 Dependabot alerts

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: subpackage `pyproject.toml` files only when a direct dependency range owns the vulnerable package.
- Create: `docs/release/2026-04-30-dependency-triage.md`

- [ ] **Step 1: Capture current alert list**

```bash
gh api repos/Obsidian-Owl/reachy-ducky/dependabot/alerts --paginate \
  --jq '.[] | select(.state == "open") | [.number, .security_advisory.severity, .dependency.package.name, .security_advisory.summary, .security_vulnerability.vulnerable_version_range, (.security_vulnerability.first_patched_version.identifier // "none")] | @tsv' \
  | tee /tmp/reachy-ducky-alerts.tsv
```

Expected: the list includes current `gradio`, `pillow`, `starlette`, `pytest`, and `pip` alerts or a smaller set if Dependabot has updated.

- [ ] **Step 2: Inspect dependency paths**

```bash
uv tree --package gradio --depth 4
uv tree --package pillow --depth 4
uv tree --package starlette --depth 4
uv tree --package pytest --depth 3
uv tree --package pip --depth 3
```

Expected current baseline:

- `gradio` is locked at `5.13.2`.
- `pillow` is locked at `11.3.0`.
- `starlette` is locked at `0.48.0`.
- `pytest` is locked at `8.4.2`.
- `pip` is locked at `26.0.1`.

- [ ] **Step 3: Attempt direct safe pins**

Apply the smallest direct pins that satisfy patched versions:

```toml
# pyproject.toml dependency-groups.dev
"pytest>=9.0.3,<10",

# app/pyproject.toml dependencies, if resolution allows
"pillow>=12.2.0,<13",
"starlette>=0.49.1,<0.50",
"gradio>=6.7.0,<7",
```

If `gradio>=6.7.0` is incompatible with `fastrtc` or `reachy-mini`, do not force the upgrade. Remove that pin and document the incompatibility in the triage doc.

- [ ] **Step 4: Sync and test**

```bash
uv sync --all-packages --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict daemon/src app/src menubar/src protocol/src
uv run mypy --strict daemon/tests protocol/tests menubar/tests app/tests
uv run pyright
uv run bandit -ll -r daemon/src app/src menubar/src protocol/src
uv run pytest -q
```

Expected: all pass. If a pin breaks runtime imports, revert the pin and document why the alert is risk-accepted for alpha.

- [ ] **Step 5: Write dependency triage doc**

Create `docs/release/2026-04-30-dependency-triage.md` with one row per alert:

```markdown
# Alpha Dependency Triage - 2026-04-30

| Alert | Package | Severity | Current | Patched | Decision | Rationale |
|---|---|---|---|---|---|---|
| #12 | pip | medium | 26.0.1 | none | accepted | Tooling-only in alpha environment; no patched release exists at triage time. |
| #11/#5 | pillow | high | 11.3.0 | 12.2.0 | fixed or accepted | Fixed if lock contains 12.2.0 or newer; otherwise accepted only if upstream constraints block it. |
| #10 | pytest | medium | 8.4.2 | 9.0.3 | fixed or accepted | Dev-only, but fix preferred before tag. |
| #9/#8/#7/#6/#3/#2/#1 | gradio | high/medium/low | 5.13.2 | mixed | fixed or accepted | Alpha does not expose Gradio as a public route; fix if upstream constraints allow. |
| #4 | starlette | high | 0.48.0 | 0.49.1 | fixed or accepted | Daemon uses FastAPI; fix preferred before tag. |
```

Replace `fixed or accepted` with the actual decision before committing.

- [ ] **Step 6: Commit dependency triage**

```bash
git add pyproject.toml app/pyproject.toml daemon/pyproject.toml uv.lock docs/release/2026-04-30-dependency-triage.md
git commit -m "chore(deps): triage alpha dependency alerts (#48)"
```

- [ ] **Step 7: Update #48**

```bash
gh issue comment 48 --body-file docs/release/2026-04-30-dependency-triage.md
```

Close #48 only if all high severity alerts are fixed or explicitly risk-accepted in the issue comment.

## Milestone 4 - Release Plumbing

### Task 5: Decide alpha daemon distribution (#28)

**Files:**
- Create: `docs/release/2026-04-30-alpha-distribution.md`
- Modify: `README.md`

- [ ] **Step 1: Record decision**

Create `docs/release/2026-04-30-alpha-distribution.md` with:

```markdown
# Alpha Distribution Decision

Decision: tagged source checkout with `uv sync --all-packages --group dev`.

Rationale:

- No PyPI release process required for alpha.
- The install target is immutable after the tag.
- Early users can still inspect the exact source.
- `uv tool install` from a subpackage is not the alpha path because the
  daemon package depends on the workspace-local protocol package.
- Homebrew and PyPI remain available for beta.

Robot app distribution:

- Production Hugging Face Space sync from `v*` tags.
- Manual offline install documented for recovery and local testing.

Deferred:

- PyPI packaging.
- Homebrew tap.
```

- [ ] **Step 2: Update issue #28**

```bash
gh issue comment 28 --body-file docs/release/2026-04-30-alpha-distribution.md
gh issue close 28 --reason completed
```

- [ ] **Step 3: Commit**

```bash
git add docs/release/2026-04-30-alpha-distribution.md README.md
git commit -m "docs: choose alpha distribution path (#28)"
```

### Task 6: Add GitHub Release workflow (#29)

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Add workflow**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write

jobs:
  github-release:
    name: GitHub Release
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with:
          fetch-depth: 0

      - name: Create release notes
        run: |
          mkdir -p dist
          {
            echo "# ${GITHUB_REF_NAME}"
            echo
            echo "Alpha release for Reachy Ducky."
            echo
            echo "## Changes"
            git log --oneline "$(git describe --tags --abbrev=0 "${GITHUB_REF_NAME}^" 2>/dev/null || git rev-list --max-parents=0 HEAD)".."${GITHUB_REF_NAME}" || true
          } > dist/release-notes.md

      - name: Publish GitHub release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          prerelease_flag=""
          case "${GITHUB_REF_NAME}" in
            *alpha*|*beta*|*rc*) prerelease_flag="--prerelease" ;;
          esac
          gh release create "${GITHUB_REF_NAME}" \
            --title "${GITHUB_REF_NAME}" \
            --notes-file dist/release-notes.md \
            ${prerelease_flag}
```

- [ ] **Step 2: Validate syntax**

```bash
uv run python - <<'PY'
from pathlib import Path
import yaml
yaml.safe_load(Path(".github/workflows/release.yml").read_text())
print("release workflow yaml ok")
PY
```

Expected: prints `release workflow yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add tag-based GitHub release workflow (#29)"
```

### Task 7: Add tag-based Hugging Face Space sync (#30)

**Files:**
- Create: `.github/workflows/hf-space-sync.yml`
- Modify: `README.md`

- [ ] **Step 1: Add workflow**

Create `.github/workflows/hf-space-sync.yml`:

```yaml
name: Hugging Face Space sync

on:
  push:
    tags:
      - "v*"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  sync-space:
    name: Sync app to production Space
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6

      - name: Install uv
        uses: astral-sh/setup-uv@v7
        with:
          version: "0.5.30"
          enable-cache: true

      - name: Set up Python
        run: uv python install 3.12

      - name: Install Hugging Face CLI
        run: uv tool install "huggingface_hub[cli]"

      - name: Sync app directory
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
          HF_SPACE: Obsidian-Owl/reachy_ducky_app
        run: |
          test -n "$HF_TOKEN"
          hf upload "$HF_SPACE" app . --repo-type space --delete "*"
```

- [ ] **Step 2: Add README secret note**

Add:

```markdown
### Release secrets

The tag-based Hugging Face Space sync requires repository secret
`HF_TOKEN` with write access to `Obsidian-Owl/reachy_ducky_app`.
```

- [ ] **Step 3: Validate syntax**

```bash
uv run python - <<'PY'
from pathlib import Path
import yaml
yaml.safe_load(Path(".github/workflows/hf-space-sync.yml").read_text())
print("hf workflow yaml ok")
PY
```

Expected: prints `hf workflow yaml ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/hf-space-sync.yml README.md
git commit -m "ci: add tag-based Hugging Face Space sync (#30)"
```

### Task 8: Decide whether PR-preview Spaces block alpha (#26)

**Files:**
- Create: `.github/workflows/hf-space-preview.yml` only if kept in alpha scope.
- Or modify: `docs/release/2026-04-30-alpha-distribution.md` if deferred.

- [ ] **Step 1: Apply alpha decision**

Recommended alpha decision: defer #26. Production tag sync plus Mac live mode is enough for alpha; PR-preview Spaces improve review ergonomics but are not required to prove the release.

- [ ] **Step 2: Record deferral**

Append to `docs/release/2026-04-30-alpha-distribution.md`:

```markdown
## PR-preview Spaces

Decision: deferred from alpha.

Reason: alpha only needs a production Space published from the release
tag plus `reachy-ducky-app-live` for local hardware validation.
PR-preview Spaces remain useful for later review ergonomics but do not
block `v0.1.0-alpha`.
```

- [ ] **Step 3: Update #26**

```bash
gh issue comment 26 --body "Deferred from alpha. Production tag sync (#30) plus live-mode hardware validation is sufficient for v0.1.0-alpha; PR-preview Spaces remain useful post-alpha review ergonomics."
```

Do not close #26 unless the team wants the milestone cleaned by moving it out of `v0.1.0 release plumbing`.

### Task 9: Document offline install and manual upgrade (#24)

**Files:**
- Create: `docs/release/2026-04-30-alpha-install.md`
- Modify: `README.md`
- Modify: `app/README.md`

- [ ] **Step 1: Create install doc**

Create `docs/release/2026-04-30-alpha-install.md`:

````markdown
# Alpha Install And Manual Upgrade

## Mac daemon

```bash
git clone https://github.com/Obsidian-Owl/reachy-ducky.git
cd reachy-ducky
git checkout v0.1.0-alpha
uv sync --all-packages --group dev
uv run reachy-ducky init
set -a; source ~/.reachy-ducky/.env; set +a
REACHY_DUCKY_DAEMON_HOST=0.0.0.0 uv run reachy-ducky-daemon
```

## Robot app through dashboard

1. Open the Reachy dashboard.
2. Install the published `Obsidian-Owl/reachy_ducky_app` Space.
3. Set `DAEMON_URL`, `DAEMON_AUTH_TOKEN`, and `OPENAI_API_KEY`.
4. Start the app.

## Manual live hardware run from Mac

```bash
git checkout v0.1.0-alpha
uv sync --all-packages --group dev
uv run reachy-ducky-app-live
```

## Upgrade

1. Stop the daemon and app.
2. Fetch and check out the new tag.
3. Re-run `reachy-ducky init` only when release notes say config shape changed.
4. Restart daemon and app.
````

- [ ] **Step 2: Link install doc**

Add this link to `README.md`:

```markdown
For alpha install, offline fallback, and manual upgrade instructions, see
`docs/release/2026-04-30-alpha-install.md`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/release/2026-04-30-alpha-install.md README.md app/README.md
git commit -m "docs: add alpha offline install and upgrade path (#24)"
```

## Milestone 5 - Issue Hygiene And Final Tag

### Task 10: Clean stale and deferred issues

**Files:**
- No code files expected.

- [ ] **Step 1: Close stale #56 if merged evidence still holds**

Verify:

```bash
rg -n "plans omitted|single oversized|symlink.*nonplan|_discover" daemon/tests daemon/src/reachy_ducky_daemon/brain/plans_mcp.py
```

Expected: the #56 follow-up tests and docstring changes are present.

Then:

```bash
gh issue comment 56 --body "Verified on 2026-04-30: the follow-ups are already present on main via PR #70 / commit 4fe93b6. Closing as completed."
gh issue close 56 --reason completed
```

- [ ] **Step 2: Mark #22 deferred from alpha**

```bash
gh issue comment 22 --body "Alpha scope decision: v0.1.0-alpha uses the configured primary project fallback. On-robot project selection remains valuable but is deferred until after alpha."
```

Move #22 out of the alpha milestone if milestone hygiene is required.

- [ ] **Step 3: Triage #78**

```bash
gh pr view 78 --json number,title,state,mergeStateStatus,statusCheckRollup
gh pr checks 78
```

If only `claude-review` fails and required CI passes, either merge after review or close with a comment that the Ruff bump will be handled in the dependency triage branch.

### Task 11: Final release verification

**Files:**
- No code files expected unless verification finds a defect.

- [ ] **Step 1: Sync with main**

```bash
git checkout main
git pull --ff-only origin main
```

- [ ] **Step 2: Run full local gate**

```bash
uv sync --all-packages --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict daemon/src app/src menubar/src protocol/src
uv run mypy --strict daemon/tests protocol/tests menubar/tests app/tests
uv run pyright
uv run bandit -ll -r daemon/src app/src menubar/src protocol/src
uv run pytest -q --cov
```

Expected: all pass.

- [ ] **Step 3: Verify release blockers**

```bash
gh issue list --state open --milestone "v0.1.0 release plumbing"
gh issue list --state open --milestone "v0.1.0 hardware bring-up"
gh pr list --state open
```

Expected:

- Release plumbing has no open blockers, or only explicitly deferred #26/#40.
- Hardware bring-up has only deferred #22.
- No open PR is required for alpha.

- [ ] **Step 4: Create tag**

```bash
git tag -a v0.1.0-alpha -m "v0.1.0-alpha"
git push origin v0.1.0-alpha
```

Expected: `Release` and `Hugging Face Space sync` workflows start.

- [ ] **Step 5: Verify release artifacts**

```bash
gh release view v0.1.0-alpha
gh run list --workflow release.yml --limit 3
gh run list --workflow hf-space-sync.yml --limit 3
```

Expected: GitHub Release exists and HF Space sync succeeded.

- [ ] **Step 6: Post-tag smoke**

Run the live hardware path from the tag:

```bash
git checkout v0.1.0-alpha
uv sync --all-packages --group dev
uv run reachy-ducky-app-live
```

Expected: same golden path as Milestone 2.

## Done When

- `live-mode-entry` is merged.
- Alpha smoke log proves wake, voice, daemon query, spoken reply, and shutdown.
- #48 is fixed or explicitly risk-accepted with a written triage.
- #28 is decided for alpha.
- #29 and #30 are implemented and verified.
- #24 docs exist and are linked.
- Stale docs no longer claim wake/audio are stubs.
- #22 is documented as deferred because alpha uses the primary project fallback.
- `v0.1.0-alpha` tag creates a GitHub Release and syncs the production Hugging Face Space.
