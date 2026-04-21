# Reachy Ducky — Phase A MVP Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Ship the Phase A MVP of Reachy Ducky — an on-demand conversational companion running on a Reachy Mini, with a Mac-side daemon that holds the memory, tool belt, and plan-reviewer specialist. No event-driven observation, no interruption policy, no always-on mode — the user summons Ducky, Ducky answers.

**Architecture:** Split-brain over LAN. The **Mac daemon** (`daemon/`) owns the Claude Agent SDK brain (pluggable), Basic Memory MCP, read-only tool belt (git/gh/fs/plans), and the plan-reviewer specialist — exposed via HTTP. A **Mac menu-bar app** (`menubar/`) shows state + hosts the mute toggle. The **Reachy Mini app** (`app/`) owns the voice layer (OpenAI Realtime via `fastrtc`), wake word, hard-mute gate, embodiment state machine (`play_move` + `look_at_image`), and calls the daemon for reasoning. A tiny **protocol package** (`protocol/`) holds shared Pydantic messages.

**Tech Stack:**
- Python 3.12, `uv` workspace, `pyproject.toml` per subpackage
- Pydantic v2 (protocol), FastAPI + `uvicorn` (daemon HTTP server), `httpx` (app → daemon client)
- `claude-agent-sdk` (Python), OAuth via locally-installed `claude` CLI
- `fastrtc`, `openai` (Realtime API) — voice layer
- `reachy_mini` SDK, `mediapipe` (face detect for gaze)
- `rumps` (Mac menu-bar)
- `pytest`, `pytest-asyncio`, `pytest-httpx`, `ruff`
- Basic Memory MCP (external MCP server, configured)

**Conventions used throughout:**
- Every substantive code task follows TDD: write failing test → run it (fails) → implement → run it (passes) → commit.
- Hardware-dependent tests are tagged `@pytest.mark.hardware` and skipped in CI.
- Integration tests needing live APIs are tagged `@pytest.mark.integration` and gated on env vars.

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion

---

## Milestone 0 — Project scaffolding

### Task 0.1: Add LICENSE and base README

**Files:**
- Create: `LICENSE` (Apache 2.0 standard text)
- Create: `README.md`

**Step 1: Fetch Apache 2.0 text**

Run: `curl -s https://www.apache.org/licenses/LICENSE-2.0.txt -o LICENSE`

**Step 2: Write `README.md`**

```markdown
# Reachy Ducky

A personal, embodied "rubber ducky" development companion for Reachy Mini.
Read-only desk robot that watches your agentic SWE workflow and talks with you.

See `docs/plans/2026-04-21-reachy-ducky-design.md` for the full design.

Status: Phase A MVP in progress.

Licensed under Apache 2.0.
```

**Step 3: Commit**

```bash
git add LICENSE README.md
git commit -m "chore: add Apache 2.0 license and baseline README"
```

---

### Task 0.2: Add `.gitignore`

**Files:**
- Create: `.gitignore`

**Step 1: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.venv-*/
dist/
build/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/
.env
.env.*
!.env.example
.DS_Store
*.log
uv.lock
```

(Note: `uv.lock` is in gitignore for subpackages; we commit a root `uv.lock` later.)

**Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add .gitignore"
```

---

### Task 0.3: Create monorepo structure

**Files:**
- Create: `daemon/`, `app/`, `menubar/`, `protocol/` (each with `src/`, `tests/`, `pyproject.toml`)
- Create root `pyproject.toml` (workspace config)

**Step 1: Create directory skeleton**

```bash
mkdir -p daemon/src/reachy_ducky_daemon daemon/tests
mkdir -p app/src/reachy_ducky_app app/tests
mkdir -p menubar/src/reachy_ducky_menubar menubar/tests
mkdir -p protocol/src/reachy_ducky_protocol protocol/tests
touch daemon/src/reachy_ducky_daemon/__init__.py
touch app/src/reachy_ducky_app/__init__.py
touch menubar/src/reachy_ducky_menubar/__init__.py
touch protocol/src/reachy_ducky_protocol/__init__.py
touch daemon/tests/__init__.py app/tests/__init__.py menubar/tests/__init__.py protocol/tests/__init__.py
```

**Step 2: Write root `pyproject.toml`**

```toml
[tool.uv.workspace]
members = ["daemon", "app", "menubar", "protocol"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["daemon/tests", "app/tests", "menubar/tests", "protocol/tests"]
markers = [
    "hardware: requires Reachy Mini hardware",
    "integration: requires live external APIs",
]
```

**Step 3: Write each subpackage `pyproject.toml`**

For `protocol/pyproject.toml`:

```toml
[project]
name = "reachy-ducky-protocol"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.7"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/reachy_ducky_protocol"]
```

For `daemon/pyproject.toml`:

```toml
[project]
name = "reachy-ducky-daemon"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = [
    "reachy-ducky-protocol",
    "claude-agent-sdk>=0.3",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-httpx>=0.30", "ruff>=0.6"]

[project.scripts]
reachy-ducky-daemon = "reachy_ducky_daemon.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/reachy_ducky_daemon"]

[tool.uv.sources]
reachy-ducky-protocol = { workspace = true }
```

For `app/pyproject.toml`:

```toml
[project]
name = "reachy-ducky-app"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = [
    "reachy-ducky-protocol",
    "reachy-mini",
    "fastrtc",
    "openai>=1.50",
    "mediapipe>=0.10",
    "httpx>=0.27",
    "numpy",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.6"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/reachy_ducky_app"]

[tool.uv.sources]
reachy-ducky-protocol = { workspace = true }
```

For `menubar/pyproject.toml`:

```toml
[project]
name = "reachy-ducky-menubar"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = ["rumps>=0.4", "httpx>=0.27", "reachy-ducky-protocol"]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.6"]

[project.scripts]
reachy-ducky-menubar = "reachy_ducky_menubar.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/reachy_ducky_menubar"]

[tool.uv.sources]
reachy-ducky-protocol = { workspace = true }
```

**Step 4: Sync dependencies**

Run: `uv sync --all-packages`
Expected: `.venv/` created; all subpackages linked as editable installs; lock file written.

**Step 5: Verify install**

Run: `uv run python -c "import reachy_ducky_protocol, reachy_ducky_daemon, reachy_ducky_app, reachy_ducky_menubar; print('ok')"`
Expected: `ok`

**Step 6: Commit**

```bash
git add pyproject.toml uv.lock daemon app menubar protocol
git commit -m "chore: scaffold uv workspace with daemon/app/menubar/protocol subpackages"
```

---

## Milestone 1 — Protocol (shared messages)

### Task 1.1: Define the wire protocol

**Files:**
- Create: `protocol/src/reachy_ducky_protocol/messages.py`
- Test: `protocol/tests/test_messages.py`

**Step 1: Write the failing test**

```python
# protocol/tests/test_messages.py
from reachy_ducky_protocol.messages import (
    BrainRequest,
    BrainResponse,
    UserUtterance,
    SpecialistRequest,
    HealthResponse,
    State,
)


def test_brain_request_serializes():
    req = BrainRequest(
        user_utterance="what's on my branch?",
        project_slug="reachy-ducky",
        include_tools=["git", "gh", "fs", "plans"],
    )
    data = req.model_dump()
    assert data["user_utterance"] == "what's on my branch?"
    assert "git" in data["include_tools"]


def test_brain_response_round_trip():
    resp = BrainResponse(text="you have 3 commits ahead of main", specialist_invoked=None)
    clone = BrainResponse.model_validate_json(resp.model_dump_json())
    assert clone.text == resp.text


def test_specialist_request_plan_reviewer():
    req = SpecialistRequest(
        name="plan-reviewer",
        project_slug="reachy-ducky",
        branch="main",
    )
    assert req.name == "plan-reviewer"


def test_state_enum_values():
    assert State.IDLE.value == "idle"
    assert State.LISTENING.value == "listening"
    assert State.THINKING.value == "thinking"
    assert State.MUTED.value == "muted"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest protocol/tests/test_messages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reachy_ducky_protocol.messages'`

**Step 3: Implement the protocol**

```python
# protocol/src/reachy_ducky_protocol/messages.py
from enum import Enum

from pydantic import BaseModel, Field


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    MUTED = "muted"


class UserUtterance(BaseModel):
    text: str
    project_slug: str | None = None


class BrainRequest(BaseModel):
    user_utterance: str
    project_slug: str | None = None
    include_tools: list[str] = Field(default_factory=list)


class BrainResponse(BaseModel):
    text: str
    specialist_invoked: str | None = None


class SpecialistRequest(BaseModel):
    name: str
    project_slug: str
    branch: str | None = None


class SpecialistResponse(BaseModel):
    name: str
    summary: str
    flags: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    ok: bool
    brain: str
    memory_ready: bool
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest protocol/tests/test_messages.py -v`
Expected: all 4 tests PASS.

**Step 5: Commit**

```bash
git add protocol/
git commit -m "feat(protocol): define shared Pydantic messages for daemon/app IPC"
```

---

## Milestone 2 — Daemon: BrainInterface + Claude Agent SDK impl

### Task 2.1: BrainInterface abstract contract + mock

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/brain/__init__.py`
- Create: `daemon/src/reachy_ducky_daemon/brain/interface.py`
- Create: `daemon/src/reachy_ducky_daemon/brain/mock.py`
- Test: `daemon/tests/test_brain_interface.py`

**Step 1: Write the failing test**

```python
# daemon/tests/test_brain_interface.py
import pytest

from reachy_ducky_daemon.brain.interface import BrainInterface
from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_protocol.messages import BrainRequest


def test_brain_interface_is_abstract():
    with pytest.raises(TypeError):
        BrainInterface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_mock_brain_echoes_prompt():
    brain = MockBrain()
    resp = await brain.query(BrainRequest(user_utterance="hello"))
    assert resp.text == "[mock] hello"


@pytest.mark.asyncio
async def test_mock_brain_records_calls():
    brain = MockBrain()
    await brain.query(BrainRequest(user_utterance="ping"))
    assert brain.calls[-1].user_utterance == "ping"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_brain_interface.py -v`
Expected: FAIL (module not found).

**Step 3: Implement the contract**

```python
# daemon/src/reachy_ducky_daemon/brain/interface.py
from abc import ABC, abstractmethod

from reachy_ducky_protocol.messages import BrainRequest, BrainResponse


class BrainInterface(ABC):
    """Abstract brain. Implementations: ClaudeSDKBrain, CodexBrain (future), MockBrain."""

    @abstractmethod
    async def query(self, request: BrainRequest) -> BrainResponse: ...
```

```python
# daemon/src/reachy_ducky_daemon/brain/mock.py
from reachy_ducky_protocol.messages import BrainRequest, BrainResponse

from .interface import BrainInterface


class MockBrain(BrainInterface):
    def __init__(self) -> None:
        self.calls: list[BrainRequest] = []

    async def query(self, request: BrainRequest) -> BrainResponse:
        self.calls.append(request)
        return BrainResponse(text=f"[mock] {request.user_utterance}")
```

```python
# daemon/src/reachy_ducky_daemon/brain/__init__.py
from .interface import BrainInterface
from .mock import MockBrain

__all__ = ["BrainInterface", "MockBrain"]
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_brain_interface.py -v`
Expected: 3 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add BrainInterface ABC and MockBrain test double"
```

---

### Task 2.2: Claude Agent SDK brain implementation

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/brain/claude_sdk.py`
- Test: `daemon/tests/test_brain_claude_sdk.py`

**Step 1: Write the failing test** (uses `pytest-httpx` is not needed; we patch the SDK at the module boundary)

```python
# daemon/tests/test_brain_claude_sdk.py
from unittest.mock import AsyncMock, patch

import pytest

from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain
from reachy_ducky_protocol.messages import BrainRequest


@pytest.mark.asyncio
async def test_claude_sdk_brain_joins_streamed_text():
    fake_chunks = [
        {"type": "text", "text": "hello "},
        {"type": "text", "text": "world"},
    ]

    async def fake_query(*args, **kwargs):
        for chunk in fake_chunks:
            yield chunk

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain(system_prompt="you are Ducky")
        resp = await brain.query(BrainRequest(user_utterance="hi"))

    assert resp.text == "hello world"


@pytest.mark.asyncio
async def test_claude_sdk_brain_passes_user_prompt():
    seen = {}

    async def fake_query(prompt, options):
        seen["prompt"] = prompt
        yield {"type": "text", "text": "ack"}

    with patch("reachy_ducky_daemon.brain.claude_sdk.sdk_query", new=fake_query):
        brain = ClaudeSDKBrain()
        await brain.query(BrainRequest(user_utterance="what's on my branch?"))

    assert "what's on my branch?" in seen["prompt"]
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_brain_claude_sdk.py -v`
Expected: FAIL (module not found).

**Step 3: Implement the Claude SDK wrapper**

```python
# daemon/src/reachy_ducky_daemon/brain/claude_sdk.py
from __future__ import annotations

from claude_agent_sdk import query as sdk_query, ClaudeAgentOptions

from reachy_ducky_protocol.messages import BrainRequest, BrainResponse

from .interface import BrainInterface

DEFAULT_SYSTEM_PROMPT = (
    "You are Ducky, a read-only rubber-ducky development companion. "
    "You observe and answer; you do not write code. "
    "Be terse. Prefer concrete specifics over vague approval."
)


class ClaudeSDKBrain(BrainInterface):
    def __init__(
        self,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._system_prompt = system_prompt
        self._model = model

    async def query(self, request: BrainRequest) -> BrainResponse:
        options = ClaudeAgentOptions(
            system_prompt=self._system_prompt,
            model=self._model,
        )
        parts: list[str] = []
        async for chunk in sdk_query(prompt=request.user_utterance, options=options):
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                parts.append(chunk.get("text", ""))
        return BrainResponse(text="".join(parts))
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_brain_claude_sdk.py -v`
Expected: 2 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add Claude Agent SDK brain implementation"
```

---

### Task 2.3: Integration smoke test against live Claude (OAuth)

**Files:**
- Test: `daemon/tests/test_brain_claude_integration.py`

**Step 1: Write the integration test**

```python
# daemon/tests/test_brain_claude_integration.py
import os

import pytest

from reachy_ducky_daemon.brain.claude_sdk import ClaudeSDKBrain
from reachy_ducky_protocol.messages import BrainRequest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_claude_responds():
    if not os.environ.get("REACHY_DUCKY_RUN_INTEGRATION"):
        pytest.skip("set REACHY_DUCKY_RUN_INTEGRATION=1 to run")
    brain = ClaudeSDKBrain()
    resp = await brain.query(BrainRequest(user_utterance="Say the single word: pong"))
    assert "pong" in resp.text.lower()
```

**Step 2: Verify `claude login` is active**

Run: `claude --version && claude auth status`
Expected: logged-in session reported.

**Step 3: Run the integration test**

Run: `REACHY_DUCKY_RUN_INTEGRATION=1 uv run pytest daemon/tests/test_brain_claude_integration.py -v -m integration`
Expected: PASS (live Claude returns a response containing "pong").

**Step 4: Commit**

```bash
git add daemon/tests/test_brain_claude_integration.py
git commit -m "test(daemon): add gated live-Claude OAuth integration smoke test"
```

---

## Milestone 3 — Daemon: Read-only tool belt

Each tool is a thin typed wrapper over a subprocess call. All tools are **read-only**: no write subcommands.

### Task 3.1: Git tool

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/tools/__init__.py`
- Create: `daemon/src/reachy_ducky_daemon/tools/git.py`
- Test: `daemon/tests/test_tools_git.py`

**Step 1: Write the failing test** (uses `tmp_path` to create a tiny real repo)

```python
# daemon/tests/test_tools_git.py
import subprocess
from pathlib import Path

import pytest

from reachy_ducky_daemon.tools.git import GitTool


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("hello")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_git_status_clean(tmp_repo: Path):
    out = GitTool(tmp_repo).status()
    assert "nothing to commit" in out or "working tree clean" in out


def test_git_log_returns_init_commit(tmp_repo: Path):
    out = GitTool(tmp_repo).log(limit=10)
    assert "init" in out


def test_git_diff_shows_uncommitted(tmp_repo: Path):
    (tmp_repo / "a.txt").write_text("hello world")
    out = GitTool(tmp_repo).diff()
    assert "+hello world" in out


def test_git_rejects_write_commands(tmp_repo: Path):
    tool = GitTool(tmp_repo)
    with pytest.raises(ValueError, match="read-only"):
        tool.run(["push", "origin", "main"])
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_tools_git.py -v`
Expected: FAIL (module not found).

**Step 3: Implement**

```python
# daemon/src/reachy_ducky_daemon/tools/git.py
from __future__ import annotations

import subprocess
from pathlib import Path

ALLOWED = frozenset({
    "log", "diff", "show", "status", "branch", "rev-parse",
    "ls-files", "ls-tree", "config", "describe", "rev-list",
})


class GitTool:
    def __init__(self, repo: Path) -> None:
        self._repo = Path(repo)

    def run(self, args: list[str]) -> str:
        if not args or args[0] not in ALLOWED:
            raise ValueError(f"git subcommand not read-only: {args[0] if args else '<empty>'}")
        result = subprocess.run(
            ["git", *args],
            cwd=self._repo,
            capture_output=True,
            text=True,
            check=False,
        )
        return (result.stdout or "") + (result.stderr or "")

    def status(self) -> str:
        return self.run(["status"])

    def log(self, limit: int = 20) -> str:
        return self.run(["log", f"-n{limit}", "--oneline"])

    def diff(self, rev_range: str | None = None) -> str:
        return self.run(["diff"] + ([rev_range] if rev_range else []))

    def show(self, ref: str) -> str:
        return self.run(["show", ref])

    def current_branch(self) -> str:
        return self.run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
```

```python
# daemon/src/reachy_ducky_daemon/tools/__init__.py
from .git import GitTool

__all__ = ["GitTool"]
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_tools_git.py -v`
Expected: 4 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add read-only GitTool"
```

---

### Task 3.2: GitHub (`gh`) tool

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/tools/gh.py`
- Test: `daemon/tests/test_tools_gh.py`

**Step 1: Write the failing test** (mocks `subprocess.run`)

```python
# daemon/tests/test_tools_gh.py
from unittest.mock import patch

from reachy_ducky_daemon.tools.gh import GhTool


def _fake_run(stdout: str):
    class R:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0
    return R()


def test_gh_pr_view_invokes_subcommand():
    with patch("reachy_ducky_daemon.tools.gh.subprocess.run") as m:
        m.return_value = _fake_run('{"number": 42}')
        out = GhTool().pr_view(42, repo="o/r")
        assert "42" in out
        args = m.call_args.args[0]
        assert args[:3] == ["gh", "pr", "view"]


def test_gh_rejects_write_commands():
    tool = GhTool()
    import pytest
    with pytest.raises(ValueError, match="read-only"):
        tool.run(["pr", "create", "--title", "x"])
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_tools_gh.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# daemon/src/reachy_ducky_daemon/tools/gh.py
from __future__ import annotations

import subprocess

READ_ONLY = {
    ("pr", "view"), ("pr", "list"), ("pr", "diff"), ("pr", "checks"), ("pr", "status"),
    ("issue", "view"), ("issue", "list"), ("issue", "status"),
    ("run", "list"), ("run", "view"),
    ("repo", "view"),
    ("api", ""),   # GET-shaped uses of `gh api` — caller responsibility
}


class GhTool:
    def run(self, args: list[str]) -> str:
        if len(args) < 2 or (args[0], args[1]) not in {(a, b) for a, b in READ_ONLY}:
            # Allow bare `gh api` (caller sets GET)
            if not (args and args[0] == "api"):
                raise ValueError(f"gh subcommand not read-only: {' '.join(args[:2])}")
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        return (result.stdout or "") + (result.stderr or "")

    def pr_view(self, number: int, repo: str | None = None) -> str:
        args = ["pr", "view", str(number), "--json", "number,title,state,body,comments,statusCheckRollup"]
        if repo:
            args += ["--repo", repo]
        return self.run(args)

    def pr_diff(self, number: int, repo: str | None = None) -> str:
        args = ["pr", "diff", str(number)]
        if repo:
            args += ["--repo", repo]
        return self.run(args)
```

Update `daemon/src/reachy_ducky_daemon/tools/__init__.py`:

```python
from .gh import GhTool
from .git import GitTool

__all__ = ["GhTool", "GitTool"]
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_tools_gh.py -v`
Expected: 2 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add read-only GhTool"
```

---

### Task 3.3: Filesystem tool (scoped reads)

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/tools/fs.py`
- Test: `daemon/tests/test_tools_fs.py`

**Step 1: Write the failing test**

```python
# daemon/tests/test_tools_fs.py
from pathlib import Path

import pytest

from reachy_ducky_daemon.tools.fs import FsTool


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("B")
    (tmp_path / ".env").write_text("SECRET=1")
    return tmp_path


def test_read_file_inside_root(tree: Path):
    tool = FsTool(root=tree)
    assert tool.read_text("a.txt") == "A"


def test_reject_escape(tree: Path):
    tool = FsTool(root=tree)
    with pytest.raises(PermissionError):
        tool.read_text("../outside.txt")


def test_reject_blocked_pattern(tree: Path):
    tool = FsTool(root=tree, blocked_globs=[".env*", "*.pem"])
    with pytest.raises(PermissionError, match="blocked"):
        tool.read_text(".env")


def test_glob_lists_non_blocked(tree: Path):
    tool = FsTool(root=tree, blocked_globs=[".env*"])
    files = tool.glob("**/*.txt")
    names = {p.name for p in files}
    assert names == {"a.txt", "b.txt"}
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_tools_fs.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# daemon/src/reachy_ducky_daemon/tools/fs.py
from __future__ import annotations

import fnmatch
from pathlib import Path

DEFAULT_BLOCKED = [".env*", "*.pem", "*.key", "id_rsa*", "secrets/**", "credentials*"]


class FsTool:
    def __init__(self, root: Path, blocked_globs: list[str] | None = None) -> None:
        self._root = Path(root).resolve()
        self._blocked = list(blocked_globs) if blocked_globs is not None else list(DEFAULT_BLOCKED)

    def _resolve(self, rel: str) -> Path:
        candidate = (self._root / rel).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise PermissionError(f"path escapes root: {rel}") from exc
        return candidate

    def _check_blocked(self, rel: str) -> None:
        for pattern in self._blocked:
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern):
                raise PermissionError(f"blocked: {rel}")

    def read_text(self, rel: str) -> str:
        self._check_blocked(rel)
        return self._resolve(rel).read_text()

    def glob(self, pattern: str) -> list[Path]:
        out: list[Path] = []
        for p in self._root.glob(pattern):
            rel = str(p.relative_to(self._root))
            try:
                self._check_blocked(rel)
            except PermissionError:
                continue
            out.append(p)
        return out
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_tools_fs.py -v`
Expected: 4 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add scoped read-only FsTool with blocklist"
```

---

### Task 3.4: Plan/spec reader tool

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/tools/plans.py`
- Test: `daemon/tests/test_tools_plans.py`

Finds plans/specs via the conventional paths: `docs/plans/**/*.md`, `specs/**/*.md`, root-level `AGENTS.md`, `CLAUDE.md`, `SPEC.md`, and any `*.plan.md`.

**Step 1: Write the failing test**

```python
# daemon/tests/test_tools_plans.py
from pathlib import Path

import pytest

from reachy_ducky_daemon.tools.plans import PlansTool


@pytest.fixture
def repo_with_plans(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "2026-04-21-foo.md").write_text("# Plan Foo")
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "api.md").write_text("# API spec")
    (tmp_path / "CLAUDE.md").write_text("# Claude")
    (tmp_path / "README.md").write_text("not a plan")
    return tmp_path


def test_discovers_conventional_locations(repo_with_plans: Path):
    tool = PlansTool(root=repo_with_plans)
    paths = [str(p.relative_to(repo_with_plans)) for p in tool.find_all()]
    assert "docs/plans/2026-04-21-foo.md" in paths
    assert "specs/api.md" in paths
    assert "CLAUDE.md" in paths
    assert "README.md" not in paths


def test_read_plan(repo_with_plans: Path):
    tool = PlansTool(root=repo_with_plans)
    content = tool.read("docs/plans/2026-04-21-foo.md")
    assert content.startswith("# Plan Foo")
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_tools_plans.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# daemon/src/reachy_ducky_daemon/tools/plans.py
from __future__ import annotations

from pathlib import Path

CONVENTIONAL_PATTERNS = [
    "docs/plans/**/*.md",
    "specs/**/*.md",
    "AGENTS.md",
    "CLAUDE.md",
    "SPEC.md",
    "*.plan.md",
]


class PlansTool:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def find_all(self) -> list[Path]:
        seen: set[Path] = set()
        out: list[Path] = []
        for pattern in CONVENTIONAL_PATTERNS:
            for p in self._root.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    out.append(p)
        return out

    def read(self, rel: str) -> str:
        return (self._root / rel).read_text()
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_tools_plans.py -v`
Expected: 2 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add PlansTool for conventional plan/spec discovery"
```

---

## Milestone 4 — Daemon: Memory layout + SOUL.md seed

### Task 4.1: Memory directory scaffolder

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/memory/__init__.py`
- Create: `daemon/src/reachy_ducky_daemon/memory/layout.py`
- Create: `daemon/src/reachy_ducky_daemon/memory/templates.py`
- Test: `daemon/tests/test_memory_layout.py`

**Step 1: Write the failing test**

```python
# daemon/tests/test_memory_layout.py
from pathlib import Path

from reachy_ducky_daemon.memory.layout import ensure_layout, ensure_project


def test_ensure_layout_creates_expected_tree(tmp_path: Path):
    ensure_layout(tmp_path)
    assert (tmp_path / "ducky" / "soul.md").exists()
    assert (tmp_path / "ducky" / "core-blocks" / "stances.md").exists()
    assert (tmp_path / "ducky" / "core-blocks" / "running-jokes.md").exists()
    assert (tmp_path / "ducky" / "core-blocks" / "open-threads.md").exists()
    assert (tmp_path / "human" / "user.md").exists()
    assert (tmp_path / "human" / "feedback.md").exists()
    assert (tmp_path / "human" / "preferences.md").exists()
    assert (tmp_path / "projects").is_dir()


def test_ensure_project_creates_per_project_tree(tmp_path: Path):
    ensure_layout(tmp_path)
    ensure_project(tmp_path, slug="reachy-ducky")
    root = tmp_path / "projects" / "reachy-ducky"
    assert (root / "project.md").exists()
    assert (root / "people.md").exists()
    assert (root / "decisions.md").exists()
    assert (root / "concerns.md").exists()
    assert (root / "branches").is_dir()


def test_ensure_layout_is_idempotent(tmp_path: Path):
    ensure_layout(tmp_path)
    (tmp_path / "ducky" / "soul.md").write_text("# edited")
    ensure_layout(tmp_path)
    assert (tmp_path / "ducky" / "soul.md").read_text() == "# edited"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_memory_layout.py -v`
Expected: FAIL.

**Step 3: Implement templates**

```python
# daemon/src/reachy_ducky_daemon/memory/templates.py
SOUL_MD = """---
name: Ducky SOUL
description: Ducky's identity, stances, running threads. Editable by Ducky itself.
type: agent-self
---

# Who Ducky is

A desk-bound rubber-ducky companion for Dan's agentic software work. Terse, curious,
biased toward concrete specifics over vague approval. Reads; does not write code.

Watches carefully. Speaks up only when it matters.
"""

STANCES_MD = """# Stances

- Default to fewer moving parts over more.
- Tests that don't match the plan are a bigger smell than tests that are missing.
- Destructive git operations are never casual.
"""

RUNNING_JOKES_MD = "# Running jokes\n\n(seed empty)\n"
OPEN_THREADS_MD = "# Open threads\n\n(seed empty — threads Dan and Ducky are tracking)\n"

USER_MD = "# Human\n\n(Ducky will populate as it learns about Dan. See global memory for stable facts.)\n"
FEEDBACK_MD = "# Feedback history\n\n(Ducky records validated approaches and explicit corrections here.)\n"
PREFERENCES_MD = "# Preferences\n\n(Dan's stated preferences; Ducky confirms changes before overwriting.)\n"

PROJECT_MD = "# Project: {slug}\n\n(Seeded on first watch.)\n"
PEOPLE_MD = "# People\n\n(Names, roles, relationships relevant to the project.)\n"
DECISIONS_MD = "# Decisions\n\n(Log of decisions made while Ducky has been watching.)\n"
CONCERNS_MD = "# Current concerns\n\n(Things Ducky is worried about. Cleared when resolved.)\n"
"""
```

**Step 4: Implement layout**

```python
# daemon/src/reachy_ducky_daemon/memory/layout.py
from __future__ import annotations

from pathlib import Path

from . import templates


def _write_if_missing(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content)


def ensure_layout(root: Path) -> None:
    root = Path(root)
    _write_if_missing(root / "ducky" / "soul.md", templates.SOUL_MD)
    _write_if_missing(root / "ducky" / "core-blocks" / "stances.md", templates.STANCES_MD)
    _write_if_missing(root / "ducky" / "core-blocks" / "running-jokes.md", templates.RUNNING_JOKES_MD)
    _write_if_missing(root / "ducky" / "core-blocks" / "open-threads.md", templates.OPEN_THREADS_MD)
    _write_if_missing(root / "human" / "user.md", templates.USER_MD)
    _write_if_missing(root / "human" / "feedback.md", templates.FEEDBACK_MD)
    _write_if_missing(root / "human" / "preferences.md", templates.PREFERENCES_MD)
    (root / "projects").mkdir(parents=True, exist_ok=True)


def ensure_project(root: Path, slug: str) -> Path:
    proj = Path(root) / "projects" / slug
    _write_if_missing(proj / "project.md", templates.PROJECT_MD.format(slug=slug))
    _write_if_missing(proj / "people.md", templates.PEOPLE_MD)
    _write_if_missing(proj / "decisions.md", templates.DECISIONS_MD)
    _write_if_missing(proj / "concerns.md", templates.CONCERNS_MD)
    (proj / "branches").mkdir(parents=True, exist_ok=True)
    return proj
```

```python
# daemon/src/reachy_ducky_daemon/memory/__init__.py
from .layout import ensure_layout, ensure_project

__all__ = ["ensure_layout", "ensure_project"]
```

**Step 5: Run to verify pass**

Run: `uv run pytest daemon/tests/test_memory_layout.py -v`
Expected: 3 PASS.

**Step 6: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add memory layout scaffolder with SOUL.md seed"
```

---

### Task 4.2: Basic Memory MCP wiring (configuration only for MVP)

For Phase A we **read Markdown directly** via `FsTool` + `PlansTool`. Basic Memory MCP integration is configured so that the Claude Agent SDK brain can use it when invoked, but we don't require it on the daemon's critical path.

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/memory/mcp_config.py`
- Test: `daemon/tests/test_memory_mcp_config.py`

**Step 1: Write the failing test**

```python
# daemon/tests/test_memory_mcp_config.py
from pathlib import Path

from reachy_ducky_daemon.memory.mcp_config import basic_memory_mcp_config


def test_config_points_at_memory_root(tmp_path: Path):
    cfg = basic_memory_mcp_config(memory_root=tmp_path)
    assert cfg["mcpServers"]["basic-memory"]["command"] == "uvx"
    assert str(tmp_path) in " ".join(cfg["mcpServers"]["basic-memory"]["args"])
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_memory_mcp_config.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# daemon/src/reachy_ducky_daemon/memory/mcp_config.py
from pathlib import Path


def basic_memory_mcp_config(memory_root: Path) -> dict:
    return {
        "mcpServers": {
            "basic-memory": {
                "command": "uvx",
                "args": ["basic-memory", "mcp", "--project-path", str(memory_root)],
            }
        }
    }
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_memory_mcp_config.py -v`
Expected: 1 PASS.

**Step 5: Manual smoke check** (one-time)

Run: `uvx basic-memory mcp --help`
Expected: Basic Memory MCP CLI help text (confirms the MCP server is installable/runnable; does not block the MVP).

**Step 6: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add Basic Memory MCP config helper"
```

---

## Milestone 5 — Daemon: plan-reviewer specialist

### Task 5.1: plan-reviewer specialist

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/specialists/__init__.py`
- Create: `daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py`
- Test: `daemon/tests/test_specialist_plan_reviewer.py`

The specialist gathers the current plan + branch diff and asks the brain to flag drift.

**Step 1: Write the failing test**

```python
# daemon/tests/test_specialist_plan_reviewer.py
import subprocess
from pathlib import Path

import pytest

from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.specialists.plan_reviewer import PlanReviewer


@pytest.fixture
def repo_with_plan_and_change(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "2026-04-21-thing.md").write_text(
        "# Plan Thing\n\nAdd feature X with test coverage.\n"
    )
    (tmp_path / "a.py").write_text("def x(): return 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    # uncommitted change
    (tmp_path / "b.py").write_text("def y(): return 2\n")
    return tmp_path


@pytest.mark.asyncio
async def test_plan_reviewer_includes_plan_and_diff(repo_with_plan_and_change: Path):
    brain = MockBrain()
    reviewer = PlanReviewer(brain=brain, repo=repo_with_plan_and_change)
    await reviewer.review()
    prompt = brain.calls[-1].user_utterance
    assert "Plan Thing" in prompt
    assert "b.py" in prompt
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_specialist_plan_reviewer.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# daemon/src/reachy_ducky_daemon/specialists/plan_reviewer.py
from __future__ import annotations

from pathlib import Path

from reachy_ducky_protocol.messages import BrainRequest, SpecialistResponse

from ..brain.interface import BrainInterface
from ..tools.git import GitTool
from ..tools.plans import PlansTool

SYSTEM = (
    "Role: plan-reviewer specialist. Read the plan and the branch diff. "
    "Report deviations between the plan's stated scope and what the diff actually does. "
    "Be specific; cite file names. If the implementation matches the plan, say so plainly."
)


class PlanReviewer:
    def __init__(self, brain: BrainInterface, repo: Path) -> None:
        self._brain = brain
        self._repo = Path(repo)

    async def review(self) -> SpecialistResponse:
        plans_tool = PlansTool(self._repo)
        git = GitTool(self._repo)

        plans = plans_tool.find_all()
        plan_text = "\n\n".join(
            f"--- {p.relative_to(self._repo)} ---\n{p.read_text()}" for p in plans
        ) or "(no plan files found)"

        branch = git.current_branch()
        diff = git.diff(f"main...{branch}") if branch != "main" else git.diff()

        prompt = (
            f"{SYSTEM}\n\n"
            f"## Plans\n{plan_text}\n\n"
            f"## Branch diff ({branch})\n{diff or '(no diff)'}"
        )
        resp = await self._brain.query(BrainRequest(user_utterance=prompt))
        return SpecialistResponse(name="plan-reviewer", summary=resp.text)
```

```python
# daemon/src/reachy_ducky_daemon/specialists/__init__.py
from .plan_reviewer import PlanReviewer

__all__ = ["PlanReviewer"]
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_specialist_plan_reviewer.py -v`
Expected: 1 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add plan-reviewer specialist (first subagent)"
```

---

## Milestone 6 — Daemon: FastAPI HTTP server

### Task 6.1: `/health` endpoint

**Files:**
- Create: `daemon/src/reachy_ducky_daemon/server.py`
- Create: `daemon/src/reachy_ducky_daemon/config.py`
- Test: `daemon/tests/test_server_health.py`

**Step 1: Write the failing test**

```python
# daemon/tests/test_server_health.py
from fastapi.testclient import TestClient

from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.server import create_app


def test_health_ok(tmp_path):
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["brain"] == "MockBrain"
    assert data["memory_ready"] is True
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_server_health.py -v`
Expected: FAIL.

**Step 3: Implement config + server**

```python
# daemon/src/reachy_ducky_daemon/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    memory_root: Path
    host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_env(cls) -> "Config":
        root = Path(os.environ.get(
            "REACHY_DUCKY_MEMORY_ROOT",
            str(Path.home() / ".reachy-ducky" / "memory"),
        ))
        return cls(memory_root=root)
```

```python
# daemon/src/reachy_ducky_daemon/server.py
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from reachy_ducky_protocol.messages import HealthResponse

from .brain.interface import BrainInterface
from .config import Config
from .memory.layout import ensure_layout


def create_app(*, brain: BrainInterface, memory_root: Path) -> FastAPI:
    ensure_layout(memory_root)
    app = FastAPI(title="reachy-ducky-daemon")

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            brain=type(brain).__name__,
            memory_ready=(memory_root / "ducky" / "soul.md").exists(),
        )

    return app


def main() -> None:
    import uvicorn

    from .brain.claude_sdk import ClaudeSDKBrain

    cfg = Config.from_env()
    app = create_app(brain=ClaudeSDKBrain(), memory_root=cfg.memory_root)
    uvicorn.run(app, host=cfg.host, port=cfg.port)
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_server_health.py -v`
Expected: 1 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add FastAPI server with /health endpoint"
```

---

### Task 6.2: `/brain/query` endpoint

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/server.py`
- Test: `daemon/tests/test_server_brain_query.py`

**Step 1: Write the failing test**

```python
# daemon/tests/test_server_brain_query.py
from fastapi.testclient import TestClient

from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.server import create_app


def test_brain_query_round_trip(tmp_path):
    app = create_app(brain=MockBrain(), memory_root=tmp_path)
    client = TestClient(app)
    r = client.post("/brain/query", json={"user_utterance": "hello"})
    assert r.status_code == 200
    assert r.json()["text"] == "[mock] hello"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_server_brain_query.py -v`
Expected: FAIL (404).

**Step 3: Extend the server**

Add to `server.py` inside `create_app`:

```python
    from reachy_ducky_protocol.messages import BrainRequest, BrainResponse

    @app.post("/brain/query", response_model=BrainResponse)
    async def brain_query(req: BrainRequest) -> BrainResponse:
        return await brain.query(req)
```

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_server_brain_query.py -v`
Expected: 1 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add /brain/query endpoint"
```

---

### Task 6.3: `/specialists/plan-reviewer` endpoint

**Files:**
- Modify: `daemon/src/reachy_ducky_daemon/server.py`
- Test: `daemon/tests/test_server_specialists.py`

**Step 1: Write the failing test** (uses the same tmp-repo fixture pattern as Task 5.1)

```python
# daemon/tests/test_server_specialists.py
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from reachy_ducky_daemon.brain.mock import MockBrain
from reachy_ducky_daemon.server import create_app


def _init_repo(root: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "p.md").write_text("# Plan\nAdd feature X\n")
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True)
    return root


def test_plan_reviewer_endpoint(tmp_path: Path):
    mem = tmp_path / "mem"
    repo = _init_repo(tmp_path / "repo")
    app = create_app(
        brain=MockBrain(),
        memory_root=mem,
        repo_roots={"repo": repo},
    )
    client = TestClient(app)
    r = client.post("/specialists/plan-reviewer", json={
        "name": "plan-reviewer",
        "project_slug": "repo",
    })
    assert r.status_code == 200
    assert r.json()["name"] == "plan-reviewer"
    assert "mock" in r.json()["summary"].lower()
```

**Step 2: Run to verify it fails**

Run: `uv run pytest daemon/tests/test_server_specialists.py -v`
Expected: FAIL.

**Step 3: Extend `create_app` signature + endpoint**

In `server.py`, change `create_app` to accept `repo_roots: dict[str, Path]` and add:

```python
    from reachy_ducky_protocol.messages import SpecialistRequest, SpecialistResponse

    from .specialists.plan_reviewer import PlanReviewer

    @app.post("/specialists/plan-reviewer", response_model=SpecialistResponse)
    async def plan_reviewer(req: SpecialistRequest) -> SpecialistResponse:
        repo = repo_roots.get(req.project_slug)
        if repo is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"unknown project: {req.project_slug}")
        return await PlanReviewer(brain=brain, repo=repo).review()
```

Add `repo_roots: dict[str, Path] | None = None` to `create_app`; default to `{}`.

**Step 4: Run to verify pass**

Run: `uv run pytest daemon/tests/test_server_specialists.py -v`
Expected: 1 PASS.

**Step 5: Commit**

```bash
git add daemon/
git commit -m "feat(daemon): add /specialists/plan-reviewer endpoint"
```

---

## Milestone 7 — Menu-bar app (Mac)

The menu-bar app is what replaces the missing LED-eye channel in Phase A.

### Task 7.1: Rumps menu-bar scaffold

**Files:**
- Create: `menubar/src/reachy_ducky_menubar/main.py`
- Create: `menubar/src/reachy_ducky_menubar/state_icon.py`
- Test: `menubar/tests/test_state_icon.py`

**Step 1: Write a small pure-logic test**

```python
# menubar/tests/test_state_icon.py
from reachy_ducky_menubar.state_icon import icon_for

from reachy_ducky_protocol.messages import State


def test_icon_idle():
    assert icon_for(State.IDLE) == "🦆"


def test_icon_listening():
    assert icon_for(State.LISTENING) == "🦆👂"


def test_icon_thinking():
    assert icon_for(State.THINKING) == "🦆💭"


def test_icon_muted():
    assert icon_for(State.MUTED) == "🦆🔇"
```

**Step 2: Run to verify it fails**

Run: `uv run pytest menubar/tests/test_state_icon.py -v`
Expected: FAIL.

**Step 3: Implement pure helpers**

```python
# menubar/src/reachy_ducky_menubar/state_icon.py
from reachy_ducky_protocol.messages import State

_MAP = {
    State.IDLE: "🦆",
    State.LISTENING: "🦆👂",
    State.THINKING: "🦆💭",
    State.MUTED: "🦆🔇",
}


def icon_for(state: State) -> str:
    return _MAP[state]
```

```python
# menubar/src/reachy_ducky_menubar/main.py
from __future__ import annotations

import threading
import time

import httpx
import rumps

from reachy_ducky_protocol.messages import State

from .state_icon import icon_for

DAEMON_URL = "http://127.0.0.1:8765"


class DuckyMenubar(rumps.App):
    def __init__(self) -> None:
        super().__init__(icon_for(State.IDLE), quit_button="Quit")
        self._state = State.IDLE
        self._mute_item = rumps.MenuItem("Mute", callback=self._toggle_mute)
        self._status_item = rumps.MenuItem("Status: idle")
        self.menu = [self._status_item, self._mute_item]
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _set_state(self, s: State) -> None:
        self._state = s
        self.title = icon_for(s)
        self._status_item.title = f"Status: {s.value}"

    def _toggle_mute(self, _item: rumps.MenuItem) -> None:
        if self._state == State.MUTED:
            self._set_state(State.IDLE)
            self._mute_item.state = 0
        else:
            self._set_state(State.MUTED)
            self._mute_item.state = 1

    def _poll_loop(self) -> None:
        while True:
            try:
                r = httpx.get(f"{DAEMON_URL}/health", timeout=1.0)
                if not r.is_success:
                    self._status_item.title = "Status: daemon unreachable"
            except httpx.HTTPError:
                self._status_item.title = "Status: daemon down"
            time.sleep(2.0)


def main() -> None:
    DuckyMenubar().run()


if __name__ == "__main__":
    main()
```

**Step 4: Run to verify pass**

Run: `uv run pytest menubar/tests/test_state_icon.py -v`
Expected: 4 PASS.

**Step 5: Manual smoke**

Run the daemon in one terminal: `uv run reachy-ducky-daemon` (you'll need `claude login` or the brain import may fail — for smoke, temporarily swap in `MockBrain` by editing `server.main`).
Run the menu bar: `uv run reachy-ducky-menubar`
Expected: 🦆 appears in the macOS menu bar; "Status: idle" updates. Click Mute → 🦆🔇.

**Step 6: Commit**

```bash
git add menubar/
git commit -m "feat(menubar): add rumps menu-bar app with state icon and mute toggle"
```

---

## Milestone 8 — App: VoiceInterface

### Task 8.1: VoiceInterface abstract + mock

**Files:**
- Create: `app/src/reachy_ducky_app/voice/__init__.py`
- Create: `app/src/reachy_ducky_app/voice/interface.py`
- Create: `app/src/reachy_ducky_app/voice/mock.py`
- Test: `app/tests/test_voice_interface.py`

The interface centers on a **session** — a conversational turn that streams audio in and produces audio + text out, with barge-in support.

**Step 1: Write the failing test**

```python
# app/tests/test_voice_interface.py
import pytest

from reachy_ducky_app.voice.interface import VoiceInterface, VoiceTurn
from reachy_ducky_app.voice.mock import MockVoice


def test_voice_interface_is_abstract():
    with pytest.raises(TypeError):
        VoiceInterface()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_mock_voice_turn_round_trip():
    voice = MockVoice(scripted_user_text="what's on my branch?", scripted_reply_text="you're on main")
    turn = await voice.start_turn()
    assert isinstance(turn, VoiceTurn)
    user_text = await turn.get_user_text()
    assert user_text == "what's on my branch?"
    await turn.speak_text("you're on main")
    spoken = turn.spoken_texts
    assert spoken == ["you're on main"]


@pytest.mark.asyncio
async def test_mock_voice_interrupt():
    voice = MockVoice(scripted_user_text="hi", scripted_reply_text="hello")
    turn = await voice.start_turn()
    await turn.speak_text("long preamble")
    await turn.interrupt()
    assert turn.interrupted is True
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_voice_interface.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# app/src/reachy_ducky_app/voice/interface.py
from abc import ABC, abstractmethod


class VoiceTurn(ABC):
    @abstractmethod
    async def get_user_text(self) -> str: ...

    @abstractmethod
    async def speak_text(self, text: str) -> None: ...

    @abstractmethod
    async def interrupt(self) -> None: ...


class VoiceInterface(ABC):
    @abstractmethod
    async def start_turn(self) -> VoiceTurn: ...
```

```python
# app/src/reachy_ducky_app/voice/mock.py
from .interface import VoiceInterface, VoiceTurn


class MockVoiceTurn(VoiceTurn):
    def __init__(self, user_text: str) -> None:
        self._user_text = user_text
        self.spoken_texts: list[str] = []
        self.interrupted = False

    async def get_user_text(self) -> str:
        return self._user_text

    async def speak_text(self, text: str) -> None:
        self.spoken_texts.append(text)

    async def interrupt(self) -> None:
        self.interrupted = True


class MockVoice(VoiceInterface):
    def __init__(self, *, scripted_user_text: str, scripted_reply_text: str = "") -> None:
        self._user_text = scripted_user_text
        self._reply = scripted_reply_text

    async def start_turn(self) -> VoiceTurn:
        return MockVoiceTurn(self._user_text)
```

```python
# app/src/reachy_ducky_app/voice/__init__.py
from .interface import VoiceInterface, VoiceTurn
from .mock import MockVoice

__all__ = ["MockVoice", "VoiceInterface", "VoiceTurn"]
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_voice_interface.py -v`
Expected: 3 PASS.

**Step 5: Commit**

```bash
git add app/
git commit -m "feat(app): add VoiceInterface ABC and MockVoice test double"
```

---

### Task 8.2: OpenAI Realtime + `fastrtc` voice implementation

**Files:**
- Create: `app/src/reachy_ducky_app/voice/openai_realtime.py`
- Test: `app/tests/test_voice_openai_realtime_integration.py` (integration, gated)

Because the Realtime API is stateful and streams bidirectional audio, the pure-unit-test surface is narrow. Ship the module with a tagged integration test that requires `OPENAI_API_KEY`, and rely on the interface contract (from Task 8.1) for unit coverage.

**Step 1: Implement the real backend**

```python
# app/src/reachy_ducky_app/voice/openai_realtime.py
from __future__ import annotations

import asyncio
import os

from .interface import VoiceInterface, VoiceTurn


class OpenAIRealtimeVoiceTurn(VoiceTurn):
    """
    Wraps an active realtime session. Implementation uses the `openai` Python SDK's
    realtime client; we adapt it to the `VoiceTurn` contract.

    NOTE: the OpenAI Realtime SDK evolves — revisit after pinning version 1.50+.
    """

    def __init__(self, session) -> None:  # noqa: ANN001 — SDK-specific type
        self._session = session
        self._user_text_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._interrupted = False

    async def get_user_text(self) -> str:
        # Consume events from the session until a final user transcript is emitted.
        async for event in self._session.events():
            etype = getattr(event, "type", None) or event.get("type")
            if etype == "conversation.item.input_audio_transcription.completed":
                text = getattr(event, "transcript", None) or event.get("transcript", "")
                if not self._user_text_future.done():
                    self._user_text_future.set_result(text)
                break
        return await self._user_text_future

    async def speak_text(self, text: str) -> None:
        await self._session.response.create({
            "modalities": ["audio", "text"],
            "instructions": text,
        })

    async def interrupt(self) -> None:
        self._interrupted = True
        await self._session.response.cancel()


class OpenAIRealtimeVoice(VoiceInterface):
    def __init__(self, model: str = "gpt-realtime") -> None:
        self._model = model
        self._api_key = os.environ["OPENAI_API_KEY"]

    async def start_turn(self) -> VoiceTurn:
        from openai import AsyncOpenAI  # imported lazily to keep unit tests light

        client = AsyncOpenAI(api_key=self._api_key)
        session = await client.beta.realtime.sessions.create(model=self._model)
        return OpenAIRealtimeVoiceTurn(session)
```

> **Note to engineer:** the `openai` SDK's realtime-session API has evolved repeatedly; treat the method names above as structural, not verbatim. Pin `openai>=1.50`, then adjust `speak_text` / `get_user_text` to match the version you land on. The contract (return `str`, call `speak_text`) is stable.

**Step 2: Write the integration test** (gated; run only when credentials exist)

```python
# app/tests/test_voice_openai_realtime_integration.py
import os

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_realtime_smoke():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("no OPENAI_API_KEY")
    from reachy_ducky_app.voice.openai_realtime import OpenAIRealtimeVoice

    voice = OpenAIRealtimeVoice()
    turn = await voice.start_turn()
    # Without a live mic, we can't feed audio. At minimum, verify construction succeeds.
    assert turn is not None
```

**Step 3: Run the integration test**

Run: `OPENAI_API_KEY=$OPENAI_API_KEY uv run pytest app/tests/test_voice_openai_realtime_integration.py -v -m integration`
Expected: PASS (or skip if no key).

**Step 4: Commit**

```bash
git add app/
git commit -m "feat(app): add OpenAI Realtime voice implementation behind VoiceInterface"
```

---

### Task 8.3: Wake-word detector

**Files:**
- Create: `app/src/reachy_ducky_app/wake.py`
- Test: `app/tests/test_wake.py`

**Strategy:** consume a community wake-word ONNX model from a Hugging Face Space (e.g., `luisomoreau/hey_reachy_wake_word_detection`). Wrap it behind a `WakeDetector` contract with a `MockWakeDetector` for tests. Runtime loading lives in a single `load_default_wake_detector` factory.

**Step 1: Write the failing test**

```python
# app/tests/test_wake.py
import pytest

from reachy_ducky_app.wake import MockWakeDetector, WakeDetector


def test_wake_detector_is_abstract():
    with pytest.raises(TypeError):
        WakeDetector()  # type: ignore[abstract]


def test_mock_detector_triggers_on_keyword():
    det = MockWakeDetector(trigger_on="hey ducky")
    assert det.detect_in_text("hey ducky, what's up?") is True
    assert det.detect_in_text("just typing") is False
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_wake.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# app/src/reachy_ducky_app/wake.py
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class WakeDetector(ABC):
    @abstractmethod
    def feed_audio(self, audio_chunk: np.ndarray) -> bool:
        """Returns True when wake word is detected in the chunk."""


class MockWakeDetector(WakeDetector):
    def __init__(self, trigger_on: str = "hey ducky") -> None:
        self._trigger = trigger_on.lower()

    def feed_audio(self, audio_chunk: np.ndarray) -> bool:
        return False

    def detect_in_text(self, text: str) -> bool:
        return self._trigger in text.lower()


def load_default_wake_detector() -> WakeDetector:
    """
    Returns the community wake-word detector when available; falls back to a mock.
    Phase A: implement as mock; swap the body out for an ONNX loader when model is ready.
    """
    # TODO(phase-A+): hook `luisomoreau/hey_reachy_wake_word_detection` or equivalent.
    return MockWakeDetector()
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_wake.py -v`
Expected: 2 PASS.

**Step 5: Commit**

```bash
git add app/
git commit -m "feat(app): add WakeDetector interface with mock; community model swap pending"
```

---

### Task 8.4: Hard-mute audio gate

**Files:**
- Create: `app/src/reachy_ducky_app/mute.py`
- Test: `app/tests/test_mute.py`

**Step 1: Write the failing test**

```python
# app/tests/test_mute.py
import numpy as np

from reachy_ducky_app.mute import MuteGate


def test_gate_passes_audio_when_unmuted():
    gate = MuteGate()
    chunk = np.ones(16, dtype=np.int16)
    assert (gate.process(chunk) == chunk).all()


def test_gate_zeros_audio_when_muted():
    gate = MuteGate()
    gate.set_muted(True)
    chunk = np.ones(16, dtype=np.int16)
    out = gate.process(chunk)
    assert (out == 0).all()


def test_gate_toggle():
    gate = MuteGate()
    gate.toggle()
    assert gate.muted is True
    gate.toggle()
    assert gate.muted is False
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_mute.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# app/src/reachy_ducky_app/mute.py
from __future__ import annotations

import numpy as np


class MuteGate:
    def __init__(self) -> None:
        self._muted = False

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, value: bool) -> None:
        self._muted = value

    def toggle(self) -> None:
        self._muted = not self._muted

    def process(self, chunk: np.ndarray) -> np.ndarray:
        if self._muted:
            return np.zeros_like(chunk)
        return chunk
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_mute.py -v`
Expected: 3 PASS.

**Step 5: Commit**

```bash
git add app/
git commit -m "feat(app): add hard MuteGate for local mic gating"
```

---

## Milestone 9 — App: Embodiment state machine

### Task 9.1: State machine + motion driver interface

**Files:**
- Create: `app/src/reachy_ducky_app/embodiment/__init__.py`
- Create: `app/src/reachy_ducky_app/embodiment/state_machine.py`
- Create: `app/src/reachy_ducky_app/embodiment/motion_driver.py`
- Test: `app/tests/test_embodiment_state_machine.py`

We isolate the SDK calls behind a `MotionDriver` interface so the state machine is fully testable without hardware.

**Step 1: Write the failing test**

```python
# app/tests/test_embodiment_state_machine.py
from reachy_ducky_app.embodiment.motion_driver import MockMotionDriver
from reachy_ducky_app.embodiment.state_machine import EmbodimentStateMachine

from reachy_ducky_protocol.messages import State


def test_transition_to_listening_plays_move():
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    sm.transition(State.LISTENING)
    assert driver.moves == ["listening"]
    assert sm.state == State.LISTENING


def test_transition_to_thinking_plays_move():
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    sm.transition(State.THINKING)
    assert driver.moves == ["thinking"]


def test_transition_to_muted_goes_to_sleep():
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    sm.transition(State.MUTED)
    assert driver.went_to_sleep is True


def test_transition_same_state_is_noop():
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    sm.transition(State.LISTENING)
    sm.transition(State.LISTENING)
    assert driver.moves == ["listening"]
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_embodiment_state_machine.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# app/src/reachy_ducky_app/embodiment/motion_driver.py
from __future__ import annotations

from abc import ABC, abstractmethod


class MotionDriver(ABC):
    @abstractmethod
    def play_move(self, name: str) -> None: ...
    @abstractmethod
    def go_to_sleep(self) -> None: ...
    @abstractmethod
    def wake_up(self) -> None: ...
    @abstractmethod
    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None: ...


class MockMotionDriver(MotionDriver):
    def __init__(self) -> None:
        self.moves: list[str] = []
        self.went_to_sleep = False
        self.woke_up = False
        self.gazes: list[tuple[float, float]] = []

    def play_move(self, name: str) -> None:
        self.moves.append(name)

    def go_to_sleep(self) -> None:
        self.went_to_sleep = True

    def wake_up(self) -> None:
        self.woke_up = True

    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
        self.gazes.append((u, v))


class ReachyMotionDriver(MotionDriver):
    """Real driver that talks to the Reachy Mini SDK. Hardware-only."""

    def __init__(self, mini) -> None:  # noqa: ANN001 — SDK type
        self._mini = mini

    def play_move(self, name: str) -> None:
        # SDK plays from the emotion library by name via `play_move`.
        self._mini.play_move(name)

    def go_to_sleep(self) -> None:
        self._mini.goto_sleep()

    def wake_up(self) -> None:
        self._mini.wake_up()

    def look_at_image(self, u: float, v: float, duration: float = 0.3) -> None:
        self._mini.look_at_image(u, v, duration=duration)
```

```python
# app/src/reachy_ducky_app/embodiment/state_machine.py
from __future__ import annotations

from reachy_ducky_protocol.messages import State

from .motion_driver import MotionDriver

_STATE_TO_MOVE = {
    State.LISTENING: "listening",
    State.THINKING: "thinking",
    State.IDLE: "neutral",
}


class EmbodimentStateMachine:
    def __init__(self, driver: MotionDriver) -> None:
        self._driver = driver
        self._state = State.IDLE

    @property
    def state(self) -> State:
        return self._state

    def transition(self, target: State) -> None:
        if target == self._state:
            return
        if target == State.MUTED:
            self._driver.go_to_sleep()
        else:
            move = _STATE_TO_MOVE.get(target)
            if move:
                self._driver.play_move(move)
        self._state = target
```

```python
# app/src/reachy_ducky_app/embodiment/__init__.py
from .motion_driver import MockMotionDriver, MotionDriver, ReachyMotionDriver
from .state_machine import EmbodimentStateMachine

__all__ = ["EmbodimentStateMachine", "MockMotionDriver", "MotionDriver", "ReachyMotionDriver"]
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_embodiment_state_machine.py -v`
Expected: 4 PASS.

**Step 5: Commit**

```bash
git add app/
git commit -m "feat(app): add embodiment state machine with MotionDriver abstraction"
```

---

### Task 9.2: Face-tracking gaze (DIY via MediaPipe + `look_at_image`)

**Files:**
- Create: `app/src/reachy_ducky_app/embodiment/gaze.py`
- Test: `app/tests/test_gaze.py`

**Step 1: Write the failing test**

```python
# app/tests/test_gaze.py
from reachy_ducky_app.embodiment.gaze import pick_primary_face


def test_pick_primary_face_closest_to_center():
    # Synthetic face detections: (u, v, confidence)
    detections = [
        (0.1, 0.5, 0.9),  # far left
        (0.5, 0.5, 0.9),  # center
        (0.8, 0.4, 0.95), # far right, highest confidence
    ]
    # Strategy: choose highest-confidence face (ties broken by closeness to center)
    u, v = pick_primary_face(detections)
    assert (u, v) == (0.8, 0.4)


def test_pick_primary_face_empty():
    assert pick_primary_face([]) is None
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_gaze.py -v`
Expected: FAIL.

**Step 3: Implement pure selection logic**

```python
# app/src/reachy_ducky_app/embodiment/gaze.py
from __future__ import annotations


def pick_primary_face(detections: list[tuple[float, float, float]]) -> tuple[float, float] | None:
    """
    detections: list of (u, v, confidence) where u,v are image-normalized [0,1].
    Returns (u, v) of the highest-confidence face, or None.
    """
    if not detections:
        return None
    best = max(detections, key=lambda d: d[2])
    return (best[0], best[1])
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_gaze.py -v`
Expected: 2 PASS.

**Step 5: Add a hardware-only face-tracking loop (not test-gated, but marked hardware)**

```python
# app/src/reachy_ducky_app/embodiment/gaze.py (append)
import asyncio


async def gaze_loop(mini, driver, *, fps: float = 5.0) -> None:  # noqa: ANN001
    """Hardware-only: runs until cancelled. Uses MediaPipe via mini.media.get_frame()."""
    import mediapipe as mp  # type: ignore[import-not-found]

    detector = mp.solutions.face_detection.FaceDetection(min_detection_confidence=0.5)
    period = 1.0 / fps
    try:
        while True:
            frame = mini.media.get_frame()
            if frame is not None:
                results = detector.process(frame)
                dets: list[tuple[float, float, float]] = []
                for d in (results.detections or []):
                    bb = d.location_data.relative_bounding_box
                    u = bb.xmin + bb.width / 2
                    v = bb.ymin + bb.height / 2
                    dets.append((u, v, d.score[0] if d.score else 0.0))
                primary = pick_primary_face(dets)
                if primary is not None:
                    h, w = frame.shape[:2]
                    driver.look_at_image(primary[0] * w, primary[1] * h)
            await asyncio.sleep(period)
    finally:
        detector.close()
```

**Step 6: Commit**

```bash
git add app/
git commit -m "feat(app): add pick_primary_face + hardware gaze loop"
```

---

## Milestone 10 — App ↔ Daemon wiring

### Task 10.1: Daemon HTTP client

**Files:**
- Create: `app/src/reachy_ducky_app/daemon_client.py`
- Test: `app/tests/test_daemon_client.py`

**Step 1: Write the failing test** (uses `pytest-httpx` — add to app dev deps)

Update `app/pyproject.toml` `[project.optional-dependencies]`:
```toml
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-httpx>=0.30", "ruff>=0.6"]
```

Re-sync: `uv sync --all-packages`.

```python
# app/tests/test_daemon_client.py
import pytest

from reachy_ducky_app.daemon_client import DaemonClient


@pytest.mark.asyncio
async def test_brain_query_posts_and_parses(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url="http://127.0.0.1:8765/brain/query",
        json={"text": "hello", "specialist_invoked": None},
    )
    client = DaemonClient()
    resp = await client.brain_query("hi")
    assert resp.text == "hello"


@pytest.mark.asyncio
async def test_health(httpx_mock):
    httpx_mock.add_response(
        url="http://127.0.0.1:8765/health",
        json={"ok": True, "brain": "MockBrain", "memory_ready": True},
    )
    client = DaemonClient()
    h = await client.health()
    assert h.ok is True
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_daemon_client.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# app/src/reachy_ducky_app/daemon_client.py
from __future__ import annotations

import httpx

from reachy_ducky_protocol.messages import BrainRequest, BrainResponse, HealthResponse


class DaemonClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765") -> None:
        self._base = base_url

    async def brain_query(self, text: str, project_slug: str | None = None) -> BrainResponse:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.post(
                f"{self._base}/brain/query",
                json=BrainRequest(user_utterance=text, project_slug=project_slug).model_dump(),
            )
            r.raise_for_status()
            return BrainResponse.model_validate(r.json())

    async def health(self) -> HealthResponse:
        async with httpx.AsyncClient(timeout=2.0) as http:
            r = await http.get(f"{self._base}/health")
            r.raise_for_status()
            return HealthResponse.model_validate(r.json())
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_daemon_client.py -v`
Expected: 2 PASS.

**Step 5: Commit**

```bash
git add app/
git commit -m "feat(app): add DaemonClient for HTTP IPC to the Mac daemon"
```

---

### Task 10.2: Conversation loop wiring

**Files:**
- Create: `app/src/reachy_ducky_app/conversation.py`
- Test: `app/tests/test_conversation.py`

Wires: wake → mute gate → voice turn → daemon brain → voice reply, with embodiment transitions.

**Step 1: Write the failing test**

```python
# app/tests/test_conversation.py
from unittest.mock import AsyncMock

import pytest

from reachy_ducky_app.conversation import run_one_turn
from reachy_ducky_app.embodiment.motion_driver import MockMotionDriver
from reachy_ducky_app.embodiment.state_machine import EmbodimentStateMachine
from reachy_ducky_app.voice.mock import MockVoice

from reachy_ducky_protocol.messages import BrainResponse, State


@pytest.mark.asyncio
async def test_run_one_turn_full_flow():
    voice = MockVoice(scripted_user_text="what's on my branch?", scripted_reply_text="")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)

    client = AsyncMock()
    client.brain_query.return_value = BrainResponse(text="you're on main")

    await run_one_turn(voice=voice, sm=sm, daemon=client, project_slug="demo")

    assert driver.moves[0] == "listening"
    assert "thinking" in driver.moves
    client.brain_query.assert_awaited_once()
    assert sm.state == State.IDLE


@pytest.mark.asyncio
async def test_run_one_turn_respects_muted():
    voice = MockVoice(scripted_user_text="should not fire")
    driver = MockMotionDriver()
    sm = EmbodimentStateMachine(driver=driver)
    sm.transition(State.MUTED)

    client = AsyncMock()

    await run_one_turn(voice=voice, sm=sm, daemon=client, project_slug="demo")
    client.brain_query.assert_not_awaited()
```

**Step 2: Run to verify it fails**

Run: `uv run pytest app/tests/test_conversation.py -v`
Expected: FAIL.

**Step 3: Implement**

```python
# app/src/reachy_ducky_app/conversation.py
from __future__ import annotations

from reachy_ducky_protocol.messages import State

from .daemon_client import DaemonClient
from .embodiment.state_machine import EmbodimentStateMachine
from .voice.interface import VoiceInterface


async def run_one_turn(
    *,
    voice: VoiceInterface,
    sm: EmbodimentStateMachine,
    daemon: DaemonClient,
    project_slug: str | None = None,
) -> None:
    if sm.state == State.MUTED:
        return

    sm.transition(State.LISTENING)
    turn = await voice.start_turn()
    user_text = await turn.get_user_text()

    sm.transition(State.THINKING)
    reply = await daemon.brain_query(user_text, project_slug=project_slug)

    sm.transition(State.LISTENING)
    await turn.speak_text(reply.text)

    sm.transition(State.IDLE)
```

**Step 4: Run to verify pass**

Run: `uv run pytest app/tests/test_conversation.py -v`
Expected: 2 PASS.

**Step 5: Commit**

```bash
git add app/
git commit -m "feat(app): add single-turn conversation loop wiring voice → daemon → voice"
```

---

## Milestone 11 — Reachy app entry point + packaging

### Task 11.1: Reachy app main (subclasses `ReachyMiniApp`)

**Files:**
- Create: `app/src/reachy_ducky_app/main.py`

This is hardware-only. Use `reachy-mini-app-assistant create` conventions: subclass `ReachyMiniApp`, implement `run(reachy_mini, stop_event)`.

**Step 1: Implement**

```python
# app/src/reachy_ducky_app/main.py
from __future__ import annotations

import asyncio
import threading

from reachy_mini_app import ReachyMiniApp  # type: ignore[import-not-found]

from .conversation import run_one_turn
from .daemon_client import DaemonClient
from .embodiment.motion_driver import ReachyMotionDriver
from .embodiment.state_machine import EmbodimentStateMachine
from .voice.openai_realtime import OpenAIRealtimeVoice
from .wake import load_default_wake_detector


class ReachyDuckyApp(ReachyMiniApp):
    def run(self, reachy_mini, stop_event: threading.Event) -> None:  # noqa: ANN001
        asyncio.run(self._run_async(reachy_mini, stop_event))

    async def _run_async(self, reachy_mini, stop_event: threading.Event) -> None:  # noqa: ANN001
        driver = ReachyMotionDriver(reachy_mini)
        sm = EmbodimentStateMachine(driver=driver)
        voice = OpenAIRealtimeVoice()
        daemon = DaemonClient(base_url="http://mac.local:8765")
        wake = load_default_wake_detector()

        while not stop_event.is_set():
            # Phase A: simplified — wait for a wake signal, run one conversational turn.
            # `load_default_wake_detector` is a mock for now; replace with a real audio loop
            # that calls `wake.feed_audio(chunk)` on each mic buffer and triggers on True.
            if self._wake_triggered(wake):
                await run_one_turn(voice=voice, sm=sm, daemon=daemon, project_slug="default")
            await asyncio.sleep(0.05)

    def _wake_triggered(self, wake) -> bool:  # noqa: ANN001
        # Placeholder until a real audio-pump loop is implemented.
        return False
```

**Step 2: Manual smoke (on Reachy hardware — cannot be automated in plan)**

- Power on Reachy Mini Wireless
- Install the app: `pip install -e app/`
- Run via the Reachy daemon dashboard at `127.0.0.1:8000`
- Confirm the app appears, loads without error, logs "started" (even though the wake detector is stubbed)

**Step 3: Commit**

```bash
git add app/
git commit -m "feat(app): add ReachyDuckyApp main entry point (hardware stub)"
```

---

### Task 11.2: Reachy Hugging Face Space metadata

**Files:**
- Create: `app/README.md`
- Create: `app/reachy_mini_app.yaml`

Follow `reachy-mini-app-assistant` conventions: an app-level `README.md` + the `reachy_mini_app.yaml` descriptor for HF Space publishing.

**Step 1: Write `app/reachy_mini_app.yaml`**

```yaml
title: reachy-ducky
emoji: 🦆
python_version: "3.12"
app_class: reachy_ducky_app.main.ReachyDuckyApp
description: >
  A read-only rubber-ducky development companion: watches your agentic SWE
  workflow (via a Mac daemon), answers questions in conversation, and
  flags concerns during SDD flows.
```

**Step 2: Write `app/README.md`**

```markdown
# Reachy Ducky — Reachy Mini App

The Reachy Mini side of [reachy-ducky](https://github.com/Obsidian-Owl/reachy-ducky).

## Install

1. Install the Mac-side daemon: `pip install reachy-ducky-daemon` and run `reachy-ducky-daemon`.
2. From the on-robot dashboard at `http://<robot>:8000`, one-click install this app from its HF Space URL.
3. Set `DAEMON_URL=http://<your-mac>.local:8765` in the app's environment.

## What it does

On-demand conversational mode only (Phase A). Say "Hey Ducky", ask a question, get an answer.
Full design: https://github.com/Obsidian-Owl/reachy-ducky/blob/main/docs/plans/2026-04-21-reachy-ducky-design.md
```

**Step 3: Commit**

```bash
git add app/
git commit -m "chore(app): add HF Space metadata and README"
```

---

## Milestone 12 — End-to-end smoke

### Task 12.1: Manual end-to-end test procedure

**Files:**
- Create: `docs/testing/2026-04-21-phase-a-e2e-procedure.md`

**Step 1: Write the procedure document**

```markdown
# Phase A End-to-End Smoke Procedure

## Prereqs
- Mac: `claude login` has been run; `uv` installed; `brew install gh` and `gh auth login`.
- Reachy Mini Wireless powered on, on same LAN as the Mac.
- `OPENAI_API_KEY` exported for the Reachy-side process.

## 1. Start the daemon

```bash
cd reachy-ducky
uv run reachy-ducky-daemon
# expect: uvicorn listening on 127.0.0.1:8765
```

Verify: `curl http://127.0.0.1:8765/health` returns `{"ok": true, ...}`.

## 2. Start the menu-bar app

```bash
uv run reachy-ducky-menubar
# 🦆 appears in macOS menu bar
```

## 3. Start the Reachy app on the robot

From the Reachy dashboard (`http://<robot>:8000`), start the `reachy-ducky` app.

## 4. Interact

- Say: "Hey Ducky, what's on my branch?"
- Expect: Ducky plays `listening`, then `thinking` (menu bar shows 🦆💭), daemon logs a `/brain/query`, Ducky speaks a reply.
- Click Mute in the menu bar: Ducky `goto_sleep`s, 🦆🔇 shows. Speaking should produce no response.
- Unmute: Ducky is back to 🦆.

## Failure modes
- "daemon unreachable" in menu bar → daemon not running; start it.
- Silence after wake word → check OpenAI API key on the robot.
- Immediate hang-up after user speech → likely the Realtime session API mismatch; pin `openai` version and revisit.
```

**Step 2: Commit**

```bash
git add docs/testing/
git commit -m "docs: add Phase A end-to-end smoke procedure"
```

---

## Milestone 13 — Wrap-up

### Task 13.1: Update root README with status + run instructions

**Files:**
- Modify: `README.md`

**Step 1: Extend**

Add under "Status":

```markdown
## Run (Phase A)

- Mac daemon: `uv run reachy-ducky-daemon`
- Menu-bar: `uv run reachy-ducky-menubar`
- Reachy app: publish via HF Spaces, install from the on-robot dashboard.

See `docs/testing/2026-04-21-phase-a-e2e-procedure.md` for end-to-end smoke.

## Development

- `uv sync --all-packages` to install all subpackages.
- `uv run pytest` runs all unit tests. Integration tests gated with `-m integration` and env vars.
- Hardware tests gated with `-m hardware` and require a connected Reachy Mini.
```

**Step 2: Commit and push**

```bash
git add README.md
git commit -m "docs: document Phase A run and development instructions"
git push
```

---

## Done when

- `uv run pytest` passes locally (all non-`integration`, non-`hardware` tests).
- The end-to-end smoke (Milestone 12) completes on real hardware: wake word → voice turn → daemon brain response → voice reply, with state-machine motions visible.
- `reachy-ducky-menubar` shows correct state transitions and mute works.
- One successful `plan-reviewer` invocation has been performed against a real repo + plan, either via `curl -X POST /specialists/plan-reviewer` or via conversational prompt routed through the brain.

## Deferred (Phase B onward)

- `fswatch` + git-ref event watcher (→ phase B)
- specialists `test-gap-assessor`, `scope-creep-detector`, `pr-reviewer` (→ phase B)
- Claude Code / Codex hook integration (→ phase C)
- Interruption policy with severity tiers and per-project overrides (→ phase C)
- Transcript ingestion (→ phase D, opt-in)
- Graphiti temporal KG memory layer (deferred; revisit if Basic Memory grep recall falls short)
- ESP32 eye hardware mod (optional, power-user path)
- Face-tracking production loop (currently a hardware-only stub in gaze.py)
- Idle breathing loop (not in MVP)
