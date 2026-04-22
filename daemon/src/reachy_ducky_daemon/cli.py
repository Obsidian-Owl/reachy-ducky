"""Interactive first-run setup for the Reachy Ducky daemon.

``reachy-ducky init`` walks a first-time user through creating
``~/.reachy-ducky/config.toml`` (plus a 0600 ``.env`` for any secrets)
and seeds the memory tree so the daemon starts without hand-editing
anything. The wizard matches the shape :class:`AppConfig.load` reads
back, so ``reachy-ducky-daemon`` Just Works once the wizard finishes.

Secrets never land in the TOML: the GitHub PAT and any daemon auth
token are written to ``~/.reachy-ducky/.env`` with 0600 perms. The TOML
stays readable + diff-able; the .env is the thing to protect.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 — used only for controlled, list-form git/claude CLI calls (not user-shell input)
from dataclasses import dataclass
from pathlib import Path

import typer

from .memory.layout import ensure_layout
from .project import _SLUG_RE as _PROJECT_SLUG_RE

__all__ = ["app", "main"]

app = typer.Typer(
    name="reachy-ducky",
    help="Reachy Ducky CLI — first-run setup and daemon helpers.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Keep typer in multi-command mode even with a single registered command.

    Without an explicit callback, typer collapses a one-command app so
    that ``reachy-ducky init`` parses ``init`` as an argument. A no-op
    callback preserves the ``reachy-ducky <command>`` form users (and
    the README) expect.
    """


# owner/repo — both parts non-empty, no embedded slashes. Keeps the check
# aligned with :class:`Project.__post_init__` but runs pre-construction so
# we can re-prompt instead of trapping a ValueError.
_GITHUB_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def _config_dir() -> Path:
    """Return the config dir — re-computed each call so tests' ``$HOME`` swap works."""
    return Path.home() / ".reachy-ducky"


def _ask(prompt: str, default: str | None = None) -> str:
    """Prompt loop that returns the empty string when the user just hits Enter.

    ``typer.prompt(..., default="")`` insists on showing ``[]`` in the
    rendered prompt which is noisier than we want. A thin wrapper keeps
    the UI tidy and unifies empty-string handling.
    """
    rendered = f"{prompt} [{default}]" if default else prompt
    # typer.prompt is typed as Any in stubs; normalise to str at the boundary.
    raw: str = str(typer.prompt(rendered, default="", show_default=False))
    value = raw.strip()
    if not value and default is not None:
        return default
    return value


def _confirm(prompt: str, *, default: bool = False) -> bool:
    """Yes/no confirmation with an explicit default; parses y/n/yes/no."""
    return typer.confirm(prompt, default=default)


@dataclass
class _DaemonConfig:
    memory_root: Path
    host: str
    port: int
    auth_token: str | None


@dataclass
class _ProjectConfig:
    slug: str
    path: Path
    github_repo: str | None
    primary: bool


def _check_claude_auth() -> None:
    """Warn (never fail) if ``claude auth status`` reports not-logged-in.

    The subprocess is invoked in list form (no shell). If ``claude``
    isn't on PATH, we catch :class:`FileNotFoundError` and degrade to a
    warning — the daemon itself will surface the auth error on first
    query, so this is purely a UX nicety.
    """
    try:
        proc = subprocess.run(  # noqa: S603  # nosec B603 B607 — list form, no shell; "claude" resolved via PATH is the intended portable behaviour
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except FileNotFoundError:
        typer.secho(
            "  (claude CLI not found on PATH — skipping login check; "
            "install it and run `claude login` before starting the daemon.)",
            fg=typer.colors.YELLOW,
        )
        return
    except subprocess.TimeoutExpired:
        typer.secho(
            "  (claude auth status timed out — skipping login check.)",
            fg=typer.colors.YELLOW,
        )
        return

    logged_in = False
    if proc.returncode == 0:
        try:
            payload = json.loads(proc.stdout or "{}")
            logged_in = bool(payload.get("loggedIn"))
        except json.JSONDecodeError:
            # Older CLI versions might print plain text; if returncode==0
            # assume logged in and move on.
            logged_in = True

    if logged_in:
        typer.secho("  claude CLI is logged in.", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "  claude CLI reports not logged in. Run `claude login` before "
            "starting the daemon — it's how ClaudeSDKBrain authenticates.",
            fg=typer.colors.YELLOW,
        )


def _collect_daemon(existing_default_memory: Path) -> _DaemonConfig:
    typer.secho("\nDaemon settings", fg=typer.colors.CYAN, bold=True)
    memory_root_str = _ask(
        "Memory root (where ducky/human/projects live)",
        default=str(existing_default_memory),
    )
    memory_root = Path(memory_root_str).expanduser()

    host = _ask("Daemon bind host", default="127.0.0.1")
    port_str = _ask("Daemon port", default="8765")
    try:
        port = int(port_str)
    except ValueError:
        typer.secho(f"  port {port_str!r} is not a number — using 8765.", fg=typer.colors.YELLOW)
        port = 8765

    auth_token: str | None = None
    raw = typer.prompt(
        "Auth token (leave blank for no auth; safe on loopback)",
        default="",
        show_default=False,
        hide_input=True,
    )
    if raw:
        typer.secho(
            "  Tip: for a strong token, run `openssl rand -hex 32` and paste the output.",
            fg=typer.colors.YELLOW,
        )
        # Default no: the typical answer is "no, keep what I typed". We only
        # prompt at all so the user sees the openssl hint.
        if _confirm("  Regenerate with openssl instead?", default=False):
            typer.echo(
                "  OK — leaving auth_token unset for now. Run `openssl rand -hex 32` "
                "and edit ~/.reachy-ducky/.env before starting the daemon."
            )
        else:
            auth_token = raw

    return _DaemonConfig(
        memory_root=memory_root,
        host=host,
        port=port,
        auth_token=auth_token,
    )


def _collect_github_pat() -> str | None:
    typer.secho("\nGitHub PAT (optional)", fg=typer.colors.CYAN, bold=True)
    typer.echo(
        "Only needed if any project sets github_repo. Scopes: repo:read, "
        "pull_requests:read, issues:read."
    )
    typer.echo("Create one at https://github.com/settings/tokens.")
    pat = typer.prompt(
        "GitHub PAT (leave blank to skip)",
        default="",
        show_default=False,
        hide_input=True,
    ).strip()
    return pat or None


def _prompt_slug(*, taken: frozenset[str] = frozenset()) -> str:
    """Prompt for a project slug, re-prompting until the user enters a valid
    unused slug.

    ``taken`` is the set of slugs already registered by earlier projects in
    the current wizard run. A collision here would otherwise produce a TOML
    file that :class:`BrainRegistry.__init__` rejects with ``ValueError``,
    leaving ``reachy-ducky init`` reporting success while the daemon can't
    start.
    """
    while True:
        slug = _ask("Project slug (lowercase kebab, e.g. 'reachy-ducky')")
        if not slug:
            typer.secho("  slug is required.", fg=typer.colors.YELLOW)
            continue
        # Delegate to Project's own regex so the two paths can't drift.
        if not _PROJECT_SLUG_RE.match(slug):
            typer.secho(
                f"  invalid slug: must match {_PROJECT_SLUG_RE.pattern!r} "
                "(lowercase kebab, starts alphanumeric).",
                fg=typer.colors.YELLOW,
            )
            continue
        if slug in taken:
            typer.secho(
                f"  slug {slug!r} is already used by an earlier project in this "
                "wizard — pick a different one.",
                fg=typer.colors.YELLOW,
            )
            continue
        return slug


def _prompt_project_path() -> Path:
    while True:
        raw = _ask("Project path (absolute, must be a git repo)")
        if not raw:
            typer.secho("  path is required.", fg=typer.colors.YELLOW)
            continue
        resolved = Path(raw).expanduser().resolve()
        if not resolved.exists():
            typer.secho(f"  {resolved} does not exist.", fg=typer.colors.YELLOW)
            continue
        if not (resolved / ".git").exists():
            typer.secho(
                f"  {resolved} is not a git repository — run `git init` first.",
                fg=typer.colors.YELLOW,
            )
            continue
        return resolved


def _prompt_github_repo() -> str | None:
    while True:
        value = _ask("GitHub repo 'owner/name' (blank to skip)")
        if not value:
            return None
        if not _GITHUB_REPO_RE.match(value):
            typer.secho(
                "  invalid shape — expected 'owner/name' "
                "(e.g. 'anthropics/claude-agent-sdk-python').",
                fg=typer.colors.YELLOW,
            )
            continue
        return value


def _collect_projects() -> list[_ProjectConfig]:
    typer.secho("\nProjects", fg=typer.colors.CYAN, bold=True)
    typer.echo("Add at least one project. The 'primary' one is used when a query omits its slug.")
    projects: list[_ProjectConfig] = []
    while True:
        typer.echo(f"\nProject #{len(projects) + 1}:")
        # Slug must be unique across the run — BrainRegistry rejects duplicates
        # at daemon startup, so a duplicate here would write a non-bootable
        # config. Pass the set of already-used slugs so _prompt_slug re-prompts.
        slug = _prompt_slug(taken=frozenset(p.slug for p in projects))
        path = _prompt_project_path()
        github_repo = _prompt_github_repo()
        is_primary = _confirm("  Mark this project as primary?", default=len(projects) == 0)

        if is_primary:
            # Demote any existing primary — only one wins.
            for p in projects:
                p.primary = False

        projects.append(
            _ProjectConfig(
                slug=slug,
                path=path,
                github_repo=github_repo,
                primary=is_primary,
            )
        )

        if not _confirm("Add another project?", default=False):
            break

    # If the user never said "yes" to primary on any project, promote the first.
    if projects and not any(p.primary for p in projects):
        projects[0].primary = True

    return projects


def _toml_quote(s: str) -> str:
    """Quote a string for TOML basic-string syntax.

    Only " and \\ need escaping; everything else renders fine. Paths on
    darwin/linux can't legally contain NUL, so we don't try to handle
    control chars.
    """
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_toml(daemon: _DaemonConfig, projects: list[_ProjectConfig]) -> str:
    """Emit config TOML by hand — simpler than adding a ``tomli_w`` dep.

    The shape is fixed and small (5 scalars + an array of tables), so a
    ~20-line manual writer stays readable and avoids a transitive dep.
    :class:`AppConfig._apply_toml` reads it back with stdlib ``tomllib``.
    """
    out: list[str] = []
    out.append("# reachy-ducky daemon config. Generated by `reachy-ducky init`.")
    out.append("# Secrets (auth tokens, GitHub PATs) live in ~/.reachy-ducky/.env instead.\n")
    out.append("[daemon]")
    out.append(f"host = {_toml_quote(daemon.host)}")
    out.append(f"port = {daemon.port}")
    out.append(f"memory_root = {_toml_quote(str(daemon.memory_root))}")
    # auth_token deliberately not emitted — lives in .env.
    for proj in projects:
        out.append("")
        out.append("[[projects]]")
        out.append(f"slug = {_toml_quote(proj.slug)}")
        out.append(f"path = {_toml_quote(str(proj.path))}")
        if proj.github_repo:
            out.append(f"github_repo = {_toml_quote(proj.github_repo)}")
        if proj.primary:
            out.append("primary = true")
    out.append("")
    return "\n".join(out)


def _write_env(path: Path, entries: dict[str, str]) -> None:
    """Write ``KEY=value`` pairs to ``path`` with 0600 perms."""
    body = "".join(f"{k}={v}\n" for k, v in entries.items())
    path.write_text(body)
    os.chmod(path, 0o600)  # noqa: S103 — 0600 is the REQUIRED tight perm for secrets


@app.command()
def init() -> None:
    """Interactive first-run setup for the Reachy Ducky daemon."""
    cfg_dir = _config_dir()
    cfg_path = cfg_dir / "config.toml"
    env_path = cfg_dir / ".env"

    typer.secho("reachy-ducky init — first-run setup", bold=True)

    if cfg_path.exists():
        if not _confirm(f"\n{cfg_path} already exists. Overwrite?", default=False):
            typer.echo("Keeping existing config. Aborted with no changes.")
            raise typer.Exit(code=0)

    cfg_dir.mkdir(parents=True, exist_ok=True)

    typer.secho("\nChecking claude CLI login status...", fg=typer.colors.CYAN, bold=True)
    _check_claude_auth()

    default_memory = cfg_dir / "memory"
    daemon = _collect_daemon(default_memory)
    pat = _collect_github_pat()
    projects = _collect_projects()

    # --- write outputs ---
    cfg_path.write_text(_render_toml(daemon, projects))

    env_entries: dict[str, str] = {}
    if daemon.auth_token:
        env_entries["REACHY_DUCKY_AUTH_TOKEN"] = daemon.auth_token
    if pat:
        env_entries["GITHUB_PERSONAL_ACCESS_TOKEN"] = pat
    if env_entries:
        _write_env(env_path, env_entries)

    ensure_layout(daemon.memory_root)

    # --- next steps ---
    typer.secho("\nDone.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Config:  {cfg_path}")
    if env_entries:
        typer.echo(f"  Secrets: {env_path} (0600)")
    typer.echo(f"  Memory:  {daemon.memory_root}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  1. Start the daemon: `uv run reachy-ducky-daemon`")
    typer.echo(f"  2. Test it:          `curl http://{daemon.host}:{daemon.port}/health`")
    if env_entries:
        typer.echo(
            f"  3. Source secrets before starting: `set -a; source {env_path}; set +a` "
            "(or use systemd/dotenv)."
        )
    typer.echo("  See README.md for more.")


def main() -> None:
    """Console-script entry point — delegates to the typer app."""
    app()


if __name__ == "__main__":
    main()
