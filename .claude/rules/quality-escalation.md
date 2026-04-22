# Quality Escalation Protocol

## Core Principle

**Do not silently make design decisions.** When there is more than one valid approach, stop and ask. The cost of a 30-second question is far lower than the cost of a hidden wrong turn.

## HARD STOP Triggers

Escalate via `AskUserQuestion` (or plain question if unavailable) when any of these occur:

### 1. Design choice with >1 valid approach
Any trade-off between approaches. Do not pick the one that feels simpler — present them.

**Examples:**
- Two ways to structure an interface
- A choice between library A and library B
- Whether to inline vs extract a helper
- Whether to add a configuration knob or hardcode
- Whether to break compatibility or preserve it

### 2. Workaround introduction
Any code that routes around a problem rather than solving it.

**Examples:**
- Monkey-patching private APIs
- `except Exception: pass` to swallow errors
- Hardcoding a value that should be derived
- Adding `# type: ignore` to bypass a real type error
- Replacing a real service call with a mock in an integration test

### 3. Test assertion weakening
When a test fails, the code is wrong — not the test. Never silently weaken an assertion to make it pass. See `testing-standards.md`.

### 4. Deviation from the plan
If the plan says X and you'd prefer Y, escalate. Plans are negotiable; silent deviations are not.

### 5. Scope expansion
Adding features, refactors, or "cleanup" beyond what the task specified. Bug fix ≠ surrounding tidy-up. Escalate first.

### 6. Shared-contract edits
Before editing `BrainInterface`, `VoiceInterface`, `MotionDriver`, or any `protocol/` message, run `gitnexus impact()` and report the blast radius. If HIGH risk, escalate before editing.

## Escalation Format

1. **What happened** — factual description
2. **Root cause** — why the problem exists
3. **Options** — 2-4 concrete paths with trade-offs
4. **Recommendation** — your pick and reasoning

```
"Test `test_brain_streams_chunks` is failing because the Claude SDK's event
 schema changed between 0.3 and 0.4. Options:
   A) Pin claude-agent-sdk to 0.3 (blocks future upgrades)
   B) Update the event parser to handle both shapes (more code, future-proof)
   C) Update the parser for 0.4 only and bump the minimum (cleaner, requires sub upgrade)
 I'd pick C — the 0.4 schema is better. Okay?"
```

## What's Autonomous

Only mechanical tasks with objectively correct outcomes:

| Autonomous | Needs Escalation |
|-----------|------------------|
| Running ruff/format | Choosing a new linter |
| Adding missing type hints | Changing a type signature used across packages |
| Fixing a clear bug with a single root cause | Choosing between 2+ valid fixes |
| Docstring additions | Changing docstring conventions |

## Anti-Patterns (forbidden)

- **Silent architect** — making a design decision without asking. The most common and most damaging pattern.
- **Silent softener** — weakening test assertions to make failing tests pass.
- **Exception swallower** — `except: pass` to hide a real error.
- **Scope creeper** — adding refactors or "cleanup" to a bug fix without asking.
- **Mock smuggler** — replacing a real service with a mock in an integration test.
