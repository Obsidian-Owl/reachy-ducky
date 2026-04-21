# Testing Standards

## Test code is production code

Type hints, docstrings, security discipline — same standards as `src/`. `mypy --strict` runs over tests too.

## TDD is the default

For every substantive behavior:

1. Write the failing test first
2. Run it — confirm it fails, and confirm the failure message is the one you expected
3. Write the minimal code to pass
4. Run it — confirm it passes
5. Commit

Don't batch tests at the end. Don't write tests that just re-assert the implementation.

## Tiers & markers

Three test tiers, gated by markers in `pyproject.toml`:

| Tier | Marker | Default | Runs where |
|------|--------|---------|------------|
| Unit | (none) | ✅ always | CI + local |
| Integration | `@pytest.mark.integration` | ❌ opt-in | Local with env vars |
| Hardware | `@pytest.mark.hardware` | ❌ opt-in | Reachy Mini connected |

Integration tests that need a live API check the env var and `pytest.skip` with a clear message only if the var is missing. Tests that describe real behavior never `skip` — they fail.

## Tests FAIL, never skip

**Forbidden:**
- `@pytest.mark.skip` / `@pytest.mark.skipif(<business condition>)`
- `pytest.skip(...)` inside the test body for anything except a missing env var on an `@integration` test or `importorskip` on a genuinely optional dep

Skipped tests are invisible failures. If infrastructure is missing for a real test, let it **fail** with a clear message telling the reader what's missing.

## No `time.sleep()` in tests

Use polling helpers with a timeout. Hardcoded sleeps are racy and slow.

```python
# forbidden
time.sleep(2)
assert service.ready()

# correct
assert wait_for(lambda: service.ready(), timeout=5.0)
```

## Assertion integrity

**Never weaken an assertion to make a failing test pass.** If a test fails, the code is wrong — not the test. When a test reveals a real problem, stop and escalate per `quality-escalation.md`.

Assertion strength hierarchy (use the strongest that fits):

| Strength | Pattern | When |
|---------|---------|------|
| Strongest | `assert x == expected_value` | Default |
| Strong | `assert set(xs) == {"a", "b"}` | Order doesn't matter |
| Moderate | `assert len(xs) == 3` | Values vary (e.g., UUIDs) |
| Weak | `assert len(xs) > 0` | Only when count genuinely varies |
| Forbidden | `assert x is not None` | Never — for values that should have specific content |

## Side-effect verification (non-negotiable)

**For every method whose purpose is an action (`speak_text`, `play_move`, `goto_sleep`, `brain_query`, `push`, `emit`, …), the test must assert the action occurred — not just that the return value has the right shape.**

This guards against the "Accomplishment Simulator" anti-pattern: a method that builds a correct-looking result, logs success, and returns `success=True` without ever performing the action.

```python
# forbidden — shape-only
async def test_speak_sends_text(turn):
    await turn.speak_text("hi")
    assert turn is not None   # proves nothing

# correct — invocation assertion
async def test_speak_sends_text(turn, mock_session):
    await turn.speak_text("hi")
    mock_session.response.create.assert_awaited_once()
    args = mock_session.response.create.await_args.args[0]
    assert "hi" in args["instructions"]
```

**Rule:** every `MagicMock()` in a fixture must have a corresponding `assert_called*()` or `assert_awaited*()` somewhere in the test file. An unasserted mock is an import-satisfying placeholder, not a test double.

## Floating-point equality

Use `pytest.approx()` or `math.isclose()`. Never `==` on floats.

## Isolation

Tests must be independently runnable in any order. No module-level globals mutated by tests. Use `tmp_path` fixtures; generate unique IDs/namespaces per test if you must share a backing store.

## Placement

- Tests for a single subpackage live in `<subpackage>/tests/`.
- Tests that import from 2+ subpackages live at the repo root `tests/` (cross-package contracts).
- No `__init__.py` in test directories (breaks `pytest --import-mode=importlib`).

## What to avoid

- Mocks in integration tests where the point is to exercise the real thing.
- Tests that only check types or existence (`isinstance(result, X)` alone is weak).
- Tests that only exercise error paths without also testing the happy path.
- Tests that hardcode values that the code itself derives (circular reasoning).
