# ONNX Wake Detector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or superpowers:subagent-driven-development for same-session execution) to implement this plan task-by-task.

**Goal:** Replace `MockWakeDetector`-only with a real openWakeWord-backed detector and Pattern-C lifecycle handoff in `main._run_async`, closing #55.

**Architecture:** Pattern C (lifecycle handoff) per the merged design at `docs/plans/2026-04-25-onnx-wake-detector-design.md` (PR #74). Single mic reader at a time; switches between wake pump (Phase 1) and voice (Phase 2). `WakeDetector` ABC reshaped to `feed(frame: AudioFrame)` + `reset()`. `OpenWakeWordDetector` does sync ONNX inference inside `feed()`, no threads. Vendored ~10 MB of weights at `app/assets/wake/`. Wake-fire transitions to `State.LISTENING` for human-in-the-loop "I heard you" affordance.

**Tech Stack:**
- Python 3.12, `uv` workspace
- `openwakeword` (Apache 2.0) — wake model + ONNX runtime wrapper
- `onnxruntime` — ONNX inference engine (transitive via `openwakeword`, but pinned for Reachy Mini ARM compatibility)
- `scipy.signal.resample` — 24 kHz → 16 kHz (already a workspace dep)
- pytest markers already configured: `hardware` (opt-in, no CI), `integration` (opt-in)

**Issues closed:** #55.

**Conventions used throughout:**
- **TDD per task**: failing test → confirm fail with the expected shape → minimal implementation → confirm pass → commit. Do not batch tests at the end.
- **Branch:** `wake-onnx` off `main` at `f66b6b2` or later. M1 lands as one PR.
- **Per-task gate before commit:**
  ```bash
  uv run ruff check . && uv run ruff format --check . \
    && uv run mypy --strict app/src app/tests \
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
  Coverage floor **90%** (matches the CI gate).
- **Side-effect verification** (per `.claude/rules/testing-standards.md`): every action-shaped mock (`fake_model.predict`, `wake.event.set`, `sm.transition`) must be asserted-called.
- **Marker discipline:**
  - Unit tests (no marker) use a `FakeOWWModel` injected into `OpenWakeWordDetector` — no real ONNX weights required in the unit tier.
  - `@pytest.mark.hardware` requires a Reachy Mini Wireless reachable on the LAN AND a human in the loop to actually say the wake word.

**Reference skills:** `@superpowers:test-driven-development`, `@superpowers:verification-before-completion`

**Prereqs:**
- `gh` authenticated with `repo` scope.
- `uv sync --all-packages --group dev` runs cleanly.
- Current branch `main` at `f66b6b2` (PR #74 merged) or later.
- For Task 6 (hardware smoke): a reachable Reachy Mini Wireless on `reachy-mini.local`.

**Out of scope (already filed as separate issues — do NOT include in this plan):**
- Custom `hey ducky` model training — #75.
- Menubar status string for wake-listening / in-conversation — #76.

---

## Milestone 1 — ONNX wake detector + Pattern C handoff

**Branch:** `wake-onnx` off fresh `main`.

### Task 1: Add openwakeword + onnxruntime deps

**Files:**
- Modify: `app/pyproject.toml`

**Step 1: Add the deps**

Open `app/pyproject.toml` and add to the `[project] dependencies` array (preserve alphabetic order):

```toml
"onnxruntime>=1.18.0",
"openwakeword>=0.6.0",
```

**Step 2: Sync**

Run: `uv sync --all-packages --group dev`
Expected: resolves cleanly, both packages installed. If `openwakeword` pulls a `tflite-runtime` extra that fails on macOS arm64, edit the dep to `"openwakeword[onnx]>=0.6.0"` or whatever extra name the package uses to opt out of the tflite path — check `openwakeword`'s pyproject and pick the ONNX-only install.

**Step 3: Verify import**

Run: `uv run python -c "from openwakeword.model import Model; print('ok')"`
Expected: `ok`

**Step 4: Commit**

```bash
git add app/pyproject.toml uv.lock
git commit -m "build(app): add openwakeword + onnxruntime deps for #55"
```

---

### Task 2: Vendor the openWakeWord weights

**Files:**
- Create: `app/src/reachy_ducky_app/assets/__init__.py` (empty marker for `importlib.resources`)
- Create: `app/src/reachy_ducky_app/assets/wake/__init__.py` (empty marker)
- Create: `app/src/reachy_ducky_app/assets/wake/hey_jarvis.onnx` (binary)
- Create: `app/src/reachy_ducky_app/assets/wake/melspectrogram.onnx` (binary)
- Create: `app/src/reachy_ducky_app/assets/wake/embedding_model.onnx` (binary)
- Create: `app/src/reachy_ducky_app/assets/wake/LICENSE` (Apache 2.0 text from openWakeWord upstream)
- Create: `app/src/reachy_ducky_app/assets/wake/README.md` (provenance)
- Modify: `app/pyproject.toml` (`[tool.hatch.build.targets.wheel]` to include the assets in the package)

**Step 1: Locate the weights**

openWakeWord ships pretrained models. After `uv sync` from Task 1, find them:

```bash
uv run python -c "import openwakeword, pathlib; \
  p = pathlib.Path(openwakeword.__file__).parent / 'resources' / 'models'; \
  print(p); print('\n'.join(sorted(x.name for x in p.iterdir())))"
```

Expected: a directory listing including `hey_jarvis_v0.1.onnx` (or the latest variant), `melspectrogram.onnx`, `embedding_model.onnx`. The exact filenames may differ across `openwakeword` versions — note them.

**Step 2: Copy them in**

```bash
SRC=$(uv run python -c "import openwakeword, pathlib; \
  print(pathlib.Path(openwakeword.__file__).parent / 'resources' / 'models')")
mkdir -p app/src/reachy_ducky_app/assets/wake
cp "$SRC/hey_jarvis_v0.1.onnx" app/src/reachy_ducky_app/assets/wake/hey_jarvis.onnx
cp "$SRC/melspectrogram.onnx" app/src/reachy_ducky_app/assets/wake/melspectrogram.onnx
cp "$SRC/embedding_model.onnx" app/src/reachy_ducky_app/assets/wake/embedding_model.onnx
touch app/src/reachy_ducky_app/assets/__init__.py
touch app/src/reachy_ducky_app/assets/wake/__init__.py
```

**Step 3: Add LICENSE + README**

`app/src/reachy_ducky_app/assets/wake/LICENSE` — paste the Apache 2.0 license text from openWakeWord's upstream LICENSE (https://github.com/dscripka/openWakeWord/blob/main/LICENSE).

`app/src/reachy_ducky_app/assets/wake/README.md`:

```markdown
# Vendored wake-word weights

These ONNX models are vendored from openWakeWord
(https://github.com/dscripka/openWakeWord) for reproducible offline
loading on the Reachy Mini.

## Files

- `hey_jarvis.onnx` — wake-word model. Stand-in keyword until a custom
  "hey ducky" model is trained (tracked in #75).
- `melspectrogram.onnx` — preprocessor; converts raw audio to mel
  spectrograms.
- `embedding_model.onnx` — shared embeddings used by all openWakeWord
  wake models.

## License

Apache 2.0. See `LICENSE` (vendored from upstream). All three files are
unmodified copies of openWakeWord's `resources/models/` artefacts.

## Provenance

Copied from `openwakeword>=0.6.0` installed via `uv sync` on
2026-04-26. Re-run the copy step in Task 2 of
`docs/plans/2026-04-26-onnx-wake-detector-plan.md` to refresh.
```

**Step 4: Wire packaging**

Open `app/pyproject.toml`. If there's a `[tool.hatch.build.targets.wheel]` (or equivalent) section, add `"src/reachy_ducky_app/assets/**/*.onnx"` and `"src/reachy_ducky_app/assets/**/LICENSE"` and `"src/reachy_ducky_app/assets/**/README.md"` to the `include` list. If hatchling auto-includes everything under `src/`, no change needed — but verify by building a wheel:

```bash
uv build --package reachy-ducky-app
unzip -l dist/reachy_ducky_app-*.whl | grep -E "wake/.*\.onnx"
```

Expected: all three `.onnx` files appear in the wheel listing.

**Step 5: Commit**

```bash
git add app/src/reachy_ducky_app/assets app/pyproject.toml
git commit -m "build(app): vendor openWakeWord weights for #55"
```

---

### Task 3: Reshape the WakeDetector ABC and update MockWakeDetector

**Files:**
- Modify: `app/src/reachy_ducky_app/wake.py`
- Modify: `app/tests/test_wake.py`

**Step 1: Write the failing test for the new ABC shape**

Open `app/tests/test_wake.py`. Add at the end:

```python
def test_wake_detector_abc_requires_feed_and_reset() -> None:
    """The new ABC contract is feed(AudioFrame) + reset() — drops the bool return."""
    abstract_methods = WakeDetector.__abstractmethods__
    assert "feed" in abstract_methods
    assert "reset" in abstract_methods


def test_mock_feed_with_trigger_on_feed_sets_event_and_buffers_nothing() -> None:
    detector = MockWakeDetector(trigger_on_feed=True)
    silent_frame: AudioFrame = (24_000, np.zeros(960, dtype=np.int16))
    detector.feed(silent_frame)
    assert detector.event.is_set()


def test_mock_reset_clears_event() -> None:
    detector = MockWakeDetector(trigger_on_feed=True)
    detector.feed((24_000, np.zeros(960, dtype=np.int16)))
    assert detector.event.is_set()
    detector.reset()
    assert not detector.event.is_set()
```

Add the `AudioFrame` import:

```python
from reachy_ducky_app.voice.audio_io import AudioFrame
```

**Also delete the obsolete `test_load_default_returns_mock_for_now` test** at `app/tests/test_wake.py:75-78`. That test pins the Phase A "factory always returns mock" contract, which #55 explicitly inverts (real detector by default; mock only via env override). Without this deletion, Step 4's per-task gate fails on `ModuleNotFoundError: ... wake_onnx` because the factory's new default path imports a module that Task 4 hasn't created yet. The new env-override behaviour is pinned by `test_load_default_returns_mock_with_env_override`, which Task 4 adds.

**Step 2: Run — confirm fail**

Run: `uv run pytest app/tests/test_wake.py -v`
Expected: 3 failures with `AttributeError: ... has no attribute 'feed'` (and `reset`). The obsolete factory test was deleted in Step 1, so it does not appear in this run.

**Step 3: Reshape the ABC**

Replace `app/src/reachy_ducky_app/wake.py` with:

```python
"""Wake-word detection: abstract contract + deterministic mock + factory."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from reachy_ducky_app.voice.audio_io import AudioFrame


class WakeDetector(ABC):
    """Consumes ``AudioFrame`` tuples; signals detection via :attr:`event`.

    Real implementations wrap an ONNX model. :class:`MockWakeDetector` here
    is pure Python and deterministic for tests.

    The detector exposes :attr:`event` — an ``asyncio.Event`` that
    :meth:`feed` sets when the wake word is detected. The wake pump in
    ``main._run_async`` awaits this event and cancels its own pull loop
    on detection.

    Constructing a ``WakeDetector`` outside an event loop is safe:
    Python 3.10+ ``asyncio.Event()`` is loop-agnostic until ``wait()``
    is called.
    """

    def __init__(self) -> None:
        self.event: asyncio.Event = asyncio.Event()

    @abstractmethod
    def feed(self, frame: AudioFrame) -> None:
        """Consume one ``AudioFrame`` and update internal state.

        Implementations MUST set ``self.event`` when the wake word is
        detected. Synchronous: must return promptly so the wake pump's
        async loop stays responsive. ONNX inference (~5 ms per 80 ms
        chunk on Pi-class CPUs) is well inside the audio frame budget.
        """

    @abstractmethod
    def reset(self) -> None:
        """Drop internal buffers and clear the event.

        Called between turns by ``main._run_async`` so the first frame
        of the next listening phase can't replay a stale hit from the
        prior phase's accumulator.
        """


class MockWakeDetector(WakeDetector):
    """Test double.

    ``feed`` is a no-op by default. Pass ``trigger_on_feed=True`` to set
    :attr:`event` on every ``feed`` call — useful for exercising the
    event-driven loop without ONNX or hardware.

    **Do NOT use ``trigger_on_feed=True`` in production wiring.** Pattern
    C's wake pump cancels itself on detection, but a continuously-firing
    detector inside the pump iteration would still re-set the event
    after the pump cancels. It's strictly a test hook.

    ``detect_in_text`` is a substring check so tests can simulate
    "heard the phrase".
    """

    def __init__(
        self,
        trigger_on: str = "hey ducky",
        *,
        trigger_on_feed: bool = False,
    ) -> None:
        super().__init__()
        self._trigger = trigger_on.lower()
        self._trigger_on_feed = trigger_on_feed

    def feed(self, frame: AudioFrame) -> None:
        del frame
        if self._trigger_on_feed:
            self.event.set()

    def reset(self) -> None:
        self.event.clear()

    def detect_in_text(self, text: str) -> bool:
        return self._trigger in text.lower()


def load_default_wake_detector() -> WakeDetector:
    """Factory for the production wake detector.

    Returns a real :class:`OpenWakeWordDetector` by default; returns
    :class:`MockWakeDetector` when ``REACHY_DUCKY_WAKE_MOCK=1`` is set
    in the environment (test/dev escape hatch only).

    Raises:
        RuntimeError: if the vendored weights are missing and no mock
            override is set.
    """
    import os

    if os.environ.get("REACHY_DUCKY_WAKE_MOCK") == "1":
        return MockWakeDetector()
    # OpenWakeWordDetector ships in Task 4; this branch exists so Task 3
    # can be committed independently with a passing test suite. Until
    # Task 4 lands the import will fail loudly.
    from reachy_ducky_app.wake_onnx import OpenWakeWordDetector  # noqa: PLC0415

    return OpenWakeWordDetector.from_vendored_weights()
```

Note: the lazy import of `OpenWakeWordDetector` keeps Task 3's commit independent — Task 4 will create the `wake_onnx` module. Until then, calling `load_default_wake_detector()` without `REACHY_DUCKY_WAKE_MOCK=1` will raise `ModuleNotFoundError`. This is intentional: any unit test that exercises the factory without the mock env override fails loudly at the missing module, not silently.

**Step 4: Run — confirm pass**

Run: `uv run pytest app/tests/test_wake.py -v`
Expected: all tests pass. Run the full suite to catch any caller of the old `feed_audio` signature: `uv run pytest -q`. Any caller that passed an int16 ndarray to `feed_audio` needs to be updated to call `feed((rate, arr))`. Most likely affected: `app/tests/test_main.py` if it stubs the wake detector. Run with `-x` to find the first break and fix in this same task; this is mechanical refactor, not a new behavior.

**Step 5: Commit**

```bash
git add app/src/reachy_ducky_app/wake.py app/tests/test_wake.py
# also any caller updates from --x sweep:
# git add app/tests/test_main.py
git commit -m "refactor(app/wake): reshape WakeDetector ABC to feed(AudioFrame) + reset()"
```

---

### Task 4: Implement OpenWakeWordDetector

**Files:**
- Create: `app/src/reachy_ducky_app/wake_onnx.py`
- Modify: `app/tests/test_wake.py`

**Step 1: Write the failing tests**

Append to `app/tests/test_wake.py`:

```python
class FakeOWWModel:
    """Stand-in for openwakeword.model.Model in unit tests."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores
        self.predict_calls = 0
        self.last_chunk: np.ndarray | None = None
        self.reset_calls = 0

    def predict(self, chunk: np.ndarray) -> dict[str, float]:
        self.predict_calls += 1
        self.last_chunk = chunk
        return dict(self._scores)

    def reset(self) -> None:
        self.reset_calls += 1


def test_owd_buffers_until_1280_samples_then_predicts() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.9})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    # 24 kHz at 640 samples = ~26.7 ms — sub-window after resample to 16 kHz (~427 samples)
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    assert not det.event.is_set()
    assert fake.predict_calls == 0
    # Two more 640-sample chunks @ 24 kHz cross the 1280-sample @ 16 kHz threshold
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    assert det.event.is_set()
    assert fake.predict_calls >= 1


def test_owd_below_threshold_does_not_set_event() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.4})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    # Push enough audio for one full window at 16 kHz: 1280 samples * 24/16 = 1920 @ 24 kHz
    det.feed((24_000, np.zeros(1920, dtype=np.int16)))
    assert not det.event.is_set()
    assert fake.predict_calls == 1


def test_owd_predict_chunk_is_1280_samples_at_16k() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.0})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    det.feed((24_000, np.zeros(1920, dtype=np.int16)))
    assert fake.last_chunk is not None
    assert fake.last_chunk.shape == (1280,)
    assert fake.last_chunk.dtype == np.int16


def test_owd_resample_path_skipped_when_input_already_16k() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.0})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    det.feed((16_000, np.zeros(1280, dtype=np.int16)))
    assert fake.predict_calls == 1
    assert fake.last_chunk is not None
    assert fake.last_chunk.shape == (1280,)


def test_owd_reset_drops_buffer_and_calls_model_reset() -> None:
    fake = FakeOWWModel(scores={"hey_jarvis": 0.9})
    det = OpenWakeWordDetector(model=fake, threshold=0.5)
    det.feed((24_000, np.zeros(640, dtype=np.int16)))  # half-buffer
    det.event.set()  # simulate a prior fire
    det.reset()
    assert not det.event.is_set()
    assert fake.reset_calls == 1
    # After reset, a sub-window feed should not trigger immediate predict
    det.feed((24_000, np.zeros(640, dtype=np.int16)))
    # If reset failed to drop the buffer, the accumulated 1280 from prior
    # half + new half would have triggered a predict — assert it didn't
    assert fake.predict_calls == 0


def test_owd_factory_raises_when_weights_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("REACHY_DUCKY_WAKE_MOCK", raising=False)
    monkeypatch.setattr(
        "reachy_ducky_app.wake_onnx._VENDORED_MODEL_PATH",
        tmp_path / "nonexistent.onnx",
    )
    with pytest.raises(RuntimeError, match="Wake model not found"):
        OpenWakeWordDetector.from_vendored_weights()


def test_load_default_returns_mock_with_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REACHY_DUCKY_WAKE_MOCK", "1")
    detector = load_default_wake_detector()
    assert isinstance(detector, MockWakeDetector)
```

Add imports at the top of the file:

```python
from pathlib import Path
from reachy_ducky_app.wake_onnx import OpenWakeWordDetector
```

**Step 2: Run — confirm fail**

Run: `uv run pytest app/tests/test_wake.py -v`
Expected: 7 failures with `ModuleNotFoundError: ... wake_onnx`.

**Step 3: Implement**

Create `app/src/reachy_ducky_app/wake_onnx.py`:

```python
"""ONNX-backed wake-word detector. See docs/plans/2026-04-25-onnx-wake-detector-design.md."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import scipy.signal

from reachy_ducky_app.voice.audio_io import AudioFrame
from reachy_ducky_app.wake import WakeDetector

_OWW_SAMPLE_RATE = 16_000
_OWW_WINDOW_SAMPLES = 1280  # 80 ms @ 16 kHz — openWakeWord's expected chunk size

# Resolved at import time so monkeypatch-replacing this module attr in
# tests is straightforward.
_VENDORED_MODEL_PATH: Path = Path(
    str(files("reachy_ducky_app.assets.wake") / "hey_jarvis.onnx")
)


class OpenWakeWordDetector(WakeDetector):
    """ONNX-backed wake detector.

    Inference is synchronous — runs inside ``feed()`` on the event loop.
    openWakeWord per-frame inference is ~5 ms on Pi-class CPUs (well
    inside an 80ms audio frame budget); staying on the event loop
    sidesteps the ``call_soon_threadsafe`` thread-safety contract that
    plagued earlier WakeDetector designs.

    Audio rate adaptation: the workspace's ``AudioFrame`` is 24 kHz
    int16 mono (set by ``ReachyMicSource``), but openWakeWord expects
    16 kHz. We resample inside ``feed()`` before buffering into 1280-
    sample windows.
    """

    def __init__(
        self,
        *,
        model: Any,  # duck-typed — accepts openwakeword.Model OR FakeOWWModel
        threshold: float = 0.5,
    ) -> None:
        super().__init__()
        self._model = model
        self._threshold = threshold
        self._buffer: npt.NDArray[np.int16] = np.zeros(0, dtype=np.int16)

    @classmethod
    def from_vendored_weights(
        cls,
        *,
        threshold: float = 0.5,
        vad_threshold: float = 0.3,
    ) -> "OpenWakeWordDetector":
        """Construct using the vendored ONNX weights at install time."""
        if not _VENDORED_MODEL_PATH.is_file():
            msg = (
                f"Wake model not found at {_VENDORED_MODEL_PATH}. Run "
                "`uv sync` to install vendored weights, or set "
                "REACHY_DUCKY_WAKE_MOCK=1 for the mock detector (tests only)."
            )
            raise RuntimeError(msg)
        from openwakeword.model import Model  # noqa: PLC0415 — keep import lazy

        model = Model(
            wakeword_models=[str(_VENDORED_MODEL_PATH)],
            vad_threshold=vad_threshold,
            inference_framework="onnx",
        )
        return cls(model=model, threshold=threshold)

    def feed(self, frame: AudioFrame) -> None:
        sample_rate, samples = frame
        if sample_rate != _OWW_SAMPLE_RATE:
            new_len = int(len(samples) * _OWW_SAMPLE_RATE / sample_rate)
            resampled = cast(
                npt.NDArray[np.float32],
                scipy.signal.resample(samples, new_len),
            )
            samples = resampled.astype(np.int16)
        self._buffer = np.concatenate((self._buffer, samples))
        while len(self._buffer) >= _OWW_WINDOW_SAMPLES:
            chunk = self._buffer[:_OWW_WINDOW_SAMPLES]
            self._buffer = self._buffer[_OWW_WINDOW_SAMPLES:]
            scores = self._model.predict(chunk)
            if any(score >= self._threshold for score in scores.values()):
                self.event.set()
                return

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.int16)
        self.event.clear()
        if hasattr(self._model, "reset"):
            self._model.reset()
```

**Step 4: Run — confirm pass**

```bash
uv run pytest app/tests/test_wake.py -v
```
Expected: all tests pass.

**Step 5: Type-check + lint**

```bash
uv run ruff check app/src/reachy_ducky_app/wake_onnx.py app/tests/test_wake.py
uv run mypy --strict app/src/reachy_ducky_app/wake_onnx.py app/tests/test_wake.py
```
Expected: no errors.

**Step 6: Commit**

```bash
git add app/src/reachy_ducky_app/wake_onnx.py app/tests/test_wake.py
git commit -m "feat(app/wake): OpenWakeWordDetector — ONNX inference + audio adaptation"
```

---

### Task 5: Wire Pattern C lifecycle into main._run_async

**Files:**
- Modify: `app/src/reachy_ducky_app/main.py`
- Create: `app/tests/test_main_wake_pump.py`

**Step 1: Write the failing integration tests**

Create `app/tests/test_main_wake_pump.py`:

```python
"""Integration tests: Pattern C lifecycle handoff in main._run_async.

Exercises the wake-pump → turn → restart cycle with fakes only — no
hardware, no ONNX, no real OpenAI. Pins the invariant that exactly
one mic consumer is active at any moment, and that wake.feed is paused
during a turn.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest

from reachy_ducky_app.embodiment import EmbodimentStateMachine, MockMotionDriver
from reachy_ducky_app.main import ReachyDuckyApp
from reachy_ducky_app.mute import MuteGate
from reachy_ducky_app.voice.audio_io import AudioFrame
from reachy_ducky_app.wake import MockWakeDetector
from reachy_ducky_protocol.messages import State


class _CountingMicSource:
    """Async mic source that counts how many times ``frames()`` was entered."""

    def __init__(self) -> None:
        self.entry_count = 0
        self.frames_yielded = 0

    async def frames(self) -> AsyncIterator[AudioFrame]:
        self.entry_count += 1
        try:
            while True:
                self.frames_yielded += 1
                yield (24_000, np.zeros(960, dtype=np.int16))
                await asyncio.sleep(0)  # cooperative yield
        finally:
            pass


class _FireOnceWake(MockWakeDetector):
    """Fires on the first feed call, then stays silent until reset()."""

    def __init__(self) -> None:
        super().__init__()
        self.feed_calls = 0
        self.reset_calls = 0
        self._fired = False

    def feed(self, frame: AudioFrame) -> None:
        self.feed_calls += 1
        if not self._fired:
            self._fired = True
            self.event.set()

    def reset(self) -> None:
        super().reset()
        self.reset_calls += 1
        self._fired = False


@pytest.mark.asyncio
async def test_wake_pump_fires_then_yields_to_turn_then_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mic = _CountingMicSource()
    wake = _FireOnceWake()
    sm_transitions: list[State] = []

    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_mic_source",
        lambda **_: mic,
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_speaker_sink",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        "reachy_ducky_app.main.load_default_wake_detector",
        lambda: wake,
    )

    fake_voice_calls: list[str] = []

    class _FakeVoice:
        def __init__(self, **_: Any) -> None: ...

    monkeypatch.setattr(
        "reachy_ducky_app.main.OpenAIRealtimeVoice", _FakeVoice
    )

    fake_daemon = type(
        "FakeDaemon",
        (),
        {"from_env": classmethod(lambda cls: cls()), "aclose": staticmethod(lambda: asyncio.sleep(0))},
    )()
    monkeypatch.setattr("reachy_ducky_app.main.DaemonClient", type(fake_daemon))

    # Snapshot wake.feed_calls at the START and END of each fake turn.
    # The invariant is that these are EQUAL — wake.feed must not run
    # during run_one_turn (Phase 2 == voice owns the mic exclusively).
    turn_feed_call_pairs: list[tuple[int, int]] = []

    async def _fake_run_one_turn(**_: Any) -> None:
        start_calls = wake.feed_calls
        # Yield once so the wake pump task (if it were buggily still
        # alive) would have a chance to run. Without this await, a stale
        # pump task wouldn't get scheduling time and the assertion below
        # would pass even on a real bug.
        await asyncio.sleep(0)
        end_calls = wake.feed_calls
        turn_feed_call_pairs.append((start_calls, end_calls))
        fake_voice_calls.append("turn")

    monkeypatch.setattr("reachy_ducky_app.main.run_one_turn", _fake_run_one_turn)

    # Capture state machine transitions
    def _capture_transition(self: Any, target: State) -> None:
        sm_transitions.append(target)

    monkeypatch.setattr(EmbodimentStateMachine, "transition", _capture_transition)

    stop_event = threading.Event()
    app = ReachyDuckyApp()

    async def _stop_after_two_turns() -> None:
        # Wait until at least two turns ran, then signal stop
        deadline = asyncio.get_event_loop().time() + 2.0
        while len(fake_voice_calls) < 2 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.01)
        stop_event.set()

    asyncio.create_task(_stop_after_two_turns())
    await app._run_async(reachy_mini=object(), stop_event=stop_event)

    # Pin the lifecycle invariants
    assert len(fake_voice_calls) >= 2, "expected at least 2 turns"
    assert mic.entry_count >= 2, "mic.frames() should be re-entered each phase 1"
    assert wake.reset_calls >= 2, "wake.reset() called once per phase 1 entry"
    # Wake.feed must NOT be called during run_one_turn — start == end per turn.
    for start_calls, end_calls in turn_feed_call_pairs:
        assert start_calls == end_calls, (
            f"wake.feed ran during run_one_turn (start={start_calls}, end={end_calls}) "
            "— Pattern C invariant violated"
        )
    # State.LISTENING transition fires once per wake hit
    assert sm_transitions.count(State.LISTENING) >= 2
```

**Step 2: Run — confirm fail**

```bash
uv run pytest app/tests/test_main_wake_pump.py -v
```
Expected: failure — current `_run_async` doesn't call `wake.feed`, doesn't reset, doesn't transition to LISTENING.

**Step 3: Implement Pattern C in main.py**

Open `app/src/reachy_ducky_app/main.py`. Locate the existing `_run_async` body around line 95. Replace the `while not stop_event.is_set():` block with:

```python
        try:
            while not stop_event.is_set():
                # Phase 1 — wake-listening. Wake pump owns the mic.
                wake.reset()
                pump_task = asyncio.create_task(_run_wake_pump(voice.mic, wake))
                wake_waiter = asyncio.create_task(wake.event.wait())
                try:
                    await asyncio.wait(
                        {pump_task, stop_checker, wake_waiter},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not pump_task.done():
                        pump_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pump_task
                    if not wake_waiter.done():
                        wake_waiter.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await wake_waiter
                if stop_event.is_set():
                    break
                wake.event.clear()

                # Phase 2 — turn. Wake confirmed; transition the embodiment
                # state so the human gets the "I heard you" affordance, then
                # let voice take exclusive mic ownership for the turn.
                sm.transition(State.LISTENING)
                await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug=None)
```

Add to the imports at the top of `main.py`:

```python
import contextlib
from reachy_ducky_app.voice.audio_io import MicSource
from reachy_ducky_app.wake import WakeDetector
from reachy_ducky_protocol.messages import State
```

(If any of these are already imported, leave them alone.)

Add the wake-pump helper at module scope, near `_watch_stop`:

```python
async def _run_wake_pump(mic: MicSource, wake: WakeDetector) -> None:
    """Pull frames from the mic and feed the wake detector until cancelled.

    Cancellation is the normal exit path — caller cancels this task once
    ``wake.event`` fires (or stop is requested). The detector signals
    detection by setting its event; the caller observes via the event,
    not via this coroutine's return value.

    Pattern C invariant: this coroutine is the only mic consumer during
    Phase 1. ``run_one_turn`` consumes the mic during Phase 2, after this
    task has been cancelled and awaited.
    """
    async for frame in mic.frames():
        wake.feed(frame)
        if wake.event.is_set():
            return
```

The `voice.mic` reference assumes `OpenAIRealtimeVoice` exposes its mic source — if it doesn't today, hoist the mic out of voice construction:

```python
mic = load_default_mic_source(reachy_mini=reachy_mini, mute_gate=mute_gate)
voice = OpenAIRealtimeVoice(
    mic=mic,
    speaker=load_default_speaker_sink(reachy_mini=reachy_mini),
)
```

then call `_run_wake_pump(mic, wake)` instead of `_run_wake_pump(voice.mic, wake)`. Use whichever shape matches the actual class.

**Step 4: Run — confirm pass**

```bash
uv run pytest app/tests/test_main_wake_pump.py -v
uv run pytest -q
```
Expected: integration tests pass; full suite green.

**Step 5: Type-check + lint**

```bash
uv run ruff check app/src/reachy_ducky_app/main.py app/tests/test_main_wake_pump.py
uv run mypy --strict app/src/reachy_ducky_app/main.py app/tests/test_main_wake_pump.py
```
Expected: no errors.

**Step 6: Commit**

```bash
git add app/src/reachy_ducky_app/main.py app/tests/test_main_wake_pump.py
git commit -m "feat(app/main): Pattern C wake-pump → turn lifecycle handoff (#55)"
```

---

### Task 6: Hardware smoke test

**Files:**
- Create: `app/tests/test_wake_hardware.py`

**Step 1: Write the gated smoke**

```python
"""Hardware smoke for #55 — real openWakeWord detection on the Reachy Mini.

Gated on ``@pytest.mark.hardware``. Runs only with a Reachy Mini Wireless
reachable on the LAN AND a human in the loop ready to say the wake word.
Invoke with ``uv run pytest -m hardware -v``.
"""

from __future__ import annotations

import asyncio
import contextlib

import numpy as np
import pytest

reachy_mini = pytest.importorskip(
    "reachy_mini",
    reason="hardware tests require the reachy-mini SDK installed",
)


@pytest.mark.hardware
@pytest.mark.asyncio
async def test_open_wake_word_fires_on_real_utterance() -> None:
    """Human says 'hey jarvis' within 10s; wake.event fires exactly once.

    Human-in-the-loop. The test prints a clear instruction at the start;
    a human listener must say the wake phrase. If the test times out
    without a fire, either the human didn't speak, the mic is dead, or
    the model genuinely missed — log inspection separates these.
    """
    from reachy_ducky_app.voice.audio_io import ReachyMicSource
    from reachy_ducky_app.wake_onnx import OpenWakeWordDetector

    print("\n>>> SAY 'hey jarvis' WITHIN 10 SECONDS <<<\n", flush=True)

    with reachy_mini.ReachyMini() as mini:
        mic = ReachyMicSource(mini)
        det = OpenWakeWordDetector.from_vendored_weights()

        async def pump() -> None:
            async for frame in mic.frames():
                det.feed(frame)
                if det.event.is_set():
                    return

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(pump(), timeout=10.0)

        assert det.event.is_set(), (
            "wake.event did not fire after 10s — did you say 'hey jarvis'? "
            "Check mic frames are flowing (test_sdk_audio_contract hardware tier "
            "should be green) and that the model file is present."
        )


@pytest.mark.hardware
@pytest.mark.asyncio
async def test_open_wake_word_paused_during_turn_phase() -> None:
    """After detection + reset, no further fires until the next utterance.

    Pin: once detected and reset, the detector does NOT spuriously fire
    on ambient room audio for at least 5s. Human-in-the-loop: stay quiet.
    """
    from reachy_ducky_app.voice.audio_io import ReachyMicSource
    from reachy_ducky_app.wake_onnx import OpenWakeWordDetector

    print("\n>>> SAY 'hey jarvis' WITHIN 10 SECONDS, THEN STAY QUIET <<<\n", flush=True)

    with reachy_mini.ReachyMini() as mini:
        mic = ReachyMicSource(mini)
        det = OpenWakeWordDetector.from_vendored_weights()

        async def first_fire() -> None:
            async for frame in mic.frames():
                det.feed(frame)
                if det.event.is_set():
                    return

        await asyncio.wait_for(first_fire(), timeout=10.0)
        assert det.event.is_set()
        det.reset()

        # Stay quiet for 5s; assert no spurious fire from ambient audio
        feed_calls = 0

        async def quiet_window() -> None:
            nonlocal feed_calls
            async for frame in mic.frames():
                feed_calls += 1
                det.feed(frame)
                if det.event.is_set():
                    return  # spurious fire — caller asserts on event state

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(quiet_window(), timeout=5.0)

        assert feed_calls > 0, "mic produced no frames in the quiet window"
        assert not det.event.is_set(), (
            "wake.event fired spuriously during the quiet window — "
            "false-positive on ambient audio"
        )
```

**Step 2: Run on hardware**

```bash
uv run pytest -m hardware app/tests/test_wake_hardware.py -v
```
Expected on a connected Reachy Mini with a human in the loop: 2 passed. Without hardware: tests fail at `ReachyMini()` construction with a clear error.

**Step 3: Commit**

```bash
git add app/tests/test_wake_hardware.py
git commit -m "test(app/wake): hardware smoke — real wake-word detection (#55)

@pytest.mark.hardware. Human-in-the-loop: human says the wake phrase;
test asserts wake.event fires within 10s. Second test pins no false
positives during a 5s quiet window after reset.

Closes #55."
```

---

### Task 7: Full-branch gate + push + PR

**Files:** none (verification + admin).

**Step 1: Run the full-branch gate**

```bash
uv run ruff check . && uv run ruff format --check . \
  && uv run mypy --strict daemon/src app/src menubar/src protocol/src \
                          daemon/tests app/tests menubar/tests protocol/tests \
  && uv run pyright \
  && uv run bandit -ll -r daemon/src app/src menubar/src protocol/src \
  && uv run pytest -q --cov
```
Expected: all green; coverage ≥ 90 %.

**Step 2: Push + open PR**

```bash
git push -u origin wake-onnx
gh pr create --title "feat(app): ONNX-backed wake detector + Pattern C handoff (closes #55)" --body "$(cat <<'EOF'
## Summary

Implements the design from `docs/plans/2026-04-25-onnx-wake-detector-design.md` (PR #74).

- Reshapes `WakeDetector` ABC: `feed(AudioFrame) -> None` + `reset()`. Drops the bool return — caller observes detection via `event` only.
- New `OpenWakeWordDetector` (`wake_onnx.py`): ONNX inference inside `feed()`, audio resampling 24 kHz → 16 kHz, 80 ms window buffering. VAD threshold 0.3, detection threshold 0.5.
- Vendored ~10 MB of openWakeWord weights at `app/src/reachy_ducky_app/assets/wake/` (Apache 2.0). Stand-in keyword `hey jarvis`; custom "hey ducky" tracked in #75.
- Pattern C lifecycle handoff in `main._run_async`: wake pump owns the mic during Phase 1; voice owns it during Phase 2; never both.
- Wake-fire transitions to `State.LISTENING` for the human-in-the-loop "I heard you" affordance.
- New `REACHY_DUCKY_WAKE_MOCK=1` env override returns `MockWakeDetector` for unit tests / dev machines without the vendored model.

## Test plan

- [x] `uv run pytest -q` — unit tier green; coverage ≥ 90%
- [x] `uv run pytest -m hardware` — 2 new wake hardware tests + 3 existing audio-I/O hardware tests pass on a live Reachy Mini Wireless
- [x] Full-branch gate: ruff + mypy --strict + pyright + bandit all green
- [x] Closes #55

## Out of scope (filed)

- #75 — Train custom `hey ducky` model
- #76 — Menubar status string

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Step 3: Wait for CI + bot reviews; address comments**

CI: ubuntu, macos, windows must all be green. Augment / Codex / Claude reviewers post comments. Address each per the project's standard escalation pattern (`.claude/rules/quality-escalation.md`):
- Real bug → fix in a follow-up commit on the branch.
- Style/preference → reply with rationale; only change if the reviewer's argument is stronger than the current shape.
- Disagree on design → escalate to user (reference the merged design doc as the authoritative answer).

---

## Acceptance

- [ ] `WakeDetector` ABC reshaped; `MockWakeDetector` updated; existing callers updated.
- [ ] `OpenWakeWordDetector` ships in `wake_onnx.py` with vendored weights at `app/assets/wake/`.
- [ ] `main._run_async` runs Pattern C handoff; integration tests pin the lifecycle.
- [ ] Hardware tier (`uv run pytest -m hardware`) passes — 2 new wake tests + 3 existing audio tests.
- [ ] Coverage ≥ 90 %.
- [ ] CI green on all 3 platforms.
- [ ] PR merged; #55 closes; #75 + #76 stay open as deferred follow-ups.
