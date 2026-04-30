# Alpha Live-Mode End-to-End Smoke Procedure

> This procedure was refreshed for the alpha release path on 2026-04-30.
> The old Phase A limitations around stub wake and silent mock audio no
> longer describe main after PR #68 and PR #79.

This procedure walks a teammate through bringing up the alpha live-mode path:
Mac daemon, optional Mac menu-bar, Mac-side live hardware run against a LAN
Reachy Mini, and the robot dashboard install path. The alpha wake word is
`hey jarvis`, backed by vendored openWakeWord ONNX weights. Custom `hey ducky`
wake-word support is deferred to #75.

## 0. Prereqs

### Accounts / logins

- `claude login` - run on the Mac. `ClaudeSDKBrain` uses the locally installed
  Claude CLI's OAuth credentials via `claude_agent_sdk`.
  (`CLAUDE_CODE_OAUTH_TOKEN` is a CI-only path used by the GitHub workflows
  under `.github/workflows/`; local runs do NOT need it.)
- `gh auth login` - only if you plan to manually inspect GitHub via `gh`. The
  daemon itself reaches GitHub via `github-mcp-server`, not `gh`.

### Runtimes on the Mac

- `uv` installed (`brew install uv`).
- Node.js 20+ on `PATH` (`brew install node@20`). Required for
  `npx -y github-mcp-server` which the Pattern B brain spawns (declared in
  `.mcp.json`).

### Env vars (Mac daemon side)

- `GITHUB_PERSONAL_ACCESS_TOKEN` - `repo:read` + `pull_requests:read` +
  `issues:read`. Required for the `mcp__github__*` tool surface when a project
  sets `github_repo`. Read from process env at daemon start by
  `build_brain_options` and forwarded into the MCP subprocess.
- `REACHY_DUCKY_AUTH_TOKEN` - optional bearer token. When set, every daemon
  route except `/health`, `/docs`, and `/openapi.json` requires
  `Authorization: Bearer <token>`. `reachy-ducky init` writes this to
  `~/.reachy-ducky/.env` at 0600 if you supply one during the wizard.

### Env vars (live app / robot side)

- `OPENAI_API_KEY` - required for OpenAI Realtime voice.
- `DAEMON_URL` - e.g. `http://<mac>.tailnet.ts.net:8765`. Defaults to
  `http://127.0.0.1:8765`, which is only right if daemon + app share a host.
- `DAEMON_AUTH_TOKEN` - must match `REACHY_DUCKY_AUTH_TOKEN` if the daemon has
  one set.

### One-time first-run wizard

```bash
cd /Users/dmccarthy/Projects/reachy-ducky
uv sync --all-packages --group dev
uv run reachy-ducky init
```

The wizard writes `~/.reachy-ducky/config.toml` (bind host/port, memory root,
project list), a 0600 `~/.reachy-ducky/.env` secrets file, and the configured
memory tree. At least one project is required; mark one as primary.

## 1. Start the daemon

```bash
cd /Users/dmccarthy/Projects/reachy-ducky
# Load secrets written by `reachy-ducky init`:
set -a; source ~/.reachy-ducky/.env; set +a
uv run reachy-ducky-daemon
```

Expected on stdout: `uvicorn` banner listening on the host/port chosen in the
wizard (defaults: `127.0.0.1:8765`). If `host` is off-loopback and no auth
token is set, the daemon logs a loud warning; fix that before continuing
unless you are on a trusted isolated network.

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

`brain` is `"unbuilt"` on a fresh boot (lazy build; no `ClaudeSDKBrain` has
been constructed yet), `"none"` if no primary project is configured, or the
class name (`"ClaudeSDKBrain"`) once a brain has been materialised by a request.

### Protected-route sanity check

When `REACHY_DUCKY_AUTH_TOKEN` is set:

```bash
curl -s -X POST http://127.0.0.1:8765/brain/query \
  -H "Authorization: Bearer $REACHY_DUCKY_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_utterance":"ping"}' | jq
```

Expected: `200` with `{"text": "...", "specialist_invoked": null}`. Omitting
the header should return `401 {"detail":"missing bearer token"}`.

If no primary project is configured the daemon returns
`400 {"detail":"no project_slug in request and no primary project configured"}`;
add `"project_slug":"<slug>"` to the body. An unknown slug returns
`404 {"detail":"unknown project: <slug>"}`.

## 2. Optional: start the menu-bar app

macOS only. Install the optional extra first:

```bash
uv sync --all-packages --extra macos
uv run reachy-ducky-menubar
```

Verification (reading `menubar/src/reachy_ducky_menubar/main.py` +
`state_icon.py`):

- Idle glyph: `🦆`.
- Status item reads `Status: idle`.
- Click "Mute" - glyph becomes `🦆🔇`, status reads `Status: muted`, menu item
  shows a check. Click again to unmute.
- Stop the daemon. Within ~2s the glyph changes to `🦆⚠` and status reads
  `Status: daemon unreachable` (or `Status: daemon error (<code>)` for a
  reachable-but-broken daemon). Restart the daemon and the glyph returns to
  `🦆`.

## 3. Mac-side live hardware run

Use this path before dashboard install. It validates the alpha live-mode app
against a LAN Reachy Mini while running from the Mac checkout.

```bash
cd /Users/dmccarthy/Projects/reachy-ducky
uv sync --all-packages --group dev
uv run reachy-ducky init

# Terminal 1
set -a; source ~/.reachy-ducky/.env; set +a
REACHY_DUCKY_DAEMON_HOST=0.0.0.0 uv run reachy-ducky-daemon

# Terminal 2
uv run reachy-ducky-app-live
```

Expected output / behavior:

- Terminal 2 prints `Connected. Say 'hey jarvis' to start a turn.` after it
  connects to the LAN Reachy Mini.
- Saying `hey jarvis` triggers wake detection and moves the app into the
  listening state.
- The captured turn is sent to the Mac daemon through `/brain/query`.
- The reply is spoken back through the live voice path, with Reachy returning
  to idle after the turn completes.
- Ctrl-C exits the live app cleanly and prints `Stopped.`.

## 4. Robot dashboard install

The dashboard path is part of alpha release validation; do not treat it as
complete just because the Mac-side live hardware run works.

1. Publish the Reachy Ducky app Space.
2. Open the Reachy dashboard at `http://<robot>:8000`.
3. Install the app from its Space URL.
4. Set robot-side environment variables:
   - `DAEMON_URL=http://<your-mac>.tailnet.ts.net:8765`
   - `DAEMON_AUTH_TOKEN=<bearer token if you set one on the Mac>`
   - `OPENAI_API_KEY=<for OpenAI Realtime voice>`
5. Start the dashboard-installed app and repeat the `hey jarvis` turn.

The dashboard instantiates `ReachyDuckyApp` from
`app_class: reachy_ducky_app.main.ReachyDuckyApp` declared in
`app/reachy_mini_app.yaml`.

## 5. Interact - intended golden path

The orchestrated turn (from `app/src/reachy_ducky_app/conversation.py`
`run_one_turn` + `app/src/reachy_ducky_app/embodiment/state_machine.py`):

1. Wake trigger fires on `hey jarvis`.
2. `sm.transition(LISTENING)` -> `play_move("listening")`.
3. `voice.start_turn()` -> `turn.get_user_text()` blocks for a final
   transcript event from OpenAI Realtime.
4. `sm.transition(THINKING)` -> `play_move("thinking")`.
5. `daemon.brain_query(text)` -> Mac daemon's `/brain/query`.
6. `sm.transition(LISTENING)` - the robot looks at the user while Ducky speaks.
7. `turn.speak_text(reply)` -> OpenAI Realtime TTS over the same WebSocket.
8. `sm.transition(IDLE)` -> `play_move("neutral")`.

A mute toggle anywhere flips the state machine to `MUTED`, which calls
`driver.go_to_sleep()` (the visible "off" posture). Exiting `MUTED` calls
`driver.wake_up()` before the next `play_move`. The menu-bar mute is a local UI
control; alpha does not propagate mute from the menu bar to the robot.

## Known gaps

### 1. Custom wake word is deferred

Alpha uses `hey jarvis` with vendored openWakeWord ONNX weights. Custom
`hey ducky` wake-word support is tracked in #75.

### 2. Menu-bar does not show robot `LISTENING` / `THINKING`

`state_icon.py` defines `🦆👂` and `🦆💭`, but the menu-bar's poll loop
(`main.py::_poll_loop`) only writes the local IDLE / MUTED state. There is no
`/state` push from the robot or daemon.

- TODO (follow-up issue): Menu-bar daemon URL should become env-overridable;
  currently hardcoded as `_DAEMON_URL_DEFAULT` at
  `menubar/src/reachy_ducky_menubar/main.py:24`. Note this is distinct from the
  Reachy-side `DAEMON_URL` env var (`daemon_client.py`) which the robot process
  does read - same conceptual value, two different wiring stories.

### 3. Dashboard install must be validated on hardware

`app/reachy_mini_app.yaml` declares
`app_class: reachy_ducky_app.main.ReachyDuckyApp` and `app/README.md` carries
the HF Space metadata. The alpha release is not complete until someone
publishes the Space, installs it through the Reachy dashboard, and repeats the
live `hey jarvis` turn from the dashboard-launched process.

### 4. Plan-doc draft has drifted

The Phase A plan's section 12.1 draft suggested the daemon console script
`reachy-ducky-daemon` would log `uvicorn listening on 127.0.0.1:8765`, mentioned
a non-existent `reachy-ducky-app` command, and hand-wrote a menu-bar mute spec
that differs from the live glyph set. Follow this procedure, not the plan's
draft. A follow-up issue should update the plan.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Menu-bar shows `🦆⚠` "daemon unreachable" | Daemon not running, or bound to a different host/port than `http://127.0.0.1:8765` | Start the daemon; or edit `_DAEMON_URL_DEFAULT` in `menubar/src/reachy_ducky_menubar/main.py:24` - the menu-bar URL is not yet env-overridable. |
| `curl /brain/query` returns `401 {"detail":"missing bearer token"}` | `REACHY_DUCKY_AUTH_TOKEN` is set on the daemon; your curl didn't send `Authorization: Bearer <token>` | Add the header, or unset the env var and restart the daemon. |
| `curl /brain/query` returns `401 {"detail":"invalid bearer token"}` | Token mismatch between daemon and caller | Re-source `~/.reachy-ducky/.env` in both shells. |
| `curl /brain/query` returns `400 {"detail":"no project_slug in request and no primary project configured"}` | Wizard was not run, or no `primary = true` project in `config.toml` | Re-run `uv run reachy-ducky init`, or edit `~/.reachy-ducky/config.toml`. |
| `curl /brain/query` returns `404 {"detail":"unknown project: <slug>"}` | Slug in request body does not appear in the loaded project list | Check `/health`'s `projects` field; fix the slug or add the project. |
| Daemon logs `ClaudeSDKError: not authenticated` (or similar) on first query | `claude login` never run on the Mac | Run `claude login`; re-run the query. |
| Daemon brain errors mentioning `github-mcp-server` | Node.js missing, or `GITHUB_PERSONAL_ACCESS_TOKEN` unset for a project with `github_repo` | `brew install node@20`; export the PAT; restart the daemon. |
| `uv run reachy-ducky-menubar` crashes with `ImportError: reachy-ducky-menubar is macOS-only` | Running on Linux | Menubar is macOS-only by design; there is nothing to run on Linux. |
| `uv run reachy-ducky-menubar` crashes with `ModuleNotFoundError: rumps` | `macos` extra not installed | `uv sync --all-packages --extra macos`. |
| `OpenAIRealtimeVoice` raises `ValueError: OPENAI_API_KEY not set` | Env var missing in the live app process | Export `OPENAI_API_KEY` before starting the app. |
| Realtime session opens but hangs up immediately after user speech | Schema drift between the `openai` SDK version and `voice/openai_realtime.py`'s assumed event names | Confirm `openai==1.109.x`; see module docstring for the verified event shape. |

## Tailing logs

The daemon logs to stdout. For long smoke sessions, start it under
`2>&1 | tee /tmp/reachy-ducky-daemon.log` (or similar) so failure modes can be
cross-referenced with the log after the fact.

## Clean shutdown

Ctrl-C the daemon in its terminal; quit the menu-bar app from its own menu; stop
the Reachy app from the dashboard (or SSH the robot and terminate the python
process). The Mac-side live app should print `Stopped.` after Ctrl-C.

## Done checklist

- [ ] `/health` returns `ok: true` and the expected brain state
- [ ] Menu-bar icon shows `🦆` (unmuted) and toggles to `🦆🔇` / back
- [ ] Protected-route curl with bearer token succeeds
- [ ] Mac-side live app prints `Connected. Say 'hey jarvis' to start a turn.`
- [ ] Saying `hey jarvis` enters listening, calls `/brain/query`, and speaks a
  reply
- [ ] Mac-side live app exits cleanly on Ctrl-C and prints `Stopped.`
- [ ] Dashboard-installed app repeats the same `hey jarvis` turn on hardware
