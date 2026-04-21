from __future__ import annotations

import subprocess
from pathlib import Path

ALLOWED = frozenset(
    {
        "log",
        "diff",
        "show",
        "status",
        "branch",
        "rev-parse",
        "ls-files",
        "ls-tree",
        "config",
        "describe",
        "rev-list",
    }
)


class GitTool:
    """Read-only wrapper around the ``git`` CLI scoped to a single repository."""

    def __init__(self, repo: Path) -> None:
        self._repo = Path(repo)

    def run(self, args: list[str]) -> str:
        """Run a git subcommand, rejecting anything not in :data:`ALLOWED`."""
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
        """Return ``git status`` output for the repository."""
        return self.run(["status"])

    def log(self, limit: int = 20) -> str:
        """Return a one-line log capped at ``limit`` commits."""
        return self.run(["log", f"-n{limit}", "--oneline"])

    def diff(self, rev_range: str | None = None) -> str:
        """Return ``git diff`` output, optionally for a specific revision range."""
        return self.run(["diff"] + ([rev_range] if rev_range else []))

    def show(self, ref: str) -> str:
        """Return ``git show`` output for a given ref."""
        return self.run(["show", ref])

    def current_branch(self) -> str:
        """Return the current branch name (abbreviated HEAD)."""
        return self.run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
