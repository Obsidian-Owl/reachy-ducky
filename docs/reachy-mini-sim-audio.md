# Reachy Mini sim audio — upstream tracking

**Last verified:** 2026-04-23

## Status today

Pollen's Reachy Mini simulator covers motion (MuJoCo physics, full
6-DOF head + antennas + body-yaw) but **does not** provide audio
input/output. Mic/speaker code running in sim will hit the laptop's
system devices (or fail, depending on the backend).

The daemon media architecture (see `reachy-mini-daemon --help`) lists
audio backends: `PulseAudio`, `ALSA`, `WASAPI`, `CoreAudio`. No `sim`
variant.

## Consequence for Reachy Ducky

`ReachyMicSource` / `ReachySpeakerSink` (see
`app/src/reachy_ducky_app/voice/audio_io.py`, closed via #23) are
**hardware-only** — the `load_default_*` factories return the mock
impls when not on a real robot. Sim can drive the motion/lifecycle
side of the app but not the voice side.

## Upstream issues to watch

- **[pollen-robotics/reachy_mini#330](https://github.com/pollen-robotics/reachy_mini/issues/330)** — webcam-fallback in sim. Adjacent pattern; no audio equivalent yet.
- **No audio-specific sim issue exists today.** If you hit this need
  again, consider filing one.

## Action for Reachy Ducky maintainers

Subscribe to new issues tagged `sim` in `pollen-robotics/reachy_mini`
and scan for audio keywords quarterly. When Pollen lands sim audio
loopback:

1. Revisit the factory logic in
   `app/src/reachy_ducky_app/voice/audio_io.py`. Today it returns mocks
   when not on real hardware — a sim-audio-capable runtime would be a
   third branch.
2. Add an `@pytest.mark.sim` test exercising a mic→speaker loopback
   through the sim.
3. Close this doc's "track" status; either move the findings into the
   main design doc or delete the doc.
