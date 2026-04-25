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
unmodified copies of openWakeWord's release artefacts.

## Provenance

`openwakeword==0.6.0` does NOT ship its weights inside the wheel — it
lazy-downloads them from the GitHub v0.5.1 release on first
`Model()` instantiation. To vendor a deterministic offline copy, the
weights here were obtained via:

```bash
uv run python -c "import openwakeword.utils; openwakeword.utils.download_models()"
```

then copied from the resulting cache. This guarantees the vendored
bytes are exactly what `openwakeword` would have downloaded itself.

To refresh, re-run the steps in Task 2 of
`docs/plans/2026-04-26-onnx-wake-detector-plan.md`.

## SHA256 (for audit)

| File | SHA256 |
|------|--------|
| hey_jarvis.onnx | 94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb |
| melspectrogram.onnx | ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f |
| embedding_model.onnx | 70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f |
