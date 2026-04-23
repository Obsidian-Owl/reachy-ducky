# Canonical Reachy-Mini Dep Migration — Design + Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (or `superpowers:subagent-driven-development` for same-session execution) to implement this plan task-by-task.

**Goal:** Mirror Pollen Robotics' canonical install pattern for the `reachy-mini` SDK so upstream SDK upgrades stop requiring workspace-layer re-architecting, and Mac-based developers can introspect + hardware-test the SDK locally without SSH.

**Architecture:** Move `reachy-mini` from a Linux-gated optional extra (`app/pyproject.toml:[project.optional-dependencies].robot`) to a plain base dep, copy Pollen's canonical `[tool.uv] dependency-metadata` workaround for the upstream `gstreamer-msvc-runtime` platform-marker bug, collapse the standalone `sdk-contract.yml` CI workflow into the main CI matrix, drop the now-redundant `sdk` pytest marker, and expand the CI matrix to add `windows-latest`. The `structural-shape` pattern on `main.py` stays (intentional import discipline; not a dep workaround).

**Tech Stack:** `uv` workspace (4 member packages), `pyproject.toml` metadata, `hatchling` build backend, `pytest` markers, GitHub Actions (matrix workflow), `reachy-mini>=1.6.4` (upstream SDK).

**Issues closed:** none directly. This is an independent workspace refactor; it *unblocks* #23 + #20 by letting Mac devs introspect the SDK locally without SSH, but the issues themselves close in the hardware-testing plan (PR #59).

**Follow-up tracking issues filed during Phase 1:**
- #60 — Upgrade `reachy-mini` past `>=1.6.4` once the migration stabilizes.
- #61 — Remove the `[tool.uv] dependency-metadata` patch when `gstreamer-bundle` upstream fixes its `gstreamer-msvc-runtime` platform marker.
- #62 — `REACHY_MINI_HOST` env var for hardware tests (today the hardware-testing plan hardcodes `reachy-mini.local`; minor DX, low priority).

**Deliberate exclusions (option C — NOT filed as issues):**
- Pollen subsystem markers (`audio` / `video` / `wireless`). Not a deferral; we're a companion app, not an SDK. File if and only if hardware coverage ever expands enough to warrant the taxonomy.
- Physical-hardware `workflow_dispatch` CI job over a self-hosted runner. Not a deferral; `-m hardware` against a LAN robot covers the dev loop. File only if true CI-tier hardware coverage becomes a requirement.

---

## Design context

### The problem statement

`app/pyproject.toml:14-19` today:

```toml
# reachy-mini has a broken Windows-only transitive dep (gstreamer-msvc-runtime)
# that prevents install on non-Windows. The app package is primarily deployed
# to the Reachy Mini (Linux/aarch64); use `uv sync --extra robot` on the
# robot. Mac dev does not need reachy-mini for unit tests.
[project.optional-dependencies]
robot = ["reachy-mini>=1.0.0rc1,<2"]
```

This gates `reachy-mini` behind an optional extra, forcing Mac dev to either SSH into the robot or go without SDK introspection. It also forces the `sdk-contract.yml` CI workflow to be a separate Ubuntu-only job with a manual `apt-get install` step for build headers. Our internal rationale for the gating (per the inline comment) is that `gstreamer-msvc-runtime` prevents install on non-Windows.

### What the research showed

Investigation (2026-04-22, against `reachy-mini` PyPI + `pollen-robotics/reachy_mini_conversation_app`):

1. **`reachy-mini` is ONE cross-platform PyPI package.** Latest stable `1.7.0`. Installs cleanly on macOS arm64 today. Our pin `1.0.0rc1` is ~53 releases behind.
2. **`gstreamer-msvc-runtime` is NOT a Pollen design choice.** It's an upstream metadata bug — `gstreamer-libs` incorrectly marks `gstreamer-msvc-runtime` as universal (should be `sys_platform == 'win32'`). Pollen patches it in both the SDK repo and the conversation app via `[tool.uv] dependency-metadata`. Our comment describes a symptom; the fix lives in the patch, not in an extra-gated install path.
3. **Pollen's conversation app (our closer analog) depends on `reachy-mini>=1.6.4` as a plain base dep** — no extras, no platform gating. Their CI matrix is `ubuntu-latest + macos-latest + windows-latest × py3.12`.
4. **Pollen's SDK repo uses subsystem markers** (`audio` / `video` / `wireless` / `ipc_resolution`) for finer-grained gating than our `hardware` marker, and runs `pytest -m 'not audio and not video and not wireless'` by default. They have a separate `workflow_dispatch` job for physical hardware on self-hosted runners. We deliberately skip mirroring their marker taxonomy (we're a companion app, not an SDK; our tier markers answer different questions).

### The decision (option C from the brainstorm)

Fully canonical on the **install story** where we're wrong. Keep our **test-tier markers** (`hardware` / `integration` / `sim`) because they fit our domain. Add `windows-latest` to the CI matrix for install-story validation even though we don't deploy to Windows.

---

## Conventions used throughout

- **TDD where applicable.** Not every migration task has a failing-test shape (config edits, file deletions). For those, the "test" is the full-branch quality gate passing after the change.
- **Phased approach**, one PR per phase, land linearly:
  - **Phase 1** — this plan doc, lands on main first as its own PR (mirrors PR #53 / PR #59 pattern).
  - **Phase 2** — atomic dep + CI collapse (all 7 tasks land together because they're load-bearing on each other).
  - **Phase 3** — windows CI as additive work on a fresh branch.
- **Branch naming**:
  - Phase 1: `canonical-reachy-mini-dep-plan`
  - Phase 2: `canonical-reachy-mini-dep-migration`
  - Phase 3: `ci-windows-matrix`
- **Per-task gate before each commit within Phase 2:**
  ```bash
  uv run ruff check . && uv run ruff format --check . \
    && uv run mypy --strict daemon/src app/src menubar/src protocol/src \
                          daemon/tests app/tests menubar/tests protocol/tests \
    && uv run pytest -q
  ```
- **Full-branch gate before push:**
  ```bash
  uv run ruff check . && uv run ruff format --check . \
    && uv run mypy --strict daemon/src app/src menubar/src protocol/src \
                          daemon/tests app/tests menubar/tests protocol/tests \
    && uv run pyright \
    && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src \
    && uv run pytest -q --cov
  ```
  Coverage floor **90%**.
- **Conventional commits** (`feat:` / `fix:` / `refactor:` / `chore(deps):` / `chore(ci):` / `test:` / `docs:`).

**Reference skills:** `@superpowers:test-driven-development`, `@superpowers:verification-before-completion`.

---

## Phase 1 — Design doc lands on main

This plan file, opened as its own docs-only PR. No implementation.

### Task 1.1: Commit + push + open PR

**Files:**
- Create: `docs/plans/2026-04-22-canonical-reachy-mini-dep-migration.md` (this file).

**Step 1: Branch + commit**

```bash
git checkout main && git pull origin main
git checkout -b canonical-reachy-mini-dep-plan
git add docs/plans/2026-04-22-canonical-reachy-mini-dep-migration.md
git commit -m "docs: add canonical reachy-mini dep migration plan"
```

**Step 2: File follow-up issues**

Before opening the PR, file the tracking issues called out above. Reference them in the PR body.

**Step 3: Push + open PR**

```bash
git push -u origin canonical-reachy-mini-dep-plan
gh pr create --base main --title "docs: add canonical reachy-mini dep migration plan"
```

---

## Phase 2 — Atomic dep + CI migration

Branch `canonical-reachy-mini-dep-migration` off `main` (after Phase 1 merges). All tasks land on this one branch; the branch ships as one PR.

### Task 2.1: Update `app/pyproject.toml`

**Files:**
- Modify: `app/pyproject.toml`.

**Changes:**
- Delete lines 14-19 (the comment block + `[project.optional-dependencies]` section).
- Add `reachy-mini>=1.6.4,<2` to `dependencies`.

**Why `>=1.6.4`:** matches the conversation app's floor. `<2` retains major-version-break safety. Bump from `1.0.0rc1` is ~53 releases of upstream change; Task 2.4's grep-audit mitigates rename risk.

**Step 1: Edit `app/pyproject.toml`**

Before:
```toml
dependencies = [
    "reachy-ducky-protocol",
    "fastrtc>=0.0.20,<0.2",
    "openai>=1.50,<2",
    "mediapipe>=0.10,<0.12",
    "httpx>=0.27,<0.29",
    "numpy>=1.26,<3",
]

# reachy-mini has a broken Windows-only transitive dep (gstreamer-msvc-runtime)
# that prevents install on non-Windows. The app package is primarily deployed
# to the Reachy Mini (Linux/aarch64); use `uv sync --extra robot` on the
# robot. Mac dev does not need reachy-mini for unit tests.
[project.optional-dependencies]
robot = ["reachy-mini>=1.0.0rc1,<2"]
```

After:
```toml
dependencies = [
    "reachy-ducky-protocol",
    "fastrtc>=0.0.20,<0.2",
    "openai>=1.50,<2",
    "mediapipe>=0.10,<0.12",
    "httpx>=0.27,<0.29",
    "numpy>=1.26,<3",
    # reachy-mini is a plain base dep — installs cross-platform. The
    # gstreamer-msvc-runtime platform-marker bug is patched at the
    # workspace root via `[tool.uv] dependency-metadata` (mirrors
    # pollen-robotics/reachy_mini_conversation_app).
    "reachy-mini>=1.6.4,<2",
]
```

Note: the `[project.optional-dependencies]` block is removed entirely.

**Step 2: Validate**

```bash
uv sync --all-packages --group dev
uv run python -c "from reachy_mini import ReachyMini; print(ReachyMini.__module__)"
```

Expected: clean sync; print a module path. If sync fails with `gstreamer-msvc-runtime` on macOS/Linux, move on to Task 2.2 (the metadata patch) first — the tasks can land in either order within the branch, but the gate only passes once both are in.

**Step 3: Do not commit yet.** Phase 2 is one atomic commit (see Task 2.8).

---

### Task 2.2: Add `[tool.uv] dependency-metadata` patch to root `pyproject.toml`

**Files:**
- Modify: root `pyproject.toml`.

**Step 1: Fetch Pollen's current patch**

```bash
curl -sS https://raw.githubusercontent.com/pollen-robotics/reachy_mini_conversation_app/main/pyproject.toml | grep -A 20 '\[tool.uv\]'
```

Expected output: a `[tool.uv]` block containing `dependency-metadata` entries. Note the exact SHA of `main` at fetch time — we'll cite it in the annotation comment.

**Step 2: Add the patch**

Paste Pollen's `[tool.uv] dependency-metadata` block into the root `pyproject.toml`, immediately below the existing `[tool.uv.workspace]` block. Add a comment annotation explaining where it came from, why it's needed, and when to remove it:

```toml
# Upstream `gstreamer-bundle` / `gstreamer-libs` incorrectly mark
# `gstreamer-msvc-runtime` as unconditional (should be
# sys_platform == 'win32'). Verbatim copy of pollen-robotics/
# reachy_mini_conversation_app@<sha>'s workaround. Remove when upstream
# fixes the platform marker.
[tool.uv]
dependency-metadata = [
  # ... paste Pollen's entries here ...
]
```

Record the upstream SHA in the comment so a future upgrade audit knows the reference point.

**Step 3: Validate**

```bash
uv lock && uv sync --all-packages --group dev
```

Expected: clean resolution. `gstreamer-msvc-runtime` should NOT appear in the lock on non-Windows platforms (grep `uv.lock` to confirm).

---

### Task 2.3: Update markers + addopts in root `pyproject.toml`

**Files:**
- Modify: root `pyproject.toml` `[tool.pytest.ini_options]` block.
- Modify: root `pyproject.toml` `[[tool.mypy.overrides]]` block (remove `reachy_mini` entry).

**Step 1: Edit markers**

Remove the `sdk` marker from the `markers` list. Before:
```toml
markers = [
    "hardware: requires Reachy Mini hardware",
    "integration: requires live external APIs",
    "sim: requires a running reachy-mini-daemon --sim (pytest -m sim)",
    "sdk: requires the reachy-mini SDK installed (no daemon); catches upstream API drift",
]
```

After:
```toml
markers = [
    "hardware: requires Reachy Mini hardware reachable (LAN for Wireless, USB for Lite)",
    "integration: requires live external APIs",
    "sim: requires a running reachy-mini-daemon --sim (pytest -m sim)",
]
```

Update the `hardware` description to reflect expanded scope (now covers instance-level SDK introspection, not just motion/audio E2E).

**Step 2: Edit addopts**

Before:
```toml
addopts = "-m 'not sim and not sdk'"
```

After:
```toml
addopts = "-m 'not sim and not hardware'"
```

Rationale: `hardware` was never default-skipped (a pre-existing bug; irrelevant today because no hardware-marked tests exist yet, but we're fixing it before Task 1.1 of the hardware-testing plan adds the first ones).

**Step 3: Remove `reachy_mini` mypy override**

Before (in root `pyproject.toml`):
```toml
[[tool.mypy.overrides]]
module = "reachy_mini"
ignore_missing_imports = true
```

Delete this block entirely. The package now ships in every dev venv; mypy can resolve it natively.

**Step 4: Validate**

```bash
uv run mypy --strict daemon/src app/src menubar/src protocol/src
```

Expected: clean. If any `reachy_mini` import surfaces a new type error that was masked by the override, fix at the source (add proper type ignores at specific lines with justification).

---

### Task 2.4: Remove inline pyright ignores + migrate `sdk`-marker tests

**Files:**
- Modify: every file grep identifies with `# pyright: ignore[reportMissingImports]` on a `reachy_mini` import (or adjacent).
- Modify: every file grep identifies with `@pytest.mark.sdk`.

**Step 1: Find + remove inline pyright ignores on `reachy_mini` imports**

```bash
grep -rn "reportMissingImports" --include="*.py" .
grep -rn "reachy_mini" --include="*.py" . | grep "pyright"
```

For each match on a `reachy_mini` import line: remove the `# pyright: ignore[reportMissingImports]` comment. Do NOT remove `# type: ignore[attr-defined]` on method-call lines (those are justified — we duck-type the `reachy_mini: object` parameter in the structural-shape pattern; see `main.py`).

**Step 2: Migrate `sdk`-marker tests**

```bash
grep -rn "pytestmark = pytest.mark.sdk\|@pytest.mark.sdk" --include="*.py" .
```

For each match:
- If the test file is all `sdk`-marked and exists because the SDK might not be installed: remove the marker (the tests become default-tier unit tests).
- If the test file contains `@pytest.mark.sdk` alongside other markers: drop the `sdk` decorator, keep the others.
- If any test ALSO needs a live daemon (instance-level introspection): change the marker to `@pytest.mark.hardware` and add an `importorskip` / connection-check fixture.

**Step 3: Validate**

```bash
uv run pyright
uv run pytest -q
```

Expected: pyright clean (no more unresolved-import warnings on `reachy_mini`); pytest passes with the migrated tests in the default tier.

---

### Task 2.5: Delete `sdk-contract.yml`; fold the apt step into main CI

**Files:**
- Delete: `.github/workflows/sdk-contract.yml`.
- Modify: `.github/workflows/ci.yml` (add the pygobject/pycairo apt step on the Linux matrix entry).

**Step 1: Delete the standalone workflow**

```bash
git rm .github/workflows/sdk-contract.yml
```

**Step 2: Read main CI**

```bash
cat .github/workflows/ci.yml
```

Identify the Linux job(s) in the matrix. Add a step BEFORE the `uv sync` step, conditioned on `runs-on == 'ubuntu-latest'`:

```yaml
      - name: Install pygobject/pycairo build headers (Linux only)
        if: runner.os == 'Linux'
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends \
            libcairo2-dev \
            libgirepository1.0-dev \
            libglib2.0-dev
```

(Exact placement: immediately after `actions/checkout` / before `astral-sh/setup-uv`.)

**Step 3: Validate locally (best-effort)**

No local way to fully verify GitHub Actions changes short of pushing. The first CI run after PR 2 is pushed will exercise this path.

---

### Task 2.6: Update `CLAUDE.md` + dev docs

**Files:**
- Modify: `CLAUDE.md`.
- Modify (if it exists): `docs/testing/2026-04-21-phase-a-e2e-procedure.md` sections that mention `--extra robot`.

**Step 1: Edit `CLAUDE.md`**

Find and update:

- **"Quick Start" section**: `uv sync --all-packages --group dev` stays correct (it now brings `reachy-mini` in by default — no `--extra robot` needed for SDK contract).
- **"Local sim dev flow" section**: delete the "on the robot only: `uv sync --all-packages --extra robot`" line — it no longer exists.
- **"SDK contract tests" paragraph**: the `sdk` marker is gone. Tests that used it are now in the default tier. Update wording to say class-level introspection runs by default; instance-level tests move under `@pytest.mark.hardware`.
- **"Hardware tests" paragraph**: expand to note hardware tier now covers instance-level SDK introspection AND end-to-end audio/motion/mute. `pytest -m hardware` still the invocation.

**Step 2: Edit E2E procedure doc if references exist**

```bash
grep -rn "extra robot\|pytest -m sdk" docs/
```

Update every hit to reflect the new reality.

**Step 3: Validate**

Docs-only changes — no gate needed beyond proof-reading for accuracy.

---

### Task 2.7: Pre-push audit — SDK attribute drift

**Rationale:** 1.0.0rc1 → 1.6.4 is ~53 releases. Renames are the plausible failure mode. Task 1.1 of the hardware-testing plan adds the defensive contract test, but that won't land until after Phase 2. Bridge the gap with a one-shot grep-audit.

**Step 1: Catalog every `ReachyMini.` attribute we reference**

```bash
grep -rn "ReachyMini\|reachy_mini\." --include="*.py" daemon/src app/src menubar/src protocol/src
```

Compile a list — expected patterns include `mini.media.*`, `mini.head.*`, `mini.body.*`, `mini.play_move(...)`, `mini.go_to_sleep()`, `mini.wake_up()`, and whatever the existing `ReachyMotionDriver` touches.

**Step 2: Cross-reference with `reachy_mini==1.6.4`**

```bash
uv pip show reachy-mini | grep Location
# then read the installed source at that Location and grep for the attrs
```

For each attribute we reference: confirm it exists in the installed version. If ANY attribute is missing/renamed: STOP. Escalate — this is a design choice, not a silent adapt.

**Step 3: Note the audit in the PR body**

Include the audit results (every `ReachyMini.` attribute we use, cross-referenced against 1.6.4) in the PR body. Links or code-blocks for each reference. Reviewers need to see this work.

---

### Task 2.8: Full-branch gate + single-commit squash + push + PR

**Step 1: Full-branch gate**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests app/tests menubar/tests protocol/tests
uv run pyright
uv run bandit -ll -r daemon/src app/src menubar/src protocol/src
uv run pytest -q --cov
```

All clean; coverage ≥ 90%.

**Step 2: Review uv.lock diff**

```bash
git diff main -- uv.lock | head -100
```

Expected: adds `reachy-mini` + its transitive graph; removes nothing that matters. Include a summary of the top-level additions in the PR body.

**Step 3: Squash into one commit**

If the task-by-task work produced multiple local commits, squash them for a clean history:

```bash
git rebase -i main
# squash all commits into one
```

Commit message template:

```
chore(deps): migrate to reachy-mini as plain base dep (canonical alignment)

Mirror pollen-robotics/reachy_mini_conversation_app's dep shape:
- Remove the `robot` optional extra; put reachy-mini>=1.6.4,<2 as a base
  dep on app/ (installs cross-platform, including macOS arm64).
- Copy pollen-robotics' [tool.uv] dependency-metadata workaround for the
  upstream gstreamer-msvc-runtime platform-marker bug.
- Collapse sdk-contract.yml into the main CI (apt step folded into the
  Linux matrix entry); the dedicated workflow no longer earns its keep
  now that reachy-mini is always available.
- Drop the `sdk` pytest marker; class-level introspection tests move
  to the default tier, instance-level ones move under `hardware`.
- Update `addopts` to `'not sim and not hardware'` (fixes a pre-existing
  bug where hardware tests weren't default-skipped; irrelevant today
  because no hardware tests exist, but about to with #59).
- Remove inline pyright ignores + mypy override for reachy_mini — now
  a plain installed module.
- Update CLAUDE.md + dev docs to reflect the new install story.

Version bump from 1.0.0rc1 to 1.6.4: ~53 upstream releases. Pre-push
audit in Task 2.7 confirms every ReachyMini.* attribute we reference
still exists in 1.6.4.

Design doc: docs/plans/2026-04-22-canonical-reachy-mini-dep-migration.md.
```

**Step 4: Push + open PR**

```bash
git push -u origin canonical-reachy-mini-dep-migration
gh pr create --base main --title "chore(deps): migrate to reachy-mini as plain base dep (canonical alignment)"
```

PR body should include:
- Design doc reference.
- uv.lock additions summary.
- Task 2.7 audit results.
- Expected CI behavior: existing Linux + macOS matrix entries continue green; the dedicated SDK contract check is gone (absorbed into main CI).

---

## Phase 3 — Windows CI (additive)

Branch `ci-windows-matrix` off `main` (after Phase 2 merges). Pure CI addition; no production-code changes.

### Task 3.1: Expand CI matrix

**Files:**
- Modify: `.github/workflows/ci.yml` (matrix block).

**Step 1: Add `windows-latest` to matrix**

```yaml
matrix:
  os: [ubuntu-latest, macos-latest, windows-latest]
```

**Step 2: Validate (shape-only)**

```bash
yamllint .github/workflows/ci.yml  # if installed
```

Real validation happens on the first CI run.

---

### Task 3.2: Exclude `menubar/` on non-macOS jobs

**Files:**
- Modify: `.github/workflows/ci.yml` (add conditional install or matrix exclude).

**Step 1: Decide the exclusion mechanism**

Two options:
- **(A)** `uv sync --all-packages --exclude-package reachy-ducky-menubar` on non-macOS jobs. Clean; doesn't install `rumps` at all.
- **(B)** Matrix-level `exclude` entry that skips the menubar test collection on non-macOS.

Option (A) is cleaner — menubar never enters the dep graph on windows / linux. Implement:

```yaml
      - name: Install workspace (platform-aware)
        run: |
          if [ "${{ runner.os }}" = "macOS" ]; then
            uv sync --frozen --all-packages --group dev
          else
            uv sync --frozen --all-packages --exclude-package reachy-ducky-menubar --group dev
          fi
        shell: bash
```

**Step 2: Skip menubar tests on non-macOS**

If menubar's tests auto-import the package, they'll fail on non-macOS even with the exclusion. Add to `menubar/tests/conftest.py`:

```python
import sys
import pytest

if sys.platform != "darwin":
    pytestmark = pytest.mark.skip(reason="menubar is macOS-only (rumps)")
```

Wait — `testing-standards.md` forbids `pytest.mark.skip` outside `importorskip`. The correct idiom:

```python
import sys
pytest.importorskip("rumps", reason="menubar is macOS-only")
```

Place this at module scope in `menubar/tests/conftest.py` so the whole test package skips when `rumps` isn't installed. That's the sanctioned escape hatch.

**Step 3: Validate**

Push; the first run on windows CI will tell you if the exclusion works. Budget 1-2 iterations.

---

### Task 3.3: Iterate on windows CI until green

**Step 1: Monitor the first run**

```bash
gh pr checks <PR-url>
```

**Step 2: Triage failures**

Common failure modes and fixes:
- `gstreamer-msvc-runtime` install error → Pollen's metadata patch didn't cover windows; re-sync the patch.
- Line-ending issues → `.gitattributes` or ruff config.
- Path separators in tests → `os.path.join` / `Path` usage instead of `/` string concat.

**Step 3: Branch protection — DO NOT add windows-latest as required yet**

Keep it informative only. Promote later (per issue #47) after a stable window (2 weeks / 20 merges without flake).

**Step 4: Commit + push**

```bash
git commit -m "chore(ci): add windows-latest to CI matrix (install-story canonical alignment)"
git push
```

Open PR; merge when green.

---

## Done / exit criteria

All three phases merged to `main`:

1. **Phase 1 merged** — this plan doc landed; follow-up issues filed.
2. **Phase 2 merged** — `--extra robot` is gone from the codebase (grep confirms). SDK contract workflow is gone. `reachy-mini` installs in every dev venv via `uv sync --all-packages --group dev`. `sdk` marker removed. CI runs on ubuntu + macos matrix with the old coverage intact.
3. **Phase 3 merged** — CI matrix includes `windows-latest` as informative-only.
4. **All CI green** on main after Phase 3 merges.
5. **Follow-up issues filed** per the top-of-doc list; `TODO(#XX)`-style comments exist in source ONLY for items with active tracking issues.

**Spot-check verifications (run on main after all merges):**

```bash
# reachy-mini is always installed
uv sync --all-packages --group dev
uv run python -c "from reachy_mini import ReachyMini; print(ReachyMini.__module__)"

# No sdk-marker leftovers
grep -rn "pytest.mark.sdk\|'sdk'" --include="*.py" --include="*.toml" .

# No --extra robot leftovers
grep -rn "extra robot\|extra_robot\|optional-dependencies.*robot" .

# Inline pyright ignores for reachy_mini are gone
grep -rn "reachy_mini" --include="*.py" . | grep -i "pyright"

# Workflow collapse
test ! -f .github/workflows/sdk-contract.yml
```

All five should return empty / expected.

---

## Risks & follow-ups

### Known risks (flagged for review attention)

- **Version jump `1.0.0rc1 → 1.6.4`.** Mitigated by Task 2.7's attribute audit. Residual risk: behavior change (not API rename) — something like `ReachyMini.media.get_audio_sample()` returning a slightly different PCM shape. No unit-level coverage for that today; the hardware-testing plan's Task 1.1 contract test is the backstop.

- **uv metadata patch drifts.** Annotated with source SHA (Task 2.2). Tracking issue filed during Phase 1.

- **HF Space deploy (`Obsidian-Owl/reachy_ducky_app`).** The `app/` subtree's dep graph changes; HF Space resolves at install time on robot-native Linux/aarch64 where the dep was already working. Smoke-test post-Phase-2 merge.

- **Windows CI first-run redness.** Non-required initially; no merge-flow blocker.

### Deliberate non-risks (no second-guessing in review)

- **Removing inline pyright ignores**: trivial revert if it breaks anything. Pure clean-up.
- **Removing mypy override**: same — safe revert path.
- **`addopts` one-line edit**: failure mode is a red suite that tells you immediately.
- **Dropping `sdk` marker**: no tooling / badge / monitoring depends on it (grep-verified in Task 2.4).

### Follow-ups NOT in scope (intentional deferrals per option C)

- **Pollen subsystem markers** (`audio` / `video` / `wireless` / `ipc_resolution`). Not now; we're a companion app, not an SDK.
- **Physical-hardware `workflow_dispatch` CI job** on self-hosted runner. Not now; `-m hardware` against a LAN robot covers our dev loop.
- **Windows-latest as a required branch-protection check.** Promote later per #47.
- **Upgrade past `>=1.6.4`.** Tracking issue filed; revisit after migration stabilizes.
- **`REACHY_MINI_HOST` env var** for hardware tests. Tracking issue filed; low priority.

---

## Session split suggestion

- **Session A** — Phase 1 (this plan lands on main). Small; can bundle with other work.
- **Session B** — Phase 2 (atomic migration). Expect 2-4 hours including the Task 2.7 audit and the first CI iteration.
- **Session C** — Phase 3 (windows CI). Expect 1-2 iterations to reach green.

Each session is bounded and independently reviewable — no stacking of in-flight branches.
