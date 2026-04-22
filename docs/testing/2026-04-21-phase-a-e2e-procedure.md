# Phase A End-to-End Smoke Procedure

> This procedure mirrors the tree on `m8-voice` as of 2026-04-22. Re-verify after every merge.

This procedure walks a teammate through bringing up the three Phase A
surfaces (Mac daemon, Mac menu-bar, Reachy Mini app) and exercising the
wire between them. Phase A wake detection and Reachy dashboard install
are NOT yet fully wired — see "Known gaps" below; a full "say 'Hey
Ducky', get a spoken reply" demo is not achievable today without a
code-level override.

## 0. Prereqs

### Accounts / logins

- `claude login` — run on the Mac. `ClaudeSDKBrain` uses the locally
  installed Claude CLI's OAuth credentials via `claude_agent_sdk`.
  (`CLAUDE_CODE_OAUTH_TOKEN` is a CI-only path used by the GitHub
  workflows under `.github/workflows/`; local runs do NOT need it.)
- `gh auth login` — only if you plan to manually inspect GitHub via
  `gh`. The daemon itself reaches GitHub via `github-mcp-server`, not
  `gh`.

### Runtimes on the Mac

- `uv` installed (`brew install uv`).
- Node.js 20+ on `PATH` (`brew install node@20`). Required for
  `npx -y github-mcp-server` which the Pattern B brain spawns
  (declared in `.mcp.json`).

### Env vars (Mac daemon side)

- `GITHUB_PERSONAL_ACCESS_TOKEN` — `repo:read` + `pull_requests:read`
  + `issues:read`. Required for the `mcp__github__*` tool surface when
  a project sets `github_repo`. Read from process env at daemon start
  by `build_brain_options` and forwarded into the MCP subprocess.
- `REACHY_DUCKY_AUTH_TOKEN` — optional bearer token. When set, every
  daemon route except `/health`, `/docs`, and `/openapi.json` requires
  `Authorization: Bearer <token>`. `reachy-ducky init` writes this to
  `~/.reachy-ducky/.env` at 0600 if you supply one during the wizard.

### Env vars (Reachy side)

- `OPENAI_API_KEY` — required. `OpenAIRealtimeVoice.__init__` fails
  fast with `ValueError` if unset.
- `DAEMON_URL` — e.g. `http://<mac>.tailnet.ts.net:8765`. Defaults to
  `http://127.0.0.1:8765`, which is only right if daemon + app share a
  host.
- `DAEMON_AUTH_TOKEN` — must match `REACHY_DUCKY_AUTH_TOKEN` if the
  daemon has one set.

### One-time first-run wizard

```bash
cd reachy-ducky
uv sync --all-packages --group dev
uv run reachy-ducky init
```

The wizard writes `~/.reachy-ducky/config.toml` (bind host/port,
memory root, project list) and a 0600 `~/.reachy-ducky/.env` (auth
token, GitHub PAT). At least one project is required; mark one as
primary.

## 1. Start the daemon

```bash
cd reachy-ducky
# Load secrets written by `reachy-ducky init`:
set -a; source ~/.reachy-ducky/.env; set +a
uv run reachy-ducky-daemon
```

Expected on stdout: `uvicorn` banner listening on the host/port chosen
in the wizard (defaults: `127.0.0.1:8765`). If `host` is off-loopback
and no `auth_token` is set, the daemon logs a loud warning — treat
that as a bug you need to fix before continuing.

### Health check (open route)

```bash
curl -s http://127.0.0.1:8765/health | jq
```

Expected body shape (from `HealthResponse` in
`protocol/src/reachy_ducky_protocol/messages.py`):

```json
{
  "ok": true,
  "brain": "unbuilt",
  "memory_ready": true,
  "projects": ["<your-slug>"]
}
```

`brain` is `"unbuilt"` on a fresh boot (lazy build — no
`ClaudeSDKBrain` has been constructed yet), `"none"` if no primary
project is configured, or the class name (`"ClaudeSDKBrain"`) once a
brain has been materialised by a request.

### Protected-route sanity check (only when `REACHY_DUCKY_AUTH_TOKEN` is set)

```bash
curl -s -X POST http://127.0.0.1:8765/brain/query \
  -H "Authorization: Bearer $REACHY_DUCKY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_utterance":"ping"}' | jq
```

Expected: `200` with `{"text": "...", "specialist_invoked": null}`.
Omitting the header should return `401 {"detail":"missing bearer
token"}`.

If no primary project is configured the daemon returns
`400 {"detail":"no project_slug in request and no primary project
configured"}`; add `"project_slug":"<slug>"` to the body. An unknown
slug returns `404 {"detail":"unknown project: <slug>"}`.

## 2. Start the menu-bar app

macOS only. Install the optional extra first:

```bash
uv sync --all-packages --extra macos
uv run reachy-ducky-menubar
```

Verification (reading `menubar/src/reachy_ducky_menubar/main.py` +
`state_icon.py`):

- Idle glyph: `🦆`.
- Status item reads `Status: idle`.
- Click "Mute" — glyph becomes `🦆🔇`, status reads `Status: muted`,
  menu item shows a check. Click again to unmute.
- Stop the daemon. Within ~2s the glyph changes to `🦆⚠` and status
  reads `Status: daemon unreachable` (or `Status: daemon error
  (<code>)` for a reachable-but-broken daemon). Restart the daemon
  and the glyph returns to `🦆`.

See "Known gaps" §2.

## 3. Start the Reachy app on the robot

Install the robot-side deps (SSH into the robot):

```bash
ssh <robot>
cd reachy-ducky
uv sync --all-packages --extra robot
export OPENAI_API_KEY=sk-...
export DAEMON_URL=http://<mac-host>:8765
export DAEMON_AUTH_TOKEN=<same as REACHY_DUCKY_AUTH_TOKEN on the Mac>
```

Start path: the on-robot Pollen dashboard (`http://<robot>:8000`)
instantiates `ReachyDuckyApp` from `app_class:
reachy_ducky_app.main.ReachyDuckyApp` declared in
`app/reachy_mini_app.yaml`. Publishing to HF Space + one-click install
from the dashboard is the intended flow but is not yet demonstrably
wired end-to-end; see "Known gaps".

## 4. Interact — intended golden path

The orchestrated turn (from `app/src/reachy_ducky_app/conversation.py`
`run_one_turn` + `app/src/reachy_ducky_app/embodiment/state_machine.py`):

1. Wake trigger fires.
2. `sm.transition(LISTENING)` → `play_move("listening")`.
3. `voice.start_turn()` → `turn.get_user_text()` blocks for a final
   transcript event from OpenAI Realtime.
4. `sm.transition(THINKING)` → `play_move("thinking")`.
5. `daemon.brain_query(text)` → Mac daemon's `/brain/query`.
6. `sm.transition(LISTENING)` — the robot looks at the user WHILE
   Ducky speaks (not during thinking).
7. `turn.speak_text(reply)` → OpenAI Realtime TTS over the same
   WebSocket.
8. `sm.transition(IDLE)` → `play_move("neutral")`.

A mute toggle anywhere flips the state machine to `MUTED`, which
calls `driver.go_to_sleep()` (the visible "off" posture). Exiting
`MUTED` calls `driver.wake_up()` before the next `play_move`. The
menu-bar mute is a local UI control; Phase A does NOT propagate
mute from the menu bar to the robot.

Phase A cannot be driven through wake word today — see "Known
gaps" §1.

## Known gaps

### 1. Wake detection is a no-op in Phase A

`app/src/reachy_ducky_app/wake.py` ships `MockWakeDetector` as the
default. `load_default_wake_detector()` returns it, and
`ReachyDuckyApp._wake_triggered()` returns `False` unconditionally.
No audio pump is wired.

Consequence: saying "Hey Ducky" into the robot's mic does nothing.
A turn is only invoked if you subclass `ReachyDuckyApp` and override
`_wake_triggered` (the test suite does this in
`app/tests/test_app_main.py::test_run_async_calls_run_one_turn_when_wake_triggers`).

Workarounds for smoke-verifying the daemon + voice path without wake:

- Run `uv run pytest app -q` on the Mac — the unit suite exercises
  `run_one_turn` end-to-end with `MockVoice` + `MockMotionDriver`
  + `httpx_mock` against a local `DaemonClient`.
- From any shell on the Mac, POST to `/brain/query` directly
  (see §1 "Protected-route sanity check"); that exercises the brain
  and tool surface without the robot in the loop.

### 2. Menu-bar never shows `LISTENING` / `THINKING`

`state_icon.py` defines `🦆👂` and `🦆💭` but the menu-bar's
poll loop (`main.py::_poll_loop`) only writes the local IDLE /
MUTED state. There is no `/state` push from the robot or daemon.

- TODO (follow-up issue): Menu-bar daemon URL should become
  env-overridable; currently hardcoded as `_DAEMON_URL_DEFAULT`
  at `menubar/src/reachy_ducky_menubar/main.py:24`. Note this is
  distinct from the Reachy-side `DAEMON_URL` env var (`daemon_client.py`)
  which the robot process does read — same conceptual value, two
  different wiring stories.

### 3. Reachy dashboard install is unverified

`app/reachy_mini_app.yaml` declares `app_class:
reachy_ducky_app.main.ReachyDuckyApp` and `app/README.md` has the HF
Space frontmatter, but we have NOT yet published to HF Spaces and
one-click-installed on a real robot. The app package exposes no
`reachy_mini_apps` entry-point in `app/pyproject.toml` either — the
YAML `app_class` field is the sole registration mechanism. Treat
step 3 of this procedure as untested until someone walks the publish
path on hardware.

### 4. Plan-doc draft has drifted

The Phase A plan's §12.1 draft suggested the daemon console script
`reachy-ducky-daemon` would log `uvicorn listening on
127.0.0.1:8765`, mentioned a non-existent `reachy-ducky-app`
command, and hand-wrote a menu-bar mute spec that differs from the
live glyph set. Follow this procedure (which mirrors the code),
not the plan's draft. A follow-up issue should update the plan.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Menu-bar shows `🦆⚠` "daemon unreachable" | Daemon not running, or bound to a different host/port than `http://127.0.0.1:8765` | Start the daemon; or edit `_DAEMON_URL_DEFAULT` in `menubar/src/reachy_ducky_menubar/main.py:24` — the menu-bar URL is not yet env-overridable in Phase A. |
| `curl /brain/query` returns `401 {"detail":"missing bearer token"}` | `REACHY_DUCKY_AUTH_TOKEN` is set on the daemon; your curl didn't send `Authorization: Bearer <token>` | Add the header, or unset the env var and restart the daemon. |
| `curl /brain/query` returns `401 {"detail":"invalid bearer token"}` | Token mismatch between daemon and caller | Re-source `~/.reachy-ducky/.env` in both shells. |
| `curl /brain/query` returns `400 {"detail":"no project_slug in request and no primary project configured"}` | Wizard was not run, or no `primary = true` project in `config.toml` | Re-run `uv run reachy-ducky init`, or edit `~/.reachy-ducky/config.toml`. |
| `curl /brain/query` returns `404 {"detail":"unknown project: <slug>"}` | Slug in request body doesn't appear in the loaded project list | Check `/health`'s `projects` field; fix the slug or add the project. |
| Daemon logs `ClaudeSDKError: not authenticated` (or similar) on first query | `claude login` never run on the Mac | Run `claude login`; re-run the query. |
| Daemon brain errors mentioning `github-mcp-server` | Node.js missing, or `GITHUB_PERSONAL_ACCESS_TOKEN` unset for a project with `github_repo` | `brew install node@20`; export the PAT; restart the daemon. |
| `uv run reachy-ducky-menubar` crashes with `ImportError: reachy-ducky-menubar is macOS-only` | Running on Linux | Menubar is macOS-only by design; there's nothing to run on Linux. |
| `uv run reachy-ducky-menubar` crashes with `ModuleNotFoundError: rumps` | `macos` extra not installed | `uv sync --all-packages --extra macos`. |
| `OpenAIRealtimeVoice` raises `ValueError: OPENAI_API_KEY not set` on the robot | Env var missing in the robot-side process | Export `OPENAI_API_KEY` before starting the app. |
| Realtime session opens but hangs up immediately after user speech | Schema drift between the `openai` SDK version and `voice/openai_realtime.py`'s assumed event names | Confirm `openai==1.109.x`; see module docstring for the verified event shape. |

## Tailing logs

The daemon logs to stdout. For long smoke sessions, start it under
`2>&1 | tee /tmp/reachy-ducky-daemon.log` (or similar) so failure
modes can be cross-referenced with the log after the fact.

## Clean shutdown

Ctrl-C the daemon in its terminal; quit the menu-bar app from its
own menu; stop the Reachy app from the dashboard (or SSH the robot
and terminate the python process).

## Done checklist

- [ ] `/health` returns `ok: true` and the expected brain state
- [ ] Menu-bar icon shows `🦆` (unmuted) and toggles to `🦆🔇` / back
- [ ] Protected-route curl with bearer token succeeds
- [ ] Daemon process exits cleanly on Ctrl-C
- [ ] (Deferred until Known gaps close) Robot app instantiates from the Reachy dashboard and plays `listening` → `thinking` moves end-to-end
