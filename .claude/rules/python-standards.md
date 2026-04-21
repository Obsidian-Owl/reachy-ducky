# Python Standards

## Type Safety (mandatory)

Every `.py` file starts with:

```python
from __future__ import annotations
```

Every function, method, and public variable has type hints. Modern generics only (`list[str]`, `dict[str, int]`). `mypy --strict` must pass.

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


def read_plan(path: Path) -> str:
    return path.read_text()


class Concern(BaseModel):
    text: str
    severity: int
```

No `Any` except at the boundary of an untyped third-party library. No `# type: ignore` without a comment explaining why and a linked issue or TODO.

## Pydantic v2 only

Use v2 syntax exclusively:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def trim(cls, v: str) -> str:
        return v.strip()
```

Use `SecretStr` for any secret field. Never log a `SecretStr`.

## Tooling

- **Ruff** for lint + format. 100-char lines. Run via pre-commit; CI fails on unformatted.
- **mypy --strict** for type checking. CI fails on type errors.
- **Bandit** for security scanning. CI fails on medium+ findings.
- **`uv`** for dependency management. The repo is a `uv` workspace; all subpackages share one resolved lock file.

## Module organization

```python
"""One-line module docstring."""

from __future__ import annotations

# 1. stdlib
import logging
from pathlib import Path

# 2. third-party
from pydantic import BaseModel

# 3. local (prefer absolute imports)
from reachy_ducky_protocol.messages import BrainRequest

logger = logging.getLogger(__name__)
```

## Logging

Use the stdlib `logging` module (or `structlog` if we add it later). Log structured context, not f-strings of business data. Never log secrets, API keys, user utterances that might contain secrets, or full tool output.

```python
logger.info("brain query", extra={"project_slug": slug, "utterance_len": len(text)})
```

## Forbidden constructs

- `eval`, `exec`
- `pickle.loads` on untrusted data
- `subprocess.run(..., shell=True)` with any variable content — use list form
- Hardcoded secrets (even in tests — use env vars with safe defaults)
- Bare `except:`
- Mutable default arguments
- Empty `except: pass` blocks (log + re-raise, or explain in comment)

## File / path conventions

- Absolute `Path` handling everywhere. No string concatenation for paths.
- All paths passed between subpackages go through Pydantic models, not bare strings.

## Comments

Default to no comments. Write one only when the *why* is non-obvious. Never paraphrase the code. Never reference "this PR" or "added for X" — those belong in commit messages.
