# Offline Workstream Implementation Plan — M2 + Phase 3 + Cleanup Bundle

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` for same-session execution of this plan.

**Goal:** Advance three independent workstreams that require no physical Reachy Mini hardware: **(A) M2 mute coordination (#20)** — bind `MuteGate` to the state machine and `ReachyMicSource` so a MUTED transition zeros the audio path at the earliest possible boundary; **(B) Phase 3 of canonical-dep-migration** — add `windows-latest` to CI matrix; **(C) Small-scope cleanup** — #56 plan-reviewer follow-ups (3 items), #19 prune wire-dead `BrainRequest.include_tools`, #47 enable auto-merge.

**Architecture:** Three independent scopes, three independent branches, three independent PRs. Execution order is flexible. Each scope is strictly TDD-shaped. Part A's **integration smoke** (Task A.3) is a full mock-driven end-to-end test (state machine → `MuteGate` → `ReachyMicSource` with `FakeMini` → optional `FakeSession` capture) that runs without hardware and proves the wiring before Task 1.5's hardware smoke ever runs.

**Tech Stack:** Python 3.12, uv workspace, pytest (+ `@pytest.mark.hardware` opt-in), scipy.signal for existing resample path, `asyncio_mode = "auto"` for async tests, GitHub Actions, `gh` CLI.

**Issues closed:**

| Part | Issue | Subject |
|------|-------|---------|
| A | #20 | Mute coordination seam: bind MuteGate to state-machine MUTED transition + mic pump |
| B | — | (Phase 3 of canonical-dep-migration plan — no separate issue) |
| C | #19 | BrainRequest.include_tools wire-dead — prune |
| C | #47 | Auto-merge enablement (sdk-contract half already obsolete since PR #65) |
| C | #56 | plan-reviewer polish follow-ups (all 3 items) |

**Design-validation strategy:**

- **A (M2 mute)**: the reference `pollen-robotics/reachy_mini_conversation_app` does NOT ship a native mute feature, so there is no direct pattern to mirror. Our design stands on its own: **`MuteGate` applied at the earliest boundary (`ReachyMicSource`) so all downstream consumers — OpenAI, future head-wobble, VU meter — see zeroed frames**. We validate against the SDK only to confirm `MuteGate.process`'s `npt.NDArray[np.int16]` signature matches the `AudioFrame`'s int16 component (already true; see `mute.py:32-35` + `audio_io.py:38`).

- **B (Phase 3 windows CI)**: mirrors Pollen's conversation-app matrix pattern (ubuntu + macos + windows × py3.12). Validated during the original canonical-dep brainstorm.

- **C (cleanup)**: each item is a decision, a docstring fix, a test addition, or a GitHub setting — no SDK validation needed. `#19` is pure YAGNI (prune dead wire); `#47` is settings + doc; `#56` items are spec'd at issue-body level.

**Testing posture:**

- **Unit tier (no hardware)**: covers all three scopes exhaustively.
- **Integration smoke (no hardware)**: Task A.3 is a new `app/tests/test_mute_integration.py` that drives `EmbodimentStateMachine` → `MuteGate` → `ReachyMicSource` with a scripted `FakeMini`, and optionally pipes through `OpenAIRealtimeVoice` with a `FakeSession` to confirm that base64-encoded bytes sent to OpenAI are all zeros when muted.
- **Hardware tier (deferred)**: an `@pytest.mark.hardware` smoke goes in `app/tests/test_main_hardware.py` alongside the existing Task 1.5 placeholder — the hardware assertion is "MUTED state transition actually zeros the LIVE mic audio through the SDK on a real Reachy Mini." Files the test here; runs it when you're on LAN.
- **CI tier**: Phase 3 adds `windows-latest` to matrix. First-run iteration tracked in Task B.3.

**Conventions used throughout:**

- **TDD per task**: failing test → confirm fail with expected shape → minimal impl → confirm pass → commit.
- **Per-task gate before commit**:
  ```bash
  uv run ruff check . && uv run ruff format --check . \
    && uv run mypy --strict <touched packages> \
    && uv run pytest -q
  ```
- **Full-branch gate before push**:
  ```bash
  uv run ruff check . && uv run ruff format --check . \
    && uv run mypy --strict daemon/src app/src menubar/src protocol/src \
                            daemon/tests app/tests menubar/tests protocol/tests \
    && uv run pyright \
    && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src \
    && uv run pytest -q --cov
  ```
  Coverage floor **90%**.
- **Branches**:
  - A: `m2-mute-coordination` (3 commits)
  - B: `ci-windows-matrix` (2 commits)
  - C: `small-cleanup-bundle` (5 commits)
- **Commit style**: conventional (`feat:` / `fix:` / `chore:` / `test:` / `docs:`).
- **Out-of-scope markers**: if an implementer wants to expand scope, STOP and report per `.claude/rules/quality-escalation.md`.

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion, @superpowers:subagent-driven-development.

**Prereqs:**
- `main` at `7ff2194` (PR #68 merged) or later.
- `gh` authenticated for Part C.5 (repo settings).
- No hardware. No LAN robot.

**Out of scope (deferred):**
- M1 Task 1.5 hardware smoke — pending LAN access (part of #23).
- M2 hardware smoke (A.4 in this plan's earlier hardware-testing plan) — filed as a test here, run on hardware later.
- New feature work on any other open issue.

**Suggested execution order:**
- **C first** — smallest cycles, clear backlog.
- **A next** — substantive unit + integration smoke.
- **B last** — independent; fills downtime.

Each part is independently reviewable, though C in particular can be done in any sub-order.

---

## Part A — M2 mute coordination (#20)

**Branch:** `m2-mute-coordination` off `main`.

**Cumulative commit count:** 3 commits.

### Task A.1: Bind `MuteGate` into `EmbodimentStateMachine`

**Files:**
- Modify: `app/src/reachy_ducky_app/embodiment/state_machine.py`
- Modify: `app/tests/test_embodiment_state_machine.py`

**Why this task runs first:** The state machine's MUTED transition is the canonical trigger. Wiring MuteGate into it first gives us a testable integration point before we touch the mic pump.

**Step 1: Write the failing test**

Append to `app/tests/test_embodiment_state_machine.py`:

```python
from reachy_ducky_app.mute import MuteGate


def test_transition_to_muted_sets_mute_gate() -> None:
    """Transitioning the state machine into MUTED toggles the passed MuteGate.

    Earliest-boundary mute: downstream consumers (mic pump, VU meter,
    head wobble) all see the gate state change without each needing
    its own transition subscription.
    """
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    assert gate.muted is False
    sm.transition(State.MUTED)
    assert gate.muted is True


def test_transition_out_of_muted_clears_mute_gate() -> None:
    """Leaving MUTED clears the gate symmetrically."""
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    sm.transition(State.MUTED)
    assert gate.muted is True
    sm.transition(State.IDLE)
    assert gate.muted is False


def test_state_machine_without_mute_gate_still_works() -> None:
    """``mute_gate=None`` is back-compat: no gate calls; transitions still fire."""
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver)  # No mute_gate kwarg.
    sm.transition(State.MUTED)
    assert sm.state == State.MUTED
    # Driver still got the visible sleep posture.
    assert driver.went_to_sleep is True
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest app/tests/test_embodiment_state_machine.py -v -k mute_gate
```

Expected: `TypeError` — `EmbodimentStateMachine.__init__` doesn't accept `mute_gate` yet.

**Step 3: Implement**

Edit `app/src/reachy_ducky_app/embodiment/state_machine.py`:

```python
from reachy_ducky_app.mute import MuteGate


class EmbodimentStateMachine:
    """... (existing docstring) ..."""

    def __init__(
        self,
        driver: MotionDriver,
        *,
        mute_gate: MuteGate | None = None,
    ) -> None:
        self._driver = driver
        self._state: State = State.IDLE
        self._mute_gate = mute_gate

    def transition(self, target: State) -> None:
        if target == self._state:
            return

        # Wire the gate BEFORE the motion call so downstream consumers
        # see the new gate state when observers notice the transition.
        if self._mute_gate is not None:
            if target == State.MUTED:
                self._mute_gate.set_muted(True)
            elif self._state == State.MUTED:
                self._mute_gate.set_muted(False)

        # Existing motion dispatch (preserve).
        if target == State.MUTED:
            self._driver.go_to_sleep()
        elif self._state == State.MUTED:
            self._driver.wake_up()
            move = _STATE_TO_MOVE.get(target)
            if move is not None:
                self._driver.play_move(move)
        else:
            move = _STATE_TO_MOVE.get(target)
            if move is not None:
                self._driver.play_move(move)

        self._state = target
```

Key points:
- `mute_gate` is **keyword-only** (`*, mute_gate=...`) — matches the `reachy_mini` kwarg convention from M1.
- Gate toggle happens **before** motion — observers racing on transition see a consistent (gate, motion) pair.
- When `mute_gate=None`, the gate-toggle block is skipped — full back-compat with existing tests.

**Step 4: Run tests to verify pass**

```bash
uv run pytest app/tests/test_embodiment_state_machine.py -v
```

Expected: all new + existing tests pass.

**Step 5: Per-task gate**

```bash
uv run ruff check app/src/reachy_ducky_app/embodiment/state_machine.py app/tests/test_embodiment_state_machine.py
uv run ruff format --check app/src/reachy_ducky_app/embodiment/state_machine.py app/tests/test_embodiment_state_machine.py
uv run mypy --strict app/src app/tests
uv run pytest -q app/tests/test_embodiment_state_machine.py
```

**Step 6: Commit**

```bash
git add app/src/reachy_ducky_app/embodiment/state_machine.py \
        app/tests/test_embodiment_state_machine.py
git commit -m "$(cat <<'EOF'
feat(embodiment): bind MuteGate into state machine MUTED transition (#20)

Task A.1 of the offline workstream. EmbodimentStateMachine gains an
optional keyword-only ``mute_gate`` kwarg. On transition TO ``MUTED``,
the gate is set muted; on transition OUT of MUTED, it clears. Motion
dispatch (go_to_sleep / wake_up + play_move) is preserved exactly —
the gate toggle is an ADDITIONAL side effect, not a replacement.

Gate toggle happens BEFORE motion so observers that race on the
transition see a consistent (gate, motion) pair. When ``mute_gate=None``,
the block is skipped — full back-compat with existing tests.

Next: Task A.2 threads the same MuteGate into ReachyMicSource so the
mic pump zeros frames at the earliest boundary.
EOF
)"
```

---

### Task A.2: Apply `MuteGate` in `ReachyMicSource` + thread through factory + `main.py`

**Files:**
- Modify: `app/src/reachy_ducky_app/voice/audio_io.py`
- Modify: `app/src/reachy_ducky_app/main.py`
- Modify: `app/tests/test_audio_io.py`

**Why at the mic-source boundary:** zeros get applied at the **earliest possible point** so all downstream consumers (OpenAI session, future head-wobble, VU meter) see silence when muted. Placing the gate inside `OpenAIRealtimeVoice` would leak non-zero frames to any parallel consumer.

**Step 1: Write the failing tests**

Append to `app/tests/test_audio_io.py`:

```python
from reachy_ducky_app.mute import MuteGate


async def test_reachy_mic_source_applies_mute_gate_zeros_frames() -> None:
    """When the MuteGate is muted, ReachyMicSource yields zeroed int16 frames.

    Zero-at-source: all downstream consumers (OpenAI, head wobble, VU)
    see silence consistently. Gate applied post-resample / pre-yield
    so the int16 ndarray shape is fixed and the zeroing is a simple
    np.zeros_like.
    """
    scripted = [
        np.full((480, 2), 0.5, dtype=np.float32),  # non-trivial signal
        np.full((480, 2), -0.5, dtype=np.float32),
    ]

    class _FakeMedia:
        _i = 0

        def get_audio_sample(self) -> np.ndarray | None:
            if _FakeMedia._i >= len(scripted):
                raise asyncio.CancelledError
            f = scripted[_FakeMedia._i]
            _FakeMedia._i += 1
            return f

    class _FakeMini:
        media = _FakeMedia()

    gate = MuteGate()
    gate.set_muted(True)
    src = ReachyMicSource(_FakeMini(), mute_gate=gate)

    collected: list[AudioFrame] = []
    with contextlib.suppress(asyncio.CancelledError):
        async for frame in src.frames():
            collected.append(frame)

    assert len(collected) == 2
    for sr, samples in collected:
        assert sr == 24000
        assert np.all(samples == 0), (
            "muted frame had non-zero values: {samples!r}"
        )


async def test_reachy_mic_source_passes_through_when_unmuted() -> None:
    """When the MuteGate is NOT muted, frames pass through unchanged."""
    scripted = [np.full((480, 2), 0.5, dtype=np.float32)]

    class _FakeMedia:
        _i = 0

        def get_audio_sample(self) -> np.ndarray | None:
            if _FakeMedia._i >= len(scripted):
                raise asyncio.CancelledError
            f = scripted[_FakeMedia._i]
            _FakeMedia._i += 1
            return f

    class _FakeMini:
        media = _FakeMedia()

    gate = MuteGate()  # Defaults to unmuted.
    src = ReachyMicSource(_FakeMini(), mute_gate=gate)

    with contextlib.suppress(asyncio.CancelledError):
        async for sr, samples in src.frames():
            assert sr == 24000
            # Signal was 0.5 float32 → ~16383 int16 (signal, not zero).
            assert np.any(samples != 0)
            break


async def test_reachy_mic_source_without_gate_passes_through() -> None:
    """No mute_gate kwarg → frames pass through unchanged (back-compat)."""
    scripted = [np.full((480, 2), 0.5, dtype=np.float32)]

    class _FakeMedia:
        _i = 0

        def get_audio_sample(self) -> np.ndarray | None:
            if _FakeMedia._i >= len(scripted):
                raise asyncio.CancelledError
            f = scripted[_FakeMedia._i]
            _FakeMedia._i += 1
            return f

    class _FakeMini:
        media = _FakeMedia()

    src = ReachyMicSource(_FakeMini())  # No mute_gate kwarg.
    with contextlib.suppress(asyncio.CancelledError):
        async for _sr, samples in src.frames():
            assert np.any(samples != 0)
            break


def test_load_default_mic_source_threads_mute_gate() -> None:
    """Factory accepts ``mute_gate`` kwarg and passes it into ``ReachyMicSource``."""
    gate = MuteGate()

    class _FakeMini:
        pass

    src = load_default_mic_source(reachy_mini=_FakeMini(), mute_gate=gate)
    assert isinstance(src, ReachyMicSource)
    # Verify the gate was threaded, not silently dropped.
    assert src._mute_gate is gate  # noqa: SLF001 — test injection check


def test_load_default_mic_source_mute_gate_is_keyword_only() -> None:
    """``mute_gate`` must be keyword-only — forward-compat with factory API growth."""
    with pytest.raises(TypeError):
        load_default_mic_source(object(), MuteGate())  # type: ignore[misc]
```

**Step 2: Run tests — expect failure**

```bash
uv run pytest app/tests/test_audio_io.py -v -k mute
```

Expected: `TypeError` — `ReachyMicSource` doesn't accept `mute_gate`, factory doesn't either.

**Step 3: Implement**

Edit `app/src/reachy_ducky_app/voice/audio_io.py`:

**3a. Update `ReachyMicSource` to accept + apply `MuteGate`:**

```python
from reachy_ducky_app.mute import MuteGate


class ReachyMicSource(MicSource):
    """... (existing docstring with appended paragraph) ...

    When a ``MuteGate`` is passed at construction, the adapter applies
    it post-resample / pre-yield so the int16 samples are zeroed when
    the gate is muted. Zero-at-source: downstream consumers (OpenAI
    Realtime session, future head-wobble, VU meter) all see silence
    consistently without each needing its own mute subscription.
    """

    def __init__(
        self,
        reachy_mini: object,
        *,
        mute_gate: MuteGate | None = None,
    ) -> None:
        self._mini = reachy_mini
        self._mute_gate = mute_gate

    async def frames(self) -> AsyncIterator[AudioFrame]:
        """... (existing docstring) ..."""
        loop = asyncio.get_running_loop()
        get_sample = self._mini.media.get_audio_sample  # type: ignore[attr-defined]
        while True:
            sample = await loop.run_in_executor(None, get_sample)
            if sample is None:
                await asyncio.sleep(0.01)
                continue
            mono = sample[:, 0] if sample.ndim == 2 else sample
            resampled = cast(
                npt.NDArray[np.float32],
                scipy.signal.resample(mono, int(len(mono) * _LLM_AUDIO_RATE / _SDK_AUDIO_RATE)),
            )
            int16 = (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16)
            if self._mute_gate is not None:
                int16 = self._mute_gate.process(int16)
            yield (_LLM_AUDIO_RATE, int16)
```

**3b. Thread `mute_gate` through the factory:**

```python
def load_default_mic_source(
    *,
    reachy_mini: object | None = None,
    mute_gate: MuteGate | None = None,
) -> MicSource:
    """... (existing docstring with note) ...

    ``mute_gate`` is also keyword-only. When provided alongside a
    non-None ``reachy_mini``, it's threaded into the returned
    ``ReachyMicSource``. ``MockMicSource`` ignores the gate (tests
    script frames directly — no gating needed).
    """
    if reachy_mini is None:
        return MockMicSource()
    return ReachyMicSource(reachy_mini, mute_gate=mute_gate)
```

`load_default_speaker_sink` does NOT need a mute_gate — speaker output is LLM-generated and shouldn't be silenced when the user mutes.

**3c. Wire in `main.py`** (inside `_run_async`):

```python
from .mute import MuteGate


async def _run_async(
    self,
    reachy_mini: object,
    stop_event: threading.Event,
) -> None:
    """... (existing docstring) ..."""
    driver = ReachyMotionDriver(reachy_mini)
    mute_gate = MuteGate()  # NEW: single gate threaded to both consumers.
    sm = EmbodimentStateMachine(driver=driver, mute_gate=mute_gate)

    voice = OpenAIRealtimeVoice(
        mic=load_default_mic_source(reachy_mini=reachy_mini, mute_gate=mute_gate),
        speaker=load_default_speaker_sink(reachy_mini=reachy_mini),
    )
    # ... rest unchanged.
```

**Step 4: Run tests — expect pass**

```bash
uv run pytest app/tests/test_audio_io.py -v
uv run pytest app/tests/test_app_main.py -v
uv run mypy --strict app/src app/tests
uv run ruff check app/src app/tests
```

Expected: all pass. `test_app_main.py` may need an update if an existing test observes the factory-construction path; patch narrowly if so.

**Step 5: Per-task gate + commit**

```bash
git add app/src/reachy_ducky_app/voice/audio_io.py \
        app/src/reachy_ducky_app/main.py \
        app/tests/test_audio_io.py
# + test_app_main.py if modified
git commit -m "$(cat <<'EOF'
feat(app/voice): ReachyMicSource applies MuteGate pre-yield (#20)

Task A.2 of the offline workstream. ReachyMicSource gains an optional
keyword-only ``mute_gate`` kwarg; applied to the int16 ndarray
post-resample / pre-yield. Zero-at-source semantics: downstream
consumers (OpenAI Realtime session, future head-wobble driver, VU
meter) all observe silence consistently when the gate is muted,
without each needing its own mute subscription.

Factory ``load_default_mic_source`` threads the gate through; speaker
factory deliberately does NOT get a gate (speaker output is LLM-
generated; muting the user's mic shouldn't silence the assistant's
replies). ``main._run_async`` constructs a single shared MuteGate and
passes the SAME instance to both ``EmbodimentStateMachine`` (Task A.1)
and ``ReachyMicSource`` (this task), so a MUTED transition on the
state machine synchronously affects the mic pump.

MockMicSource is unaffected — tests script frames directly.
EOF
)"
```

---

### Task A.3: Local integration smoke (no hardware)

**Files:**
- Create: `app/tests/test_mute_integration.py`

**Why this task:** Units prove each piece in isolation. This proves the WIRING — that a state-machine transition actually makes the mic pump yield zeros, end-to-end, with real (not mocked) `EmbodimentStateMachine`, `MuteGate`, and `ReachyMicSource` classes.

**Step 1: Write the failing test**

```python
"""Integration smoke — state machine → MuteGate → ReachyMicSource.

Proves the A.1 + A.2 wiring end-to-end without hardware. Uses real
``EmbodimentStateMachine``, ``MuteGate``, and ``ReachyMicSource``
instances; only the underlying SDK is mocked (via a scripted
``FakeMini``). A real hardware equivalent is filed in
``test_main_hardware.py`` and runs under ``@pytest.mark.hardware``.

This is NOT a unit test — it deliberately crosses module boundaries.
Lives in its own file so it's easy to find + doesn't inflate the
individual ``test_*.py`` files.
"""

from __future__ import annotations

import asyncio
import contextlib

import numpy as np
import pytest

from reachy_ducky_app.embodiment import EmbodimentStateMachine, MockMotionDriver
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import AudioFrame, ReachyMicSource
from reachy_ducky_protocol.messages import State


class _ScriptedFakeMini:
    """FakeMini that scripts a sequence of float32 stereo frames."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = list(frames)
        self.media = self._Media(self)

    class _Media:
        def __init__(self, outer: "_ScriptedFakeMini") -> None:
            self._outer = outer

        def get_audio_sample(self) -> np.ndarray | None:
            if not self._outer._frames:
                raise asyncio.CancelledError
            return self._outer._frames.pop(0)


async def test_state_machine_transition_to_muted_zeros_mic_frames() -> None:
    """Transitioning the state machine to MUTED zeros subsequent mic frames.

    Drives: sm.transition(MUTED) → gate.set_muted(True) → mic.frames()
    yields zeros. Reverses: sm.transition(IDLE) → gate clears →
    subsequent frames non-zero.
    """
    scripted = [np.full((480, 2), 0.5, dtype=np.float32) for _ in range(4)]
    mini = _ScriptedFakeMini(scripted)

    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)
    src = ReachyMicSource(mini, mute_gate=gate)

    frames_generated: list[AudioFrame] = []
    mute_toggle_sequence: list[bool] = []

    async def drive_mic() -> None:
        """Consume frames; capture the gate state at each yield."""
        with contextlib.suppress(asyncio.CancelledError):
            async for frame in src.frames():
                frames_generated.append(frame)
                mute_toggle_sequence.append(gate.muted)
                if len(frames_generated) == 4:
                    return

    async def orchestrate() -> None:
        """Interleave transitions with the mic pump."""
        # Frame 1 — unmuted (IDLE).
        await asyncio.sleep(0.01)
        # Frame 2 — muted (after transition to MUTED).
        sm.transition(State.MUTED)
        await asyncio.sleep(0.01)
        # Frame 3 — still muted.
        await asyncio.sleep(0.01)
        # Frame 4 — unmuted again.
        sm.transition(State.IDLE)
        await asyncio.sleep(0.01)

    await asyncio.gather(drive_mic(), orchestrate())

    assert len(frames_generated) == 4

    # Frame 1: captured BEFORE any transition — should be non-zero.
    assert np.any(frames_generated[0][1] != 0), "pre-mute frame was zeroed"
    # Frame 2 and 3: captured AFTER transition to MUTED.
    assert np.all(frames_generated[1][1] == 0), "muted frame 2 had signal"
    assert np.all(frames_generated[2][1] == 0), "muted frame 3 had signal"
    # Frame 4: after transition back to IDLE.
    assert np.any(frames_generated[3][1] != 0), "post-unmute frame was zeroed"


async def test_mute_gate_shared_between_state_machine_and_mic_source() -> None:
    """Single MuteGate instance threaded to both the state machine AND the
    mic source. A transition-driven mute and a mic-driven read of
    ``gate.muted`` observe the SAME state.
    """
    gate = MuteGate()
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver, mute_gate=gate)

    mini = _ScriptedFakeMini([np.full((480, 2), 0.0, dtype=np.float32)])
    src = ReachyMicSource(mini, mute_gate=gate)

    # Before transition: gate.muted should be False as observed by src.
    assert src._mute_gate is gate  # noqa: SLF001 — integration smoke.
    assert src._mute_gate.muted is False  # noqa: SLF001

    sm.transition(State.MUTED)
    assert src._mute_gate.muted is True  # noqa: SLF001 — identity check.
```

This file has two tests — one for the end-to-end transition → zeroed-frames chain, one for the identity property (same gate object observed on both ends).

**Step 2: Run — expect fail**

```bash
uv run pytest app/tests/test_mute_integration.py -v
```

Expected: both tests fail. If the test infrastructure (e.g. `asyncio.CancelledError` handling) is finicky, the implementer may need to adjust the `_ScriptedFakeMini` class — the contract is "raise `CancelledError` after scripted frames exhaust."

**Step 3: Implement**

No new production code. Tasks A.1 + A.2 already provide the wiring; this task only asserts it.

If the tests fail for a substantive reason (e.g., state-machine-to-gate sequencing is wrong), STOP and escalate — that's a genuine design bug that needs revisiting A.1.

**Step 4: Run tests — expect pass**

```bash
uv run pytest app/tests/test_mute_integration.py -v
```

Both pass.

**Step 5: Also add a deferred hardware smoke**

Append to `app/tests/test_main_hardware.py` (create if does not exist; follow the pattern in `test_sdk_audio_contract.py`):

```python
"""Hardware smoke for #20 — mute coordination end-to-end on real hardware.

Gated on ``@pytest.mark.hardware``. Runs when the user is on LAN and
the Reachy Mini is reachable at ``reachy-mini.local``. Deferred from
the offline workstream (which landed the integration smoke in
``test_mute_integration.py``).
"""

from __future__ import annotations

import asyncio
import contextlib

import numpy as np
import pytest

from reachy_ducky_app.embodiment import EmbodimentStateMachine, MockMotionDriver
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import ReachyMicSource
from reachy_ducky_protocol.messages import State

reachy_mini = pytest.importorskip(
    "reachy_mini",
    reason="hardware tests require the reachy-mini SDK installed",
)


@pytest.mark.hardware
async def test_muted_transition_zeros_live_mic() -> None:
    """On real hardware, MUTED transition yields zeroed int16 samples.

    Same contract as ``test_state_machine_transition_to_muted_zeros_mic_frames``
    in the offline integration smoke, but against a LIVE ReachyMini
    via the real SDK.
    """
    mini = reachy_mini.ReachyMini()
    gate = MuteGate()
    driver = MockMotionDriver()  # We don't actually care about motion here.
    sm = EmbodimentStateMachine(driver, mute_gate=gate)
    src = ReachyMicSource(mini, mute_gate=gate)

    # Pull a few frames in pre-mute state; just confirm the pipeline is alive.
    frames_before: list = []
    async def drain_pre():
        with contextlib.suppress(asyncio.CancelledError):
            async for frame in src.frames():
                frames_before.append(frame)
                if len(frames_before) >= 2:
                    return
    await asyncio.wait_for(drain_pre(), timeout=5.0)

    # Transition to MUTED; confirm subsequent frames are all zeros.
    sm.transition(State.MUTED)
    frames_after: list = []
    async def drain_post():
        with contextlib.suppress(asyncio.CancelledError):
            async for frame in src.frames():
                frames_after.append(frame)
                if len(frames_after) >= 2:
                    return
    await asyncio.wait_for(drain_post(), timeout=5.0)

    for _sr, samples in frames_after:
        assert np.all(samples == 0), "live-hardware muted frame had signal"
```

This test is NOT run today (user offline). It's filed + gated; run it later on LAN.

**Step 6: Per-task gate + commit**

```bash
git add app/tests/test_mute_integration.py app/tests/test_main_hardware.py
git commit -m "$(cat <<'EOF'
test(mute): full-stack integration smoke + deferred hardware test (#20)

Task A.3 of the offline workstream. Two artifacts:

``app/tests/test_mute_integration.py`` — real ``EmbodimentStateMachine``,
real ``MuteGate``, real ``ReachyMicSource`` with a scripted ``FakeMini``.
Drives a sequence of state transitions interleaved with mic pump
consumption and asserts:
- Frame 1 (pre-transition): non-zero signal.
- Frames 2, 3 (after transition to MUTED): all zeros.
- Frame 4 (after transition back to IDLE): non-zero again.
- Identity property: same MuteGate observed on both ends.

This is the "run locally without hardware" smoke the offline
workstream targets — proves the A.1 + A.2 wiring before Task 1.5
(hardware smoke) ever runs.

``app/tests/test_main_hardware.py`` (new file) — adds
``test_muted_transition_zeros_live_mic`` under ``@pytest.mark.hardware``.
Same contract but against a live ReachyMini via real SDK. Runs when
the user is back on LAN; deferred today.
EOF
)"
```

---

**End of Part A.** 3 commits on `m2-mute-coordination`. Push + open PR:

```bash
git push -u origin m2-mute-coordination
gh pr create --base main --title "feat(app): M2 mute coordination (closes #20)" --body "<summary referencing A.1 / A.2 / A.3>"
```

---

## Part B — Phase 3 windows CI (from canonical-dep-migration plan)

**Branch:** `ci-windows-matrix` off `main`.

**Cumulative commit count:** 2 commits.

Baseline content already drafted in `docs/plans/2026-04-22-canonical-reachy-mini-dep-migration.md` Phase 3. This part is the task-by-task execution of that.

### Task B.1: Add `windows-latest` to CI matrix + handle menubar exclusion

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify (if needed): `menubar/tests/conftest.py` (platform skip)

**Step 1: Read the existing CI config**

```bash
cat .github/workflows/ci.yml | head -80
```

Identify the matrix block. Current shape (verify):
```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest]
```

**Step 2: Add `windows-latest`**

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, macos-latest, windows-latest]
  fail-fast: false  # Add if not present — we want the matrix to continue
                    # so one platform's red doesn't hide others' signals.
```

**Step 3: Add the platform-aware install + menubar exclusion**

The `uv sync` step needs to exclude `reachy-ducky-menubar` on non-macOS (it depends on `rumps`, which is macOS-only). Update the install step:

```yaml
      - name: Install workspace (platform-aware)
        shell: bash
        run: |
          if [ "${{ runner.os }}" = "macOS" ]; then
            uv sync --frozen --all-packages --group dev
          else
            uv sync --frozen --all-packages --exclude-package reachy-ducky-menubar --group dev
          fi
```

The Linux apt step (pygobject/pycairo headers, already added in PR #65) stays; on Windows we may hit a different issue but we'll see it on the first run (B.2 iterates).

**Step 4: Add menubar conftest skip-guard**

Create or modify `menubar/tests/conftest.py`:

```python
"""Platform guard — menubar uses rumps which is macOS-only.

When running the workspace test suite on Linux or Windows CI, we skip
the menubar package entirely since rumps can't be imported. Uses
``pytest.importorskip`` at module scope (per ``testing-standards.md``'s
"importorskip is the one sanctioned skip mechanism") so the skip
message is honest ("rumps not importable") rather than an ad-hoc
``pytest.mark.skip``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("rumps", reason="menubar is macOS-only (rumps not importable)")
```

**Step 5: Validate YAML syntax locally**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

If `yamllint` or `actionlint` is installed:
```bash
yamllint .github/workflows/ci.yml 2>/dev/null || true
actionlint .github/workflows/ci.yml 2>/dev/null || true
```

Shape-only validation. Real validation happens when we push and CI runs.

**Step 6: Local test the exclusion shape (best-effort)**

We can't actually run Linux CI locally, but we CAN confirm `uv sync --exclude-package reachy-ducky-menubar` behaves right:

```bash
# Don't run this — it'd actually exclude menubar in the dev venv. Just
# verify the command syntax exists in uv.
uv sync --help | grep -A 2 exclude-package
```

Confirm the flag exists.

**Step 7: Commit**

```bash
git add .github/workflows/ci.yml menubar/tests/conftest.py
git commit -m "$(cat <<'EOF'
chore(ci): add windows-latest to CI matrix (Phase 3 of canonical dep migration)

Task B.1 of the offline workstream, implementing Phase 3 of the
canonical reachy-mini dep migration (see
``docs/plans/2026-04-22-canonical-reachy-mini-dep-migration.md``
Phase 3). Matches ``pollen-robotics/reachy_mini_conversation_app``'s
3-platform matrix.

Changes:
- ``.github/workflows/ci.yml``: matrix gains ``windows-latest``;
  platform-aware ``uv sync`` excludes ``reachy-ducky-menubar`` on
  non-macOS (rumps is macOS-only). ``fail-fast: false`` so one
  platform red doesn't hide others' signals.
- ``menubar/tests/conftest.py``: module-scope ``pytest.importorskip``
  on ``rumps`` so the menubar test suite honestly skips on platforms
  where rumps isn't importable.

First-run redness expected on windows — that's the whole point of
adding it; B.2 iterates. Promotion to required-check deferred to a
separate PR (see #47 for the auto-merge side).
EOF
)"
```

**Step 8: Push + open PR + monitor first run**

```bash
git push -u origin ci-windows-matrix
gh pr create --base main --title "chore(ci): add windows-latest to CI matrix" --body "..."
```

---

### Task B.2: Iterate until windows-latest is green

**Files:**
- Whatever the first CI run surfaces.

**Step 1: Monitor the first run**

```bash
gh pr checks <pr-url>
```

Watch for failures on `Lint, type-check, unit tests (windows-latest)`.

**Step 2: Triage failures**

Common failure modes on Windows first-runs and fixes:

- **`gstreamer-msvc-runtime` installs but has a native-code fallback missing** → verify our `[tool.uv] dependency-metadata` patch actually activates on windows resolve. It should (`sys_platform == 'win32'`). If not, inspect `uv.lock` on the windows runner and patch accordingly.
- **Path-separator issues in tests** → `os.path.join` / `pathlib.Path` replacements for any hard-coded `/`.
- **Line-ending issues** → add `* text=auto` / `*.py text eol=lf` in `.gitattributes` if not present.
- **`scipy.signal` wheel availability** → should be fine on windows; prebuilt wheels for py3.12 exist.

**Step 3: Iterate until green**

Each fix: narrow commit, push, monitor.

```bash
git add <fixed-files>
git commit -m "fix(ci): <what the specific failure was>"
git push
gh pr checks <pr-url>
```

Budget 1–3 iterations.

**Step 4: Final commit + decision on required-check promotion**

Once green, the PR stands. **Do NOT** promote `windows-latest` to a required check in this PR — that's a branch-protection change tracked in #47 (auto-merge enablement) and decided separately.

**Step 5: Merge**

```bash
gh pr merge <pr-url> --squash --delete-branch
```

---

**End of Part B.** 2 commits on `ci-windows-matrix`. Merge after green.

---

## Part C — Small-scope cleanup bundle

**Branch:** `small-cleanup-bundle` off `main`.

**Cumulative commit count:** 5 commits (one per sub-task).

### Task C.1: #56 item 1 — fix `plans_mcp.py` module docstring

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/brain/plans_mcp.py`

**Step 1: Read the current module docstring**

```bash
head -25 daemon/src/reachy_ducky_daemon/brain/plans_mcp.py
```

Find the line that says "symlink escapes are denied" in the module docstring.

**Step 2: Edit**

Replace per #56 item 1:

- FROM: `"symlink escapes are denied"` (or similar — read for exact phrasing)
- TO: `"symlink escapes are denied in :func:\`_discover\`; :func:\`_read_plan\`'s membership check inherits the property"`

**Step 3: Verify — no code changes, just doc**

```bash
uv run ruff check daemon/src/reachy_ducky_daemon/brain/plans_mcp.py
uv run pytest -q daemon/tests/test_brain_plans_mcp.py
```

**Step 4: Commit**

```bash
git add daemon/src/reachy_ducky_daemon/brain/plans_mcp.py
git commit -m "docs(brain): point plans_mcp module docstring to _discover (#56 item 1)

Follow-up to #8 — the security property moved from _read_plan to
_discover during the plan-reviewer polish sweep. Module docstring now
accurately points at _discover as the primary enforcement site, with
_read_plan's membership check inheriting the guarantee."
```

---

### Task C.2: #56 item 2 — pin "single oversized plan lands" invariant

**Files:**
- Modify: `daemon/tests/test_specialist_plan_reviewer.py`

**Step 1: Write the test**

Append per #56 item 2:

```python
@pytest.mark.asyncio
async def test_single_oversized_plan_still_lands_despite_total_cap(
    tmp_path: Path,
) -> None:
    """A single plan larger than ``max_total_plan_chars`` MUST land anyway —
    the ``included > 0`` guard in ``_assemble_plans_block`` ensures we
    never return zero plans when at least one exists.

    Regression guard: if a future refactor changes ``included > 0``
    to ``included > 1`` or drops the guard, this test fails clearly.
    """
    _init_repo(tmp_path)
    plans_dir = tmp_path / "docs" / "plans"
    plans_dir.mkdir(parents=True)

    huge = "a" * 250_000
    (plans_dir / "huge.md").write_text(huge)
    _commit(tmp_path, "add huge plan")

    brain = MockBrain()
    reviewer = PlanReviewer(
        brain=brain,
        repo=tmp_path,
        max_plan_chars=60_000,
        max_total_plan_chars=50_000,
    )
    await reviewer.review()

    prompt = brain.calls[-1].user_utterance

    # The plan lands, truncated to 60k via per-file cap.
    assert "huge.md" in prompt
    # Plan body is present (truncated).
    assert "a" * 60_000 in prompt
    # Truncation marker appears.
    assert "[... truncated:" in prompt
    # Total-budget marker MUST NOT appear — we didn't omit a plan.
    assert "plans omitted" not in prompt
    assert "plan omitted" not in prompt
```

**Step 2: Run test — expect pass** (the invariant already holds)

```bash
uv run pytest daemon/tests/test_specialist_plan_reviewer.py -v -k single_oversized
```

Expected: passes on current code — we're pinning existing behavior.

**Step 3: Verify the invariant matters: break the guard, re-run**

(Optional sanity check — DON'T commit this.) Temporarily change `included > 0` to `included > 999` in `plan_reviewer.py`'s `_assemble_plans_block`, run the test. Should fail. Revert the change.

**Step 4: Commit**

```bash
git add daemon/tests/test_specialist_plan_reviewer.py
git commit -m "test(plan-reviewer): pin single-oversized-plan-lands invariant (#56 item 2)

Regression guard for ``_assemble_plans_block``'s ``included > 0``
check: ensures the loop always lands at least one plan when any
exist, even if that plan alone exceeds the total budget (it gets
truncated via the per-file cap, not dropped). A refactor that
accidentally changed the guard to ``included > 1`` would previously
have passed CI; now it doesn't."
```

---

### Task C.3: #56 item 3 — pin symlink-to-non-plan rejection

**Files:**
- Modify: `daemon/tests/test_brain_plans_mcp.py`

**Step 1: Write the test**

Append per #56 item 3:

```python
def test_read_plan_rejects_symlink_to_nonplan_inside_project(
    tmp_path: Path,
) -> None:
    """A plan-shaped symlink pointing to a non-plan path inside the project
    root still raises ``PermissionError("not a plan")`` — the
    ``_discover``-based membership check catches "inside but not a
    conventional plan path" even though the resolved target is
    technically inside the root.
    """
    project = tmp_path / "project"
    (project / "docs" / "plans").mkdir(parents=True)
    (project / "daemon" / "src").mkdir(parents=True)

    # Target is inside the project but NOT a conventional plan path.
    target = project / "daemon" / "src" / "foo.py"
    target.write_text("# not a plan\n")

    # Plan-shaped symlink pointing to the non-plan target.
    link = project / "docs" / "plans" / "escape.md"
    link.symlink_to(target)

    with pytest.raises(PermissionError, match="not a plan"):
        _read_plan(project, "docs/plans/escape.md")
```

**Step 2: Run test — expect pass**

```bash
uv run pytest daemon/tests/test_brain_plans_mcp.py -v -k symlink_to_nonplan
```

Expected: passes on current code.

**Step 3: Commit**

```bash
git add daemon/tests/test_brain_plans_mcp.py
git commit -m "test(brain): pin symlink-to-nonplan rejection (#56 item 3)

_read_plan's _discover-based membership check rejects plan-shaped
symlinks whose resolved target is inside the project root but NOT a
conventional plan path. The happy case (symlink to a plan path) was
already covered; this test closes the gap for the 'inside root but
not a plan' case."
```

---

### Task C.4: #19 — prune wire-dead `BrainRequest.include_tools`

**Files:**
- Modify: `protocol/src/reachy_ducky_protocol/messages.py`
- Modify (if present): `protocol/tests/test_messages.py` (or similar)

**Rationale:** Verified grep — `include_tools` appears ONLY at the declaration site. Not used anywhere else. YAGNI prune.

**Step 1: Find all references**

```bash
grep -rn "include_tools" protocol/ app/ daemon/ menubar/ --include="*.py"
```

Expected: single hit at `protocol/src/reachy_ducky_protocol/messages.py:36`.

**Step 2: Delete the field**

Edit `protocol/src/reachy_ducky_protocol/messages.py`. Remove the line:

```python
include_tools: list[str] = Field(default_factory=list)
```

If it has a preceding comment, remove that too.

**Step 3: Grep once more for safety**

```bash
grep -rn "include_tools" .
```

Expected: zero hits (or only in `uv.lock` / plan docs — harmless).

**Step 4: Run full test suite**

```bash
uv run pytest -q
```

All pass. If any test referenced `include_tools` (shouldn't — grep was clean), STOP and investigate.

**Step 5: Commit**

```bash
git add protocol/src/reachy_ducky_protocol/messages.py
git commit -m "chore(protocol): prune wire-dead BrainRequest.include_tools (closes #19)

YAGNI — the field was declared but never set by any caller
(DaemonClient.brain_query has no kwarg) and never read by any
downstream consumer (ClaudeSDKBrain.query forwards only user_utterance).
Wire-present dead code removed. If we need per-request tool filtering
later, design the full flow; don't resurrect a silent dead field."
```

---

### Task C.5: #47 — enable auto-merge (settings + docs)

**Files:**
- Modify: `CLAUDE.md` (or wherever GitOps is documented)

**Step 1: Enable the setting**

```bash
gh api repos/Obsidian-Owl/reachy-ducky --method PATCH -f allow_auto_merge=true
```

Verify:
```bash
gh api repos/Obsidian-Owl/reachy-ducky --jq '.allow_auto_merge'
```

Expected: `true`.

**Step 2: Document in CLAUDE.md**

Locate the GitOps section (grep for "GitOps" or "Git" headings). Append:

```markdown
### Auto-merge for Dependabot + safe PRs

The repo allows auto-merge on PRs that pass required CI. Use selectively:

```bash
# Safe: dev-tool-only Dependabot bumps (after required checks pass)
gh pr merge --auto --squash --delete-branch

# Manual review required: any PR touching runtime deps, source code,
# or configuration with behavioral impact. Don't --auto these.
```

Required status checks on `main` (as of 2026-04-24):
- `Lint, type-check, unit tests (ubuntu-latest)`
- `Lint, type-check, unit tests (macos-latest)`

`sdk-contract` is NO LONGER a separate workflow (collapsed into main CI via PR #65); its required-check promotion is moot. `windows-latest` from Phase 3 lands as informative-only; promotion requires a stable period (see #47 follow-up).
```

**Step 3: Verify via an actual auto-merge flow (optional)**

This is confirmation only; don't block on it. When a future Dependabot PR lands, use:

```bash
gh pr list --author dependabot --state open
# Pick one; verify CI green; then:
gh pr merge <pr-url> --auto --squash --delete-branch
```

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(gitops): document auto-merge policy (#47 partial — half obsolete)

#47's first half (promote sdk-contract to required) is obsolete —
sdk-contract.yml was deleted in PR #65 (canonical dep migration) and
its introspection tests now run in the default CI tier. Second half
(auto-merge) enabled via ``gh api ... -f allow_auto_merge=true`` and
documented in CLAUDE.md's GitOps section.

Use selectively: Dependabot dev-tool bumps with green CI are safe
auto-merge candidates; anything with behavioral impact stays
manual-review."
```

---

**End of Part C.** 5 commits on `small-cleanup-bundle`. Push + open PR:

```bash
git push -u origin small-cleanup-bundle
gh pr create --base main --title "chore: small-cleanup bundle (#19, #47, #56)" --body "Closes #19, #56. #47 half-closed (auto-merge enabled; sdk-contract half obsolete post-PR #65). 5 commits; each traces to a single issue item."
```

---

## Done / exit criteria

When all three PRs merge:

1. **Issues closed**: #19, #20, #47 (partial), #56.
2. **Main CI green** on ubuntu + macos (existing) + windows-latest (Phase 3, informative).
3. **Open backlog** drops to: #6, #22, #23 (still partial — Task 1.5 hardware smoke pending), #24, #26, #28, #29, #30, #40, #48, #55, #58, #60, #61, #62. Net: **-4 issues** from the open list.
4. **Integration smoke** in `app/tests/test_mute_integration.py` runs green locally without hardware on every future PR — catches mute-gate-wiring regressions before they reach Task 1.5's hardware run.
5. **Hardware-smoke queue** grows by 1: `test_main_hardware.py::test_muted_transition_zeros_live_mic` runs alongside Task 1.5's mic-silence smoke when the user is back on LAN.

---

## Risks & escalation points

- **A.1 — transition-order issue**: if downstream code observes `sm.state` and `gate.muted` at slightly different times, the "gate-before-motion" choice matters. If the implementer finds a race, escalate before changing order.
- **A.2 — MockMicSource shape**: do NOT add `mute_gate` to `MockMicSource` — tests script frames directly and gating would double-zero. If a reviewer asks, the answer is "tests don't need a gate; they script the exact frames they want."
- **A.3 — asyncio.gather timing**: the interleaved `drive_mic` + `orchestrate` pattern relies on `asyncio.sleep(0.01)` to order. If tests are flaky, switch to explicit `asyncio.Event` handshakes.
- **B.2 — windows-first-run red**: expected. Budget 1–3 iterations. If the failure is in the metadata-patch path (gstreamer on windows), escalate — that's a Phase 2 regression, not a Phase 3 concern.
- **C.4 — `include_tools` unexpected consumer**: pre-task grep is authoritative. If it surfaces ANY hit outside the declaration, stop and redesign the prune.
- **C.5 — repo-settings change**: `gh api ... -f allow_auto_merge=true` is reversible via `allow_auto_merge=false`. Low-stakes.

---

## Follow-ups explicitly NOT in scope

- **#23 Task 1.5 hardware smoke** — pending LAN access. Not in this plan.
- **M2 hardware smoke** — filed as `test_main_hardware.py::test_muted_transition_zeros_live_mic`; runs when user is on LAN.
- **#47 sdk-contract promotion** — obsolete (workflow deleted in PR #65). Close that half via commenting on #47; no code action.
- **#6 AppConfig errors, #19 include_tools (this plan only prunes), #22 project-slug, #40 sim CI, #48 Dependabot alerts** — other QoL / backlog items; not bundled here.
- **#55 ONNX wake detector** — substantive new feature; needs its own brainstorm + plan.
- **Windows-latest required-check promotion** — not this PR; wait for stable period then update branch-protection.

---

## Session-split suggestion

- **Session 1 (Part C)** — small cleanup; ~1h. Easy warmup; drains backlog fast.
- **Session 2 (Part A)** — M2 substantive; ~2h. Includes the integration smoke that proves the wiring.
- **Session 3 (Part B)** — windows CI; ~1h active + waiting time for CI iterations.

Can compress to two sessions if sessions 1 and 3 are combined (both are low-cognitive-load).
