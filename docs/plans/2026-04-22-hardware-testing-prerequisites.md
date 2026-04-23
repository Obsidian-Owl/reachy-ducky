# Hardware Testing Prerequisites Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (or `superpowers:subagent-driven-development` for same-session execution) to implement this plan task-by-task.

> **Last refreshed 2026-04-23** after PR #65 (canonical reachy-mini dep migration) landed — install-story prereqs simplified; SDK contract test flow moved from `@pytest.mark.sdk` + SSH to the default CI tier for class-level introspection and `@pytest.mark.hardware` + local Mac client for instance-level tests.

**Goal:** Resolve the two open issues that block end-to-end hardware testing of Reachy Ducky on a real Reachy Mini Wireless: hardware audio I/O (#23) and mute coordination across mic + state machine (#20). After this plan merges, a human can plug in the robot, speak, and hear a response — with a functional mute toggle that proves zeroed audio *and* a visible body-sleep posture.

**Architecture:** Two milestones, each a single-PR-sized change. **M1** adds `ReachyMicSource` / `ReachySpeakerSink` hardware implementations (wrapping `ReachyMini.media.*`), pins the SDK audio surface via a contract test, updates the factories to select hardware when a `reachy_mini` handle is passed, and adds an opt-in hardware smoke. **M2** (depends on M1 merged) threads a single `MuteGate` through `EmbodimentStateMachine` *and* the hardware mic source so the MUTED state transition zeros the audio path in addition to putting the body to sleep.

**Tech Stack:**
- Python 3.12, `uv` workspace
- `reachy_mini` SDK — plain base dep on `app/pyproject.toml` since PR #65; installs cross-platform (macOS arm64, Linux, Windows) via `uv sync --all-packages --group dev`. The on-robot daemon still runs on the Reachy Mini; Mac / Linux dev machines connect as clients over the LAN (default `reachy-mini.local:8000`).
- `numpy` — PCM16 frame manipulation
- `asyncio` — executor off-loads the sync SDK calls so the voice event loop isn't blocked; the wake loop itself is already event-driven (shipped in #15)
- pytest markers already configured: `hardware` (opt-in, no CI — needs LAN/USB-connected robot), `integration` (opt-in), `sim` (local-only, needs `reachy-mini-daemon --sim`)

**Issues closed:**

| Milestone | Issue | Subject |
|-----------|-------|---------|
| M1 | #23 | Wire `ReachyMicSource` / `ReachySpeakerSink` hardware-backed implementations |
| M2 | #20 | Bind `MuteGate` to state-machine MUTED transition + mic pump |

**Conventions used throughout:**
- **TDD per task**: failing test → confirm fail with the expected shape → minimal implementation → confirm pass → commit. Do not batch tests at the end.
- **Branch per milestone**: `hardware-audio-io` for M1, `mute-coordination` for M2. Both branch off `main`; M2 rebases on M1-merged main before starting.
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
  Coverage floor **90%** (matches the CI gate).
- **Marker discipline** (per `.claude/rules/testing-standards.md`):
  - Class-level SDK introspection (e.g. `hasattr(ReachyMini, "play_move")`) runs in the **default unit tier** — `reachy-mini` installs on every dev machine since PR #65, so the main CI workflow (`ci.yml`) catches upstream method renames without a dedicated hardware job.
  - `@pytest.mark.hardware`: requires a Reachy Mini reachable on the LAN (Wireless) or USB (Lite). **Not in CI.** Run with `uv run pytest -m hardware`. Tests that cannot reach a robot (e.g. `ReachyMini()` constructor raises `ConnectionRefusedError`) should fail with a clear error — they're explicitly opt-in via the marker filter, so a missing robot manifests as an honest failure for the developer who asked for them. Tests that describe real behaviour never use `@pytest.mark.skip` / `skipif`.
  - Unit tests (no marker) stay hardware-free: use `FakeMini` / `FakeMedia` stand-ins.
- **Side-effect verification** (per `testing-standards.md`): every action-shaped mock (`push_audio_sample`, `get_audio_sample`, `go_to_sleep`, `set_muted`) must be asserted-called. A `MagicMock` without a matching `assert_called*` is a placeholder, not a test.

**Reference skills:** `@superpowers:test-driven-development`, `@superpowers:verification-before-completion`

**Prereqs:**
- Physical Reachy Mini Wireless reachable on the LAN (user owns one).
- `gh` authenticated with `repo` scope for PR creation.
- `uv sync --all-packages --group dev` runs cleanly on your dev machine (macOS arm64, Linux, or Windows). No `--extra robot`; no Linux-host requirement; no SSH into the device for the introspection / contract tasks.
- Current branch `main` at commit `34d4c36` or later (PR #65 merged — this plan's install-story prereq). Confirm with `git log --oneline main | head -5`.
- **#64 (`play_move` signature drift) should be fixed before M1's hardware smoke actually touches motion.** Our state-machine mapping uses `str` move names but the SDK expects `Move` objects; unit tests don't exercise it today, but hardware tests will. Tracked separately; fix as its own PR.

**Out of scope (deferred, with rationale):**
- **Real ONNX wake detection.** `MockWakeDetector` ships today (`wake.py:95-105` comment: *"Task 8.2+ swaps in an ONNX-backed real implementation"*). Manual testing with `MockWakeDetector(trigger_on_feed=True)` **does not exercise a real "hey ducky" path** — it fires on every audio frame, which starves the loop (see `wake.py:65-68`). A separate issue should be filed for real wake once hardware audio is flowing and mock-driven testing proves insufficient. *Not in this plan because it is not an open issue today.*
- v0.1.0 release plumbing (#28, #29, #30, #24, #26, #47) — ship *after* hardware testing works.
- **Mac-daemon auto-discovery from the on-robot app (#58)** — today `DAEMON_URL` is set manually in `~/.reachy-ducky/.env`. Fine for the primary developer on their own LAN; a real friction point for community distribution. Explicitly out of scope here so the hardware-testing path doesn't blur with the DX-hardening work.
- **#64 — `ReachyMotionDriver.play_move` passes `str` but SDK expects `Move` object.** Pre-existing latent bug surfaced by the canonical-dep-migration audit. Unit tests mock the driver so it's invisible today; any hardware test that actually exercises a non-MUTED transition will hit it. **Fix as a separate PR before this plan's M1 hardware smoke runs against a live robot.**
- #60 (upgrade `reachy-mini` past `>=1.6.4` once migration stabilizes), #61 (remove `[tool.uv] dependency-metadata` gstreamer-msvc patch when upstream fixes its platform marker), #62 (`REACHY_MINI_HOST` env var for hardware tests — drop hardcoded `reachy-mini.local`) — migration-follow-up housekeeping; do not block this plan.
- #48 Dependabot runtime alerts — parallel investigation; does not block testing.
- Plan-reviewer polish sweep (#2, #3, #4, #5, #8) — shipped via PR #57; follow-ups tracked in #56.
- #6 (AppConfig error ergonomics), #19 (BrainRequest.include_tools wire-dead), #22 (project-slug selector) — quality-of-life; not test-blocking.
- Sim-audio integration (closed `cleanup/sim-tests` branch history + #40) — Pollen's sim stack doesn't expose audio on GitHub-hosted ubuntu-latest today; revisit when upstream lands sim audio.

**Session split:**
- **Session A** — M1 (SDK investigation + hardware smoke). The SDK audio signature is not yet pinned in our codebase; Task 1.1 resolves that via a local Mac client against the LAN robot and may drive minor adjustments to later tasks.
- **Session B** — M2 (depends on M1 merged to `main`).

Do **not** try to ship both in one sitting; each milestone warrants its own PR + review cycle.

---

## Milestone 1 — Hardware audio I/O (`#23`)

**Branch:** `hardware-audio-io` off fresh `main`.

**Cumulative commit count:** 5 commits on the branch (one per task).

### Task 1.1: Pin the Reachy Mini audio SDK surface via a contract test

**Files:**
- Create: `app/tests/test_sdk_audio_contract.py`

**Why this task runs first:** The exact SDK method names, signatures, and return shapes drive Tasks 1.2 and 1.3. We cannot write the failing tests for `ReachyMicSource` / `ReachySpeakerSink` without knowing what the SDK actually returns. A contract test doubles as regression protection in the main CI workflow (`ci.yml`) — class-level introspection runs in the default tier now that `reachy-mini` is a plain base dep; instance-level tests gate on `@pytest.mark.hardware`.

**Step 1: Introspect the installed SDK locally**

Runs on the Mac against the LAN robot — **not SSH, not Linux-only**. `reachy-mini` is a plain base dep since PR #65, so `ReachyMini()` constructs cross-platform; a `ReachyMini()` with default auto-detection will connect to `reachy-mini.local:8000` on the LAN where our primary robot lives.

```bash
uv run python <<'EOF'
import inspect
from reachy_mini import ReachyMini

# Connects over the LAN to reachy-mini.local:8000 by default (auto-
# detect connection_mode). Our primary robot is reachable today.
mini = ReachyMini()
media_attrs = sorted(a for a in dir(mini.media) if not a.startswith("_"))
print("media attrs:", media_attrs)

for name in ("get_audio_sample", "push_audio_sample"):
    if hasattr(mini.media, name):
        member = getattr(mini.media, name)
        print(f"{name} signature:", inspect.signature(member))
        print(f"{name} doc:", (member.__doc__ or "").splitlines()[:3])
    else:
        print(f"!! {name} MISSING")
EOF
```

**Expected shape** (from design doc §11; verify against output):
- `mini.media.get_audio_sample()` — returns a chunk of mic PCM. Either `bytes` or `np.ndarray[int16]`.
- `mini.media.push_audio_sample(pcm)` — pushes a frame to the speaker. Accepts `bytes` or the SDK-native buffer type.

Record the actual signatures + dtypes in the PR description. If either method does not exist or has a different name, **stop and escalate** per `quality-escalation.md` — that is a design decision (use a different method / adapt format), not a silent pivot.

**Step 2: Write the failing contract tests**

Two buckets:

1. **Class-level introspection** (no daemon, no media init): runs in the default unit tier — these pass on any machine where `reachy-mini` is installed, which is every dev machine and CI since PR #65.
2. **Instance-level live-robot tests**: gated on `@pytest.mark.hardware` — require a reachable Reachy Mini.

```python
"""SDK contract — pin the ReachyMini audio surface used by #23 impls.

Class-level introspection runs in the default unit tier (``reachy-mini``
installs on every machine since PR #65). Instance-level live-robot
checks are gated on ``@pytest.mark.hardware`` and run locally via
``uv run pytest -m hardware`` against a LAN-reachable Reachy Mini —
they catch upstream audio-API renames / signature changes before they
hit real hardware.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from reachy_mini import ReachyMini


def test_reachy_mini_media_exposes_audio_sample_methods() -> None:
    """ReachyMini.media has the two audio methods our impls call.

    Class-level only — does not construct a ``ReachyMini`` instance, so
    it runs in the default tier without needing a live robot.
    """
    assert hasattr(ReachyMini, "media") or "media" in getattr(ReachyMini, "__annotations__", {}), (
        "ReachyMini.media attribute missing — can't source or sink frames"
    )
    # Deeper introspection of .media (get_audio_sample / push_audio_sample
    # attribute existence) requires an instance; those checks live in the
    # @pytest.mark.hardware tests below.


@pytest.mark.hardware
def test_reachy_mini_media_audio_methods_present_on_instance() -> None:
    """On a live robot, ``mini.media`` exposes both audio methods."""
    mini = ReachyMini()
    assert hasattr(mini.media, "get_audio_sample"), (
        "ReachyMini.media.get_audio_sample missing — ReachyMicSource "
        "can't source frames"
    )
    assert hasattr(mini.media, "push_audio_sample"), (
        "ReachyMini.media.push_audio_sample missing — ReachySpeakerSink "
        "can't sink frames"
    )


@pytest.mark.hardware
def test_get_audio_sample_returns_non_empty_buffer() -> None:
    """get_audio_sample returns a non-empty PCM buffer.

    Exact dtype/shape asserted in the refinement pass after Step 1
    introspection. Today we pin only ``truthy``/``non-empty``.
    """
    mini = ReachyMini()
    sample = mini.media.get_audio_sample()
    assert sample is not None, "get_audio_sample returned None"
    # bytes → len > 0; np.ndarray → size > 0
    length = len(sample) if hasattr(sample, "__len__") else sample.size
    assert length > 0, f"get_audio_sample returned empty buffer ({sample!r})"


@pytest.mark.hardware
def test_push_audio_sample_accepts_silent_frame() -> None:
    """push_audio_sample accepts a well-formed silent PCM frame."""
    mini = ReachyMini()
    # 40ms @ 24 kHz mono PCM16 = 960 samples = 1920 bytes
    silent = np.zeros(960, dtype=np.int16).tobytes()
    mini.media.push_audio_sample(silent)  # must not raise


@pytest.mark.hardware
def test_get_audio_sample_signature_is_parameterless() -> None:
    """Signature is () → buffer; we rely on calling without arguments."""
    sig = inspect.signature(ReachyMini().media.get_audio_sample)
    assert len(sig.parameters) == 0, (
        f"get_audio_sample unexpectedly accepts {list(sig.parameters)}; "
        "ReachyMicSource calls it with no arguments"
    )
```

**Step 3: Run the failing tests locally against the LAN robot**

```bash
uv run pytest app/tests/test_sdk_audio_contract.py -v                 # class-level (default tier)
uv run pytest -m hardware app/tests/test_sdk_audio_contract.py -v     # instance-level (live robot)
```

Expected on any dev machine (no live robot): the first command passes the class-level test; the second exits with "no tests ran" unless the marker filter matches. Expected on a machine with a reachable Reachy Mini: one of four outcomes for the hardware-marked tests:
1. All pass — pin the contract as-is, move on.
2. `hasattr` fails — SDK method renamed. **Escalate** (see Step 1).
3. Buffer assertion fails — SDK returns `None` or empty. Check if the mic needs initialization first; update the test to call whatever initialization is required.
4. Signature assertion fails — method takes args we don't know about. Update the test to match reality; ensure Task 1.2's `ReachyMicSource.frames()` supplies them.

**Step 4: Refine based on Step 3 output**

Once the test actually runs against the SDK, tighten the dtype/size assertions to the exact shape observed. Example refinement if `get_audio_sample` returns `np.ndarray[int16]` with 480 samples (this is a `@pytest.mark.hardware` test — add the marker at module or per-function scope):

```python
@pytest.mark.hardware
def test_get_audio_sample_returns_int16_ndarray_at_24khz() -> None:
    mini = ReachyMini()
    sample = mini.media.get_audio_sample()
    assert isinstance(sample, np.ndarray), f"expected ndarray, got {type(sample)}"
    assert sample.dtype == np.int16, f"expected int16, got {sample.dtype}"
    assert sample.size > 0
    # Optional: pin the exact frame size if it's stable (e.g. 480 = 20ms @ 24kHz)
```

**Step 5: Commit**

```bash
git add app/tests/test_sdk_audio_contract.py
git commit -m "$(cat <<'EOF'
test: pin ReachyMini audio surface used by #23 impls

Class-level introspection runs in the default CI tier; instance-level
live-robot checks gate on @pytest.mark.hardware. Catches upstream
audio-API renames / signature changes before they hit real hardware.
Complements the existing class-surface contract tests pinning the
motion/lifecycle surface.

Refinement of dtype/size asserts based on Step 1 introspection results
tracked in the PR description.
EOF
)"
```

---

### Task 1.2: Implement `ReachyMicSource`

**Files:**
- Modify: `app/src/reachy_ducky_app/voice/audio_io.py`
- Modify: `app/tests/voice/test_audio_io.py` (create if it does not yet exist — verify with `ls app/tests/voice/`)

**Step 1: Write the failing unit test**

Uses a `FakeMini` that scripts the mic output so the test runs hardware-free.

```python
from __future__ import annotations

import pytest

from reachy_ducky_app.voice.audio_io import (
    MicSource,
    ReachyMicSource,
)


@pytest.mark.asyncio
async def test_reachy_mic_source_yields_frames_from_get_audio_sample() -> None:
    """ReachyMicSource pulls frames from mini.media.get_audio_sample in a loop."""
    scripted = [b"\x01" * 1920, b"\x02" * 1920, b"\x03" * 1920]
    calls: list[str] = []

    class FakeMedia:
        _i = 0

        def get_audio_sample(self) -> bytes:
            calls.append("get")
            if FakeMedia._i >= len(scripted):
                return b""  # sentinel: end-of-stream
            frame = scripted[FakeMedia._i]
            FakeMedia._i += 1
            return frame

    class FakeMini:
        media = FakeMedia()

    src = ReachyMicSource(FakeMini())
    collected: list[bytes] = []
    async for frame in src.frames():
        collected.append(frame)

    assert collected == scripted
    assert len(calls) == len(scripted) + 1  # +1 for the empty-buffer terminator


@pytest.mark.asyncio
async def test_reachy_mic_source_is_a_mic_source() -> None:
    """Structural: ReachyMicSource satisfies the MicSource contract."""
    class FakeMedia:
        def get_audio_sample(self) -> bytes:
            return b""

    class FakeMini:
        media = FakeMedia()

    src = ReachyMicSource(FakeMini())
    assert isinstance(src, MicSource)
```

**Step 2: Run the failing tests**

```bash
uv run pytest app/tests/voice/test_audio_io.py -v -k reachy_mic_source
```

Expected: `ImportError` / `AttributeError` — `ReachyMicSource` does not exist in `audio_io.py` yet.

**Step 3: Implement `ReachyMicSource`**

Append to `app/src/reachy_ducky_app/voice/audio_io.py` (after the existing `MockSpeakerSink`, before the factories):

```python
import asyncio


class ReachyMicSource(MicSource):
    """Hardware-backed mic source: pulls PCM frames from the ReachyMini SDK.

    Wraps ``ReachyMini.media.get_audio_sample()`` in an async generator.
    The SDK call is synchronous and runs on an executor so the voice
    event loop isn't blocked. Format contract (PCM16 mono 24 kHz) is
    pinned by :mod:`test_sdk_audio_contract` (class-level checks in the
    default tier; instance-level checks under ``@pytest.mark.hardware``);
    this class does NOT resample — if a future SDK version returns a
    different rate, the contract test fails first.

    **Hardware-only.** Constructors take a duck-typed ``reachy_mini``
    whose ``.media`` exposes ``get_audio_sample()``; the factory
    :func:`load_default_mic_source` selects this impl over
    :class:`MockMicSource` when a non-None ``reachy_mini`` is passed.
    """

    def __init__(self, reachy_mini: object) -> None:
        self._mini = reachy_mini

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield PCM frames until the SDK returns an empty buffer."""
        loop = asyncio.get_running_loop()
        get_sample = self._mini.media.get_audio_sample  # type: ignore[attr-defined]
        while True:
            frame = await loop.run_in_executor(None, get_sample)
            if frame is None or len(frame) == 0:
                return
            yield frame
```

**Step 4: Run the tests**

```bash
uv run pytest app/tests/voice/test_audio_io.py -v -k reachy_mic_source
uv run mypy --strict app/src app/tests
uv run ruff check app/src app/tests
```

Expected: 2 passed, mypy clean, ruff clean. The `# type: ignore[attr-defined]` is justified because `reachy_mini: object` is duck-typed — we cannot name the SDK class without importing it, and the SDK is hardware-only. Document this in the docstring (already done above).

**Step 5: Commit**

```bash
git add app/src/reachy_ducky_app/voice/audio_io.py app/tests/voice/test_audio_io.py
git commit -m "$(cat <<'EOF'
feat(app/voice): ReachyMicSource wraps mini.media.get_audio_sample

Hardware-backed MicSource for #23. Synchronous SDK call is dispatched to
an executor so the voice loop stays unblocked. Terminates on empty-
buffer sentinel. Format contract (PCM16 mono 24 kHz) pinned by
test_sdk_audio_contract.

Refs #23.
EOF
)"
```

---

### Task 1.3: Implement `ReachySpeakerSink`

**Files:**
- Modify: `app/src/reachy_ducky_app/voice/audio_io.py`
- Modify: `app/tests/voice/test_audio_io.py`

**Step 1: Write the failing unit test**

```python
@pytest.mark.asyncio
async def test_reachy_speaker_sink_forwards_to_push_audio_sample() -> None:
    """ReachySpeakerSink.play forwards PCM frames to mini.media.push_audio_sample."""
    pushed: list[bytes] = []

    class FakeMedia:
        def push_audio_sample(self, pcm: bytes) -> None:
            pushed.append(pcm)

    class FakeMini:
        media = FakeMedia()

    from reachy_ducky_app.voice.audio_io import ReachySpeakerSink, SpeakerSink

    sink = ReachySpeakerSink(FakeMini())
    assert isinstance(sink, SpeakerSink)
    await sink.play(b"frame-1")
    await sink.play(b"frame-2")
    assert pushed == [b"frame-1", b"frame-2"]
```

**Step 2: Run the failing test**

```bash
uv run pytest app/tests/voice/test_audio_io.py -v -k reachy_speaker_sink
```

Expected: `ImportError`.

**Step 3: Implement `ReachySpeakerSink`**

Append to `audio_io.py` (next to `ReachyMicSource`):

```python
class ReachySpeakerSink(SpeakerSink):
    """Hardware-backed speaker sink: pushes PCM frames to the ReachyMini SDK.

    Wraps ``ReachyMini.media.push_audio_sample()``. Symmetric with
    :class:`ReachyMicSource`: synchronous SDK call is dispatched to an
    executor, format contract is PCM16 mono 24 kHz (pinned by
    :mod:`test_sdk_audio_contract`).
    """

    def __init__(self, reachy_mini: object) -> None:
        self._mini = reachy_mini

    async def play(self, pcm: bytes) -> None:
        loop = asyncio.get_running_loop()
        push = self._mini.media.push_audio_sample  # type: ignore[attr-defined]
        await loop.run_in_executor(None, push, pcm)
```

**Step 4: Run the tests**

```bash
uv run pytest app/tests/voice/test_audio_io.py -v -k reachy_speaker_sink
uv run mypy --strict app/src app/tests
```

Expected: 1 passed, mypy clean.

**Step 5: Commit**

```bash
git add app/src/reachy_ducky_app/voice/audio_io.py app/tests/voice/test_audio_io.py
git commit -m "$(cat <<'EOF'
feat(app/voice): ReachySpeakerSink wraps mini.media.push_audio_sample

Hardware-backed SpeakerSink for #23. Symmetric with ReachyMicSource —
synchronous SDK call on an executor; PCM16 mono 24 kHz format contract.

Refs #23.
EOF
)"
```

---

### Task 1.4: Factory selection — return hardware impls when `reachy_mini` is passed

**Files:**
- Modify: `app/src/reachy_ducky_app/voice/audio_io.py` (factories)
- Modify: `app/src/reachy_ducky_app/main.py` (thread `reachy_mini` into the factories)
- Modify: `app/tests/voice/test_audio_io.py` (factory selection tests)
- Modify: `app/tests/test_main.py` (update any construction-site tests)

**Step 1: Write the failing tests**

```python
from reachy_ducky_app.voice.audio_io import (
    MockMicSource,
    MockSpeakerSink,
    ReachyMicSource,
    ReachySpeakerSink,
    load_default_mic_source,
    load_default_speaker_sink,
)


def test_load_default_mic_source_returns_mock_when_reachy_mini_is_none() -> None:
    """No reachy_mini → MockMicSource (dev-machine / unit-test path)."""
    src = load_default_mic_source(reachy_mini=None)
    assert isinstance(src, MockMicSource)


def test_load_default_mic_source_returns_hardware_impl_when_reachy_mini_given() -> None:
    """A ReachyMini-like object → ReachyMicSource."""
    class FakeMini:
        class media:  # noqa: N801 — duck-typed, mirrors SDK shape
            @staticmethod
            def get_audio_sample() -> bytes:
                return b""

    src = load_default_mic_source(reachy_mini=FakeMini())
    assert isinstance(src, ReachyMicSource)


def test_load_default_speaker_sink_returns_mock_when_reachy_mini_is_none() -> None:
    sink = load_default_speaker_sink(reachy_mini=None)
    assert isinstance(sink, MockSpeakerSink)


def test_load_default_speaker_sink_returns_hardware_impl_when_reachy_mini_given() -> None:
    class FakeMini:
        class media:  # noqa: N801
            @staticmethod
            def push_audio_sample(pcm: bytes) -> None:
                pass

    sink = load_default_speaker_sink(reachy_mini=FakeMini())
    assert isinstance(sink, ReachySpeakerSink)


def test_load_default_factories_default_reachy_mini_to_none() -> None:
    """Back-compat: factories callable with no args (returns mocks)."""
    assert isinstance(load_default_mic_source(), MockMicSource)
    assert isinstance(load_default_speaker_sink(), MockSpeakerSink)
```

**Step 2: Run — expect TypeError / signature mismatch**

```bash
uv run pytest app/tests/voice/test_audio_io.py -v -k load_default
```

Expected: `TypeError` — factories do not accept the `reachy_mini` kwarg.

**Step 3: Update the factories**

Replace the two factory functions in `audio_io.py`:

```python
def load_default_mic_source(reachy_mini: object | None = None) -> MicSource:
    """Return :class:`ReachyMicSource` when ``reachy_mini`` is given, else mock.

    The on-robot Pollen daemon hands :meth:`ReachyDuckyApp.run` a live
    ``ReachyMini`` instance; :meth:`ReachyDuckyApp._run_async` threads it
    through this factory so production is hardware by default. Dev
    machines and unit tests pass ``None`` (the default) and get the
    silent :class:`MockMicSource`.
    """
    if reachy_mini is None:
        return MockMicSource()
    return ReachyMicSource(reachy_mini)


def load_default_speaker_sink(reachy_mini: object | None = None) -> SpeakerSink:
    """Return :class:`ReachySpeakerSink` when ``reachy_mini`` is given, else mock."""
    if reachy_mini is None:
        return MockSpeakerSink()
    return ReachySpeakerSink(reachy_mini)
```

**Step 4: Thread `reachy_mini` through `main.py`**

Edit `app/src/reachy_ducky_app/main.py`. The change is to the two factory calls inside `_run_async`:

```python
# Before:
voice = OpenAIRealtimeVoice(
    mic=load_default_mic_source(),
    speaker=load_default_speaker_sink(),
)

# After:
voice = OpenAIRealtimeVoice(
    mic=load_default_mic_source(reachy_mini=reachy_mini),
    speaker=load_default_speaker_sink(reachy_mini=reachy_mini),
)
```

No other call sites touch these factories today — confirm with:

```bash
uv run grep -rn "load_default_mic_source\|load_default_speaker_sink" app/
```

Should return only: the two definitions in `audio_io.py`, the two call sites in `main.py`, and the tests.

**Step 5: Run the tests + type-check**

```bash
uv run pytest app/tests -q
uv run mypy --strict app/src app/tests
uv run ruff check app/src app/tests
```

Expected: all pass, including the five new factory tests. If `test_main.py` asserted on the exact class of `voice.mic` / `voice.speaker`, update those tests to match the new selection behaviour (pass `reachy_mini=None` in the test path so they still get mocks).

**Step 6: Commit**

```bash
git add app/src/reachy_ducky_app/voice/audio_io.py app/src/reachy_ducky_app/main.py \
        app/tests/voice/test_audio_io.py app/tests/test_main.py
git commit -m "$(cat <<'EOF'
feat(app/voice): factories return hardware impls when reachy_mini given

load_default_mic_source and load_default_speaker_sink accept an optional
reachy_mini kwarg; truthy → ReachyMicSource / ReachySpeakerSink, None →
mocks. main._run_async threads the on-robot daemon's ReachyMini handle
through so production is hardware by default and dev/unit-test paths
stay mock by default.

Refs #23.
EOF
)"
```

---

### Task 1.5: Hardware smoke test

**Files:**
- Create: `app/tests/voice/test_audio_io_hardware.py`

**Step 1: Write the gated smoke**

```python
"""Hardware smoke for the #23 audio-I/O plumbing.

Gated on ``@pytest.mark.hardware`` — runs only with a real Reachy Mini
reachable on the LAN (or USB for Lite). Invoke locally with
``uv run pytest -m hardware``.

Verifies end-to-end that PCM frames flow through the real SDK and come
out the speaker without the process crashing. Does NOT assert audio
*content* — that is a human-in-the-loop judgement (the developer
listens for silence / the expected tone).
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from reachy_mini import ReachyMini

pytestmark = pytest.mark.hardware


@pytest.mark.asyncio
async def test_reachy_speaker_sink_plays_silent_frame_without_error() -> None:
    from reachy_ducky_app.voice.audio_io import ReachySpeakerSink

    mini = ReachyMini()
    sink = ReachySpeakerSink(mini)
    silent = np.zeros(960, dtype=np.int16).tobytes()  # 40ms @ 24 kHz mono
    await sink.play(silent)
    # Human-in-the-loop: listener confirms audible silence (no static /
    # garbage). If this call raises, the SDK rejected the frame shape;
    # re-check the test_sdk_audio_contract pins.


@pytest.mark.asyncio
async def test_reachy_mic_source_yields_at_least_one_frame() -> None:
    from reachy_ducky_app.voice.audio_io import ReachyMicSource

    mini = ReachyMini()
    src = ReachyMicSource(mini)

    async def pull_one() -> bytes | None:
        async for frame in src.frames():
            return frame
        return None

    frame = await asyncio.wait_for(pull_one(), timeout=5.0)
    assert frame is not None, "ReachyMicSource produced no frames in 5s"
    assert len(frame) > 0
```

**Step 2: Run on hardware**

```bash
uv run pytest -m hardware app/tests/voice/test_audio_io_hardware.py -v
```

Expected on a connected Reachy Mini: 2 passed. On a dev machine with no reachable robot: the `ReachyMini()` constructor will raise (e.g. `ConnectionRefusedError`) and the tests will fail with a clear error — that's the honest-failure contract per `testing-standards.md`. These tests are explicitly opt-in via the marker filter, so `-m hardware` is the only path that invokes them.

**Step 3: Commit**

```bash
git add app/tests/voice/test_audio_io_hardware.py
git commit -m "$(cat <<'EOF'
test(app/voice): hardware smoke for ReachyMic/SpeakerSink

@pytest.mark.hardware. Plays a silent frame, pulls one mic frame — does
not assert audio content (human-in-the-loop check). Closes the #23
acceptance criteria; the end-to-end listen-and-reply walkthrough runs
after M2 (mute coordination) lands.

Closes #23.
EOF
)"
```

**Step 4: Open PR for M1**

```bash
git push -u origin hardware-audio-io
gh pr create --title "feat(app): hardware audio I/O (closes #23)" --body "$(cat <<'EOF'
## Summary
- Adds `ReachyMicSource` / `ReachySpeakerSink` wrapping `ReachyMini.media.get_audio_sample` / `push_audio_sample`.
- Factories select hardware impls when `reachy_mini` is passed; mocks otherwise.
- SDK contract test pins the audio surface: class-level checks in the default CI tier; instance-level checks under `@pytest.mark.hardware`.
- Hardware smoke test (`@pytest.mark.hardware`) validates end-to-end on a real robot.

## Test plan
- [ ] `uv run pytest -q` — unit suite, all green, coverage ≥ 90% (includes the class-level contract checks).
- [ ] `uv run pytest -m hardware` — hardware smoke + instance-level contract tests pass on the Reachy Mini (physical confirmation: silent frame audible silence; mic frame non-empty).
- [ ] Closes #23 when merged.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Milestone 2 — Mute coordination (`#20`)

**Branch:** `mute-coordination` off `main` (after M1 merged).

**Depends on:** M1 merged. `ReachyMicSource` must exist before we can wire `MuteGate` into it.

**Cumulative commit count:** 3 commits on the branch.

### Task 2.1: Accept optional `MuteGate` in `EmbodimentStateMachine`

**Files:**
- Modify: `app/src/reachy_ducky_app/embodiment/state_machine.py`
- Modify: `app/tests/embodiment/test_state_machine.py`

**Step 1: Write the failing test**

```python
from reachy_ducky_app.mute import MuteGate


def test_state_machine_sets_mute_gate_on_muted_transition(fake_driver) -> None:
    """Transitioning to MUTED calls mute_gate.set_muted(True)."""
    gate = MuteGate()
    sm = EmbodimentStateMachine(driver=fake_driver, mute_gate=gate)
    assert gate.muted is False

    sm.transition(State.MUTED)
    assert gate.muted is True
    # And the body-sleep side effect still fires:
    fake_driver.go_to_sleep.assert_called_once()


def test_state_machine_clears_mute_gate_on_leaving_muted(fake_driver) -> None:
    """Transitioning OUT of MUTED calls mute_gate.set_muted(False)."""
    gate = MuteGate()
    gate.set_muted(True)
    sm = EmbodimentStateMachine(driver=fake_driver, mute_gate=gate)
    sm.transition(State.MUTED)  # start from MUTED
    fake_driver.go_to_sleep.reset_mock()
    fake_driver.wake_up.reset_mock()

    sm.transition(State.IDLE)
    assert gate.muted is False
    # And wake-up fires before the IDLE play_move:
    fake_driver.wake_up.assert_called_once()


def test_state_machine_mute_gate_is_optional(fake_driver) -> None:
    """Passing no mute_gate preserves today's behaviour (no mute side effect)."""
    sm = EmbodimentStateMachine(driver=fake_driver)
    sm.transition(State.MUTED)
    # No MuteGate to observe; transition still calls go_to_sleep.
    fake_driver.go_to_sleep.assert_called_once()
```

The `fake_driver` fixture already exists in the test file (`MotionDriver` mock); confirm with:

```bash
uv run grep -n "fake_driver\|MotionDriver" app/tests/embodiment/test_state_machine.py
```

**Step 2: Run — expect FAIL**

```bash
uv run pytest app/tests/embodiment/test_state_machine.py -v -k mute_gate
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'mute_gate'`.

**Step 3: Implement**

Edit `app/src/reachy_ducky_app/embodiment/state_machine.py`:

```python
"""Embodiment state machine: maps :class:`State` transitions to motion."""

from __future__ import annotations

from reachy_ducky_protocol.messages import State

from ..mute import MuteGate
from .motion_driver import MotionDriver

_STATE_TO_MOVE: dict[State, str] = {
    State.IDLE: "neutral",
    State.LISTENING: "listening",
    State.THINKING: "thinking",
}


class EmbodimentStateMachine:
    """Maps :class:`State` transitions to motion + optional mute-gate side effects.

    Invariants:

    - A transition to the SAME state is a no-op.
    - ``MUTED`` calls :meth:`MotionDriver.go_to_sleep` **and** (if a
      ``mute_gate`` was supplied) ``mute_gate.set_muted(True)`` — a
      single seam that moves both the body and the mic into the muted
      posture simultaneously, so the user cannot observe a window where
      the body is asleep but audio still flows.
    - Exiting ``MUTED`` calls :meth:`MotionDriver.wake_up` and
      ``mute_gate.set_muted(False)`` BEFORE the target-state
      ``play_move``.
    - ``mute_gate`` is optional: ``None`` preserves the bare state-
      machine behaviour used by embodiment unit tests that don't care
      about audio gating.
    """

    def __init__(
        self,
        driver: MotionDriver,
        *,
        mute_gate: MuteGate | None = None,
    ) -> None:
        self._driver = driver
        self._mute_gate = mute_gate
        self._state: State = State.IDLE

    @property
    def state(self) -> State:
        return self._state

    def transition(self, target: State) -> None:
        if target == self._state:
            return
        if target == State.MUTED:
            self._driver.go_to_sleep()
            if self._mute_gate is not None:
                self._mute_gate.set_muted(True)
        else:
            if self._state == State.MUTED:
                self._driver.wake_up()
                if self._mute_gate is not None:
                    self._mute_gate.set_muted(False)
            move = _STATE_TO_MOVE.get(target)
            if move is not None:
                self._driver.play_move(move)
        self._state = target
```

**Step 4: Run tests + gates**

```bash
uv run pytest app/tests/embodiment -v
uv run mypy --strict app/src app/tests
uv run ruff check app/src app/tests
```

Expected: all pass (including the 3 new tests + the existing state-machine suite).

**Step 5: Commit**

```bash
git add app/src/reachy_ducky_app/embodiment/state_machine.py \
        app/tests/embodiment/test_state_machine.py
git commit -m "$(cat <<'EOF'
feat(embodiment): state machine optionally binds a MuteGate

Entering MUTED calls mute_gate.set_muted(True) alongside go_to_sleep;
exiting MUTED clears the gate before wake_up + the target-state
play_move. mute_gate is keyword-only and defaults to None so existing
embodiment unit tests continue working unchanged.

Refs #20.
EOF
)"
```

---

### Task 2.2: Wire a shared `MuteGate` through `main._run_async` + into `ReachyMicSource`

**Files:**
- Modify: `app/src/reachy_ducky_app/voice/audio_io.py` — `ReachyMicSource` consults a `MuteGate`
- Modify: `app/src/reachy_ducky_app/main.py` — construct one `MuteGate`, thread it into the state machine AND the mic factory
- Modify: `app/tests/voice/test_audio_io.py`
- Modify: `app/tests/test_main.py`

**Step 1: Write the failing unit test for `ReachyMicSource` + `MuteGate`**

```python
import numpy as np

from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import ReachyMicSource


@pytest.mark.asyncio
async def test_reachy_mic_source_zeroes_frames_when_gate_is_muted() -> None:
    """A muted gate causes frames to be zeroed before being yielded."""
    nonzero = np.full(960, 1234, dtype=np.int16).tobytes()

    class FakeMedia:
        _i = 0

        def get_audio_sample(self) -> bytes:
            FakeMedia._i += 1
            if FakeMedia._i > 2:
                return b""
            return nonzero

    class FakeMini:
        media = FakeMedia()

    gate = MuteGate()
    gate.set_muted(True)
    src = ReachyMicSource(FakeMini(), mute_gate=gate)

    collected = [frame async for frame in src.frames()]
    assert len(collected) == 2
    expected_silent = np.zeros(960, dtype=np.int16).tobytes()
    for frame in collected:
        assert frame == expected_silent


@pytest.mark.asyncio
async def test_reachy_mic_source_passes_frames_when_gate_not_muted() -> None:
    """An un-muted gate is transparent — frames flow unchanged."""
    payload = b"\x42" * 1920

    class FakeMedia:
        _i = 0

        def get_audio_sample(self) -> bytes:
            FakeMedia._i += 1
            if FakeMedia._i > 1:
                return b""
            return payload

    class FakeMini:
        media = FakeMedia()

    gate = MuteGate()  # defaults to unmuted
    src = ReachyMicSource(FakeMini(), mute_gate=gate)
    collected = [frame async for frame in src.frames()]
    assert collected == [payload]


@pytest.mark.asyncio
async def test_reachy_mic_source_gate_is_optional() -> None:
    """No gate passed → frames flow unchanged (back-compat with Task 1.2)."""
    payload = b"\x07" * 1920

    class FakeMedia:
        _i = 0

        def get_audio_sample(self) -> bytes:
            FakeMedia._i += 1
            if FakeMedia._i > 1:
                return b""
            return payload

    class FakeMini:
        media = FakeMedia()

    src = ReachyMicSource(FakeMini())  # no gate
    collected = [frame async for frame in src.frames()]
    assert collected == [payload]
```

**Step 2: Run — expect FAIL**

```bash
uv run pytest app/tests/voice/test_audio_io.py -v -k mute
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'mute_gate'`.

**Step 3: Update `ReachyMicSource`**

Edit `app/src/reachy_ducky_app/voice/audio_io.py`:

```python
import numpy as np

from ..mute import MuteGate


class ReachyMicSource(MicSource):
    # ... existing docstring ...

    def __init__(
        self,
        reachy_mini: object,
        *,
        mute_gate: MuteGate | None = None,
    ) -> None:
        self._mini = reachy_mini
        self._mute_gate = mute_gate

    async def frames(self) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        get_sample = self._mini.media.get_audio_sample  # type: ignore[attr-defined]
        while True:
            frame = await loop.run_in_executor(None, get_sample)
            if frame is None or len(frame) == 0:
                return
            if self._mute_gate is not None and self._mute_gate.muted:
                # Zero the PCM bytes in-shape. Using numpy keeps the
                # conversion honest if a future SDK version returns a
                # non-bytes buffer; .tobytes() round-trips either way.
                zeroed = np.zeros_like(
                    np.frombuffer(frame, dtype=np.int16)
                ).tobytes()
                yield zeroed
            else:
                yield frame
```

Update the `load_default_mic_source` factory to accept + forward the gate:

```python
def load_default_mic_source(
    reachy_mini: object | None = None,
    *,
    mute_gate: MuteGate | None = None,
) -> MicSource:
    """Return hardware source when ``reachy_mini`` given, else mock.

    ``mute_gate`` is threaded into :class:`ReachyMicSource` so a MUTED
    state transition on the embodiment state machine zeros the mic path
    at its source. ``None`` (the default) keeps today's dev/unit-test
    behaviour: the mock mic ignores muting because its frames are
    already under test control.
    """
    if reachy_mini is None:
        return MockMicSource()
    return ReachyMicSource(reachy_mini, mute_gate=mute_gate)
```

**Step 4: Write the failing `main.py` integration test**

```python
@pytest.mark.asyncio
async def test_run_async_shares_one_mute_gate_between_sm_and_mic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main._run_async constructs one MuteGate and hands it to BOTH seams.

    Proves the two consumers reference the same object: toggling the
    gate via the state machine must be observable from the mic side.
    """
    # Capture the MuteGate instance(s) handed to each seam.
    captured_sm_gate: list[object] = []
    captured_mic_gate: list[object] = []

    real_sm_cls = EmbodimentStateMachine
    real_factory = load_default_mic_source

    def recording_sm(*, driver, mute_gate, **kw):
        captured_sm_gate.append(mute_gate)
        return real_sm_cls(driver=driver, mute_gate=mute_gate, **kw)

    def recording_factory(reachy_mini=None, *, mute_gate=None):
        captured_mic_gate.append(mute_gate)
        return real_factory(reachy_mini=reachy_mini, mute_gate=mute_gate)

    monkeypatch.setattr(
        "reachy_ducky_app.main.EmbodimentStateMachine", recording_sm
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source", recording_factory
    )

    app = ReachyDuckyApp()
    stop = threading.Event()
    stop.set()  # immediate exit — we only care about construction.
    await app._run_async(reachy_mini=None, stop_event=stop)

    assert len(captured_sm_gate) == 1
    assert len(captured_mic_gate) == 1
    assert captured_sm_gate[0] is captured_mic_gate[0], (
        "sm and mic source must share the same MuteGate instance"
    )
    assert isinstance(captured_sm_gate[0], MuteGate)
```

**Step 5: Run — expect FAIL**

```bash
uv run pytest app/tests/test_main.py -v -k shares_one_mute_gate
```

Expected: the recording shim receives `mute_gate=None` because `main.py` doesn't yet construct one.

**Step 6: Implement the `main.py` wiring**

Edit `app/src/reachy_ducky_app/main.py`. Add the import, construct one `MuteGate`, thread it through:

```python
# Add to imports:
from .mute import MuteGate
```

Inside `_run_async`, adjust the construction block:

```python
driver = ReachyMotionDriver(reachy_mini)
mute_gate = MuteGate()
sm = EmbodimentStateMachine(driver=driver, mute_gate=mute_gate)
voice = OpenAIRealtimeVoice(
    mic=load_default_mic_source(reachy_mini=reachy_mini, mute_gate=mute_gate),
    speaker=load_default_speaker_sink(reachy_mini=reachy_mini),
)
```

**Step 7: Run all tests + gates**

```bash
uv run pytest app/tests -q
uv run mypy --strict app/src app/tests
uv run ruff check app/src app/tests
uv run pytest -q --cov
```

Expected: all pass; coverage ≥ 90%.

**Step 8: Commit**

```bash
git add app/src/reachy_ducky_app/voice/audio_io.py app/src/reachy_ducky_app/main.py \
        app/tests/voice/test_audio_io.py app/tests/test_main.py
git commit -m "$(cat <<'EOF'
feat(app): share one MuteGate across state machine + mic pump

main._run_async constructs a single MuteGate and threads it into both
EmbodimentStateMachine (for the MUTED transition) and ReachyMicSource
(for per-frame zeroing). A muted robot now has a zeroed mic path in
addition to a sleeping body — previously the gate was instantiated only
in its own unit test and never reached real audio.

Closes #20.
EOF
)"
```

---

### Task 2.3: Hardware end-to-end mute smoke

**Files:**
- Create: `app/tests/test_main_hardware.py`

**Step 1: Write the gated test**

```python
"""Hardware smoke for the #20 mute-coordination seam.

Gated on ``@pytest.mark.hardware``. Runs locally on the Mac against a
LAN-reachable Reachy Mini. Constructs the real app graph (without
running the conversation loop) and verifies that transitioning the
state machine to MUTED zeros the live mic source *and* calls
go_to_sleep on the body. Complements the unit-level coverage in
test_audio_io.py and test_state_machine.py.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from reachy_mini import ReachyMini

from reachy_ducky_protocol.messages import State

pytestmark = pytest.mark.hardware


@pytest.mark.asyncio
async def test_muted_transition_zeros_live_mic_and_sleeps_body() -> None:
    from reachy_ducky_app.embodiment.motion_driver import ReachyMotionDriver
    from reachy_ducky_app.embodiment.state_machine import EmbodimentStateMachine
    from reachy_ducky_app.mute import MuteGate
    from reachy_ducky_app.voice.audio_io import load_default_mic_source

    mini = ReachyMini()
    driver = ReachyMotionDriver(mini)
    gate = MuteGate()
    sm = EmbodimentStateMachine(driver=driver, mute_gate=gate)
    src = load_default_mic_source(reachy_mini=mini, mute_gate=gate)

    # Enter MUTED — gate should flip, body should sleep.
    sm.transition(State.MUTED)
    assert gate.muted is True

    # Pull one frame from the live mic; verify it is zeroed.
    async def pull_one() -> bytes | None:
        async for frame in src.frames():
            return frame
        return None

    frame = await asyncio.wait_for(pull_one(), timeout=5.0)
    assert frame is not None
    zeroed = np.zeros(len(frame) // 2, dtype=np.int16).tobytes()
    assert frame == zeroed, "MUTED mic source returned non-zero bytes"

    # Exit MUTED — gate clears, body wakes.
    sm.transition(State.IDLE)
    assert gate.muted is False
```

**Step 2: Run on hardware**

```bash
uv run pytest -m hardware app/tests/test_main_hardware.py -v
```

Expected: 1 passed on a connected Reachy Mini. Human confirms visually that the body goes to sleep and wakes up during the test. **Note:** this test exercises `ReachyMotionDriver.play_move` via the MUTED→IDLE transition — #64 (`play_move` signature drift) must be fixed first or the `IDLE` leg will fail.

**Step 3: Commit + push + open PR**

```bash
git add app/tests/test_main_hardware.py
git commit -m "$(cat <<'EOF'
test(hardware): end-to-end mute zeros live mic and sleeps body

@pytest.mark.hardware. Verifies the #20 seam on real hardware — MUTED
transition zeros the mic source's live output AND calls go_to_sleep on
the body. Closes the hardware-verification arm of the #20 acceptance
criteria.
EOF
)"

git push -u origin mute-coordination
gh pr create --title "feat(app): mute coordination across sm + mic (closes #20)" --body "$(cat <<'EOF'
## Summary
- `EmbodimentStateMachine` accepts an optional `MuteGate` and toggles it on MUTED / non-MUTED transitions alongside the existing body sleep/wake side effects.
- `ReachyMicSource` consults an optional `MuteGate` per frame — when muted, yields zeroed PCM in-shape.
- `main._run_async` constructs one `MuteGate` and threads it into both seams so the two stay coherent.
- Hardware smoke verifies MUTED actually zeroes the live mic on a connected Reachy Mini.

## Test plan
- [ ] `uv run pytest -q` — unit suite, all green, coverage ≥ 90%.
- [ ] `uv run pytest -m hardware` — hardware smoke passes (human confirms body sleep / wake transitions).
- [ ] Manual end-to-end: speak into the mic → transcript → reply audible. Then mute → robot sleeps and transcripts stop. Unmute → robot wakes and transcripts resume.
- [ ] Closes #20 when merged.

Depends on #23 (hardware audio I/O) — already merged. Also requires #64 (`play_move` signature drift) resolved before the IDLE leg of the MUTED→IDLE transition touches real hardware.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Done / exit criteria

When both milestone PRs are merged:

1. **Unit + CI gates green locally and in CI:**
   ```bash
   uv run pytest -q --cov                       # ≥ 90% coverage
   uv run mypy --strict daemon/src app/src menubar/src protocol/src daemon/tests app/tests menubar/tests protocol/tests
   uv run pyright                               # CLI run, not IDE diagnostics
   uv run ruff check . && uv run ruff format --check .
   uv run bandit -ll -r daemon/src app/src menubar/src protocol/src
   ```

2. **Hardware smokes pass on the robot:**
   ```bash
   uv run pytest -m hardware -v
   ```
   Expected: the Task 1.1 instance-level contract tests + 2 from #23 (speaker silent frame, mic one frame) + 1 from #20 (MUTED zeros live mic + sleeps body) all pass. Requires #64 (`play_move` signature drift) already fixed so the MUTED→IDLE leg doesn't fail at the motion call.

3. **Default CI tier stays green** on every PR (`ci.yml`) — including the class-level SDK contract checks added in Task 1.1.

4. **Issue audit:** #20, #23 both closed by their respective PRs' merge commits.

5. **End-to-end hardware walkthrough** (manual, once):
   - `reachy-mini-daemon` running on the robot.
   - `uv run reachy-ducky` (the Mac daemon) running on the dev laptop.
   - App installed on the Reachy Mini, started from the dashboard.
   - Trigger a wake in whichever way testing requires today (e.g. the test harness fires `wake.event.set()`, or — once real wake lands — actually say "hey ducky").
   - Speak a short utterance. Observe:
     - Robot enters LISTENING (visible motion).
     - Robot enters THINKING.
     - Audible reply plays through the robot speaker.
   - Press mute on the menu-bar app. Observe:
     - Robot goes to sleep (visible posture).
     - Speaking into the mic produces no transcript (mic path is zeroed).
   - Unmute. Observe:
     - Robot wakes up.
     - Next utterance produces a reply.

When the walkthrough passes, **Reachy Ducky is ready for ongoing testing**. Any further issues found (real wake detection, project-slug selector, config ergonomics, multi-project UX) are tracked by separate issues — not by this plan.

---

## Risks & escalation points

- **Task 1.1 — unexpected SDK shape.** If `get_audio_sample` returns `np.ndarray` instead of `bytes`, `ReachyMicSource.frames()` needs `frame.tobytes()` before yielding. This is a small adapter, not a design change — but it changes the Task 1.2 test payloads. Escalate only if the method name / call signature differs.
- **Task 1.2 / 1.3 — sync vs async SDK methods.** The plan assumes synchronous methods dispatched via `run_in_executor`. If either method is already `async`, drop the executor and `await` directly. Not a scope change.
- **Task 2.2 — PCM format drift.** `MuteGate.process` operates on `np.int16` arrays; the mic path operates on `bytes`. The in-task conversion (`np.frombuffer → zeros_like → tobytes`) keeps `MuteGate`'s typed contract intact. If the SDK returns non-PCM16 bytes, the Task 1.1 contract tests (instance-level hardware checks) will catch it and this plan's mute zeroing becomes nonsensical — escalate.
- **Task 2.3 — hardware access needed.** Without a physical Reachy Mini, 2.3 cannot run. The unit-level coverage (tests added in 2.1 + 2.2) is strong enough to merge `mute-coordination` with 2.3 marked as a follow-up verification step. Do not skip 2.3 permanently — it is the only test that proves the wiring works on real audio hardware.
