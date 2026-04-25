# ONNX-Backed Wake Detector — Design

**Issue:** [#55 — Ship ONNX-backed wake detector so 'hey ducky' works on real audio](https://github.com/Obsidian-Owl/reachy-ducky/issues/55)

**Status:** Design validated 2026-04-25. Implementation plan to follow.

**Goal:** Replace the `MockWakeDetector`-only wake path with a real, ONNX-backed detector so "say the wake word, get a response" works on the live Reachy Mini Wireless. This is the last blocker between today's hardware-tier passing tests and a real end-to-end conversational walkthrough.

---

## Architecture: Pattern C — lifecycle handoff

The Reachy Mini SDK's audio pipeline (`MediaManager.get_audio_sample` → `AudioBase` → GStreamer `appsink.try_pull_sample`) is **single-consumer destructive** (verified at `audio_base.py:47-61`, `gstreamer_utils.py:31`). Two concurrent readers race and each get half the frames. Pattern A (detector owns its own mic) is therefore not safe; Pattern B (fan-out tap) is overengineering nobody in the wake-word ecosystem ships.

Pattern C — the same shape Rhasspy `wyoming-satellite`, openWakeWord's canonical example, and Home Assistant Voice all use — has **one mic reader at a time**, with the consumer switching by phase:

```
Phase 1 (wake-listening): mic.frames() → wake_pump → wake.feed(frame)
                          on detection → wake.event.set() → exit pump

Phase 2 (turn):           mic.frames() → voice.send_audio(frame)
                          on turn end  → restart Phase 1
```

`main._run_async` orchestrates the handoff; the mic generator is closed cleanly between phases (via `pump_task.cancel()` in a `finally` block) before voice opens its own iteration. **No two-readers race; no fan-out broker; no wall-clock refractory** — Phase 2 itself is the refractory.

## Library: openWakeWord

Apache 2.0; pure ONNX runtime; ships pretrained models + a documented synthetic-data pipeline for training custom wake words. Picked over Picovoice Porcupine (proprietary blob, requires access key — bad fit for the open-distribution direction in #28) and Vosk (heavier, runs full ASR continuously). Stand-in keyword: **`hey jarvis`** (one of openWakeWord's pretrained models). A community-trainable "hey ducky" model is tracked as a follow-up issue, not blocking #55.

## Components

```
app/
  src/reachy_ducky_app/
    wake.py                       MODIFY — OpenWakeWordDetector, ABC reshape
    main.py                       MODIFY — split _run_async into wake-pump + turn phases
  assets/wake/                    NEW — vendored ONNX weights (~10 MB total)
    hey_jarvis.onnx
    melspectrogram.onnx
    embedding_model.onnx
    LICENSE                       Apache 2.0 — openWakeWord upstream
    README.md                     provenance + custom-training notes
  tests/
    test_wake.py                  MODIFY — keep mock tests, add fake-oWW tests
    test_main_wake_pump.py        NEW — lifecycle handoff (start, fire, handoff, restart)
    test_wake_hardware.py         NEW — @pytest.mark.hardware live wake smoke
  pyproject.toml                  MODIFY — add openwakeword + onnxruntime
```

## ABC change

```python
class WakeDetector(ABC):
    event: asyncio.Event
    @abstractmethod
    def feed(self, frame: AudioFrame) -> None: ...
    @abstractmethod
    def reset(self) -> None: ...
```

- `feed` accepts the existing `AudioFrame = tuple[int, NDArray[int16]]` from `voice/audio_io.py` so the wake pump can pull straight from `ReachyMicSource.frames()` with no shape conversion.
- Drops the `bool` return: caller observes detection via `event` only — single source of truth.
- `reset()` clears internal mel-spectrogram + accumulator buffers between turns so the first frame after handoff can't replay a stale hit.
- Synchronous: openWakeWord inference is ~5 ms per 80 ms chunk on Pi-class CPUs (well inside frame budget); staying on the event loop sidesteps the `call_soon_threadsafe` thread-safety contract that was the only reason `feed_audio` ever returned a bool.

## OpenWakeWordDetector

Vendored model loaded at construction; 80 ms windows (1280 samples @ 16 kHz) buffered from incoming frames with sample-rate adaptation if the source isn't 16 kHz. VAD threshold defaults to **0.3** (HA Voice Ch.7's tuning — drops baseline CPU near zero in quiet rooms without clipping quiet wakes). Detection threshold defaults to **0.5** (openWakeWord recommendation).

## Factory + missing-weights behaviour

`load_default_wake_detector()` returns the real detector when the vendored model file is present; raises `RuntimeError` if not. **No silent mock fallback in production** — a missing weight is a real configuration error.

`REACHY_DUCKY_WAKE_MOCK=1` env override returns `MockWakeDetector` (explicit dev/test escape hatch).

## UX: the human needs to know when to talk

Wake-fired transitions immediately to `State.LISTENING` so the bot perks up — head/antenna motion via the existing `_STATE_TO_MOVE` mapping. This is the same affordance Mycroft / HA Voice / Pollen's `reachy_mini_conversation_app` use: the wake confirmation **is** the listening-state transition. A single boot-time "armed" cue (e.g., antenna blink) ships with #55 if it stays small; a richer menubar status string is a separate follow-up enhancement.

## Pitfalls the prior art surfaced

1. **Refractory period.** Wyoming uses a wall-clock; we use lifecycle (Phase 2 = refractory). Cleaner.
2. **Pre-roll buffering.** Wyoming forwards the wake-detection chunk into ASR so the first syllable of the command isn't clipped. **Not needed for our shape** — OpenAI Realtime expects an explicit user turn after `response.create`, not continuation of pre-wake audio. The wake word itself is discarded.
3. **AEC routing.** Reachy Mini routes via the AEC loopback (`audio_gstreamer.py:158-163`). Pattern C preserves this — we never bypass `MediaManager`.
4. **VAD before wake is a CPU win.** Enabled via `vad_threshold=0.3` constructor arg.
5. **Drop-on-overflow.** GStreamer's `appsink` is `drop=True, max-buffers=200`. Slow consumers silently lose frames. Wake pump is fast (5 ms inference per 80 ms frame); voice was already proven-fast in #68. No new risk.

## Testing strategy

- **Unit tier.** Existing `MockWakeDetector` tests retained. New `OpenWakeWordDetector` tests use a fake `Model` injected via constructor — no real ONNX weights required for unit tests. Covers: buffer accumulation, threshold gating, sample-rate adaptation, reset semantics, factory missing-weights raise, env override returns mock.
- **Integration tier.** New `test_main_wake_pump.py` exercises the full lifecycle handoff with a fake mic + fake voice + always-firing fake wake. Pins: number of `mic.frames()` re-entries == number of turns; `wake.feed` calls during Phase 2 == 0; `sm.transition(LISTENING)` fires once per wake.
- **Hardware tier.** New `test_wake_hardware.py`, `@pytest.mark.hardware`. Single human-in-the-loop smoke: human says "hey jarvis" within 10 s, `wake.event` fires exactly once, follow-up 5 s window confirms wake.feed paused during turn.

## Out of scope (deferred, with rationale)

- **Custom "hey ducky" model training.** openWakeWord's documented synthetic-data pipeline; not a code change. Filed as a separate follow-up issue.
- **Menubar status string** ("Listening for 'hey jarvis'…" during Phase 1, "In conversation" during Phase 2). Useful for debugging, but a menubar feature, not a wake feature. Filed as a separate enhancement.
- **Post-turn cooldown.** No wall-clock refractory in v1; lifecycle handles it. Re-evaluate only if real-room testing shows the AEC tail bleeds into the next listen window.

## Acceptance

- `WakeDetector` ABC unchanged for callers (event-driven contract preserved).
- `OpenWakeWordDetector` ships in `wake.py` with vendored weights at `app/assets/wake/`.
- Hardware tier (`uv run pytest -m hardware`) gains `test_wake_hardware.py` — passes against a live Reachy Mini Wireless.
- `main._run_async` runs Pattern C handoff; integration tests pin the lifecycle.
- Unit tier coverage stays ≥ 90 %.
- Issue #55 closes.
