# Code Intelligence Tool Selection

## The Ladder

Use the cheapest tool that answers the question. Escalate only when it fails.

| Step | Tool | Use when | ~Tokens |
|------|------|----------|---------|
| 1 | `Grep` | Known symbol, string, file pattern | 50 |
| 2 | `GitNexus impact()` | Before editing shared contracts or cross-package symbols | 4,000 |
| 3 | `GitNexus cypher()` | Structural queries Grep can't answer ("all implementors of X") | 1,000 |
| 4 | `GitNexus detect_changes()` | Pre-commit scope verification | 500 |

**Auggie is deferred.** Re-evaluate when the repo crosses ~100 files.

## Grep (default)

Grep is the right tool for ~70% of lookups. Use it whenever you know the symbol, function name, class name, or error string.

```
Grep("class BrainInterface", type="py")
Grep("def query", type="py", path="daemon")
Grep("REACHY_DUCKY_", type="py")
```

## GitNexus

Local code knowledge graph. **Always pass `repo: "reachy-ducky"`** — the index supports multiple repos and will error without it.

### `impact()` — before editing shared code

The only tool that answers "what breaks if I change X?" Returns depth-grouped dependents with risk level.

**Use before editing:**
- `BrainInterface`, `VoiceInterface`, `MotionDriver`, `WakeDetector`, any ABC
- `protocol/` messages (they're the wire contract)
- `create_app`, `DaemonClient`, anything exported from subpackage `__init__.py`
- Any function with ≥5 callers or called from another subpackage

**Do NOT use for:**
- Local edits inside a single file
- Private (`_`-prefixed) functions
- Test-only changes
- Docstring or type-hint-only changes

### `detect_changes()` — before committing

Maps staged diffs to affected symbols. Cheap (~500 tokens) safety net.

```
gitnexus_detect_changes({scope: "staged", repo: "reachy-ducky"})
```

### `cypher()` — structural queries

Precise graph queries when Grep's regex isn't enough.

```
gitnexus_cypher({
  query: "MATCH (c)-[:CodeRelation {type: 'IMPLEMENTS'}]->(i {name: 'BrainInterface'}) RETURN c.name, c.filePath",
  repo: "reachy-ducky"
})
```

### Avoid in routine use

| Tool | Problem | Use instead |
|------|---------|-------------|
| `gitnexus_query()` | 2-5k tokens, noisy | Grep |
| `gitnexus_context()` | 3-5k tokens, full 360 dump | `Read` the file |
| `gitnexus_rename()` | Heavyweight for simple renames | Grep + Edit |

## Anti-patterns

- **Running `gitnexus_impact` on every edit.** Reserve it for shared contracts and cross-package symbols.
- **Using `gitnexus_query` for known names.** Grep is 50× cheaper.
- **Using `gitnexus_context` to "understand" a symbol.** Read the file — cheaper, clearer.
- **Forgetting the `repo:` parameter.** GitNexus errors or returns cross-repo noise.

## Maintenance

- The PostToolUse hook runs `npx gitnexus analyze --incremental` after Python file edits; the index stays warm automatically.
- If queries feel stale, run a full `npx gitnexus analyze` manually.
- `.gitnexusignore` excludes `tests/`, `docs/`, `.venv/`, `dist/`, `build/`.
