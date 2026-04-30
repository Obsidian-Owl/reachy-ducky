---
# HF Space presentation chrome ONLY — fields HuggingFace Spaces reads
# that do NOT appear in reachy_mini_app.yaml. Shared fields (title,
# emoji, app_class, python_version, description) live in the YAML
# (canonical for the on-robot Pollen dashboard); this frontmatter
# carries only HF-Space-listing metadata that has no other consumer,
# so drift is impossible by construction (a field can't drift if it
# exists in only one place). See closed issue #16.
colorFrom: yellow
colorTo: blue
sdk: static
pinned: false
short_description: Read-only rubber-ducky companion for agentic SWE work.
tags:
  - reachy_mini
  - reachy_mini_python_app
---

# Reachy Ducky — Reachy Mini App

The Reachy Mini side of the [Reachy Ducky project](https://github.com/Obsidian-Owl/reachy-ducky).

## What it does

On-demand conversational rubber-ducky companion for agentic SWE work.
For alpha, say `hey jarvis`, ask a question, get an answer. The alpha wake
word uses vendored openWakeWord ONNX weights; custom `hey ducky` support is
deferred to #75. The Mac-side daemon does the thinking (Claude Agent SDK with a
project-scoped tool surface); this app handles voice I/O on the robot.

## Install

1. **Mac side (the thinking brain)**
   - `pip install reachy-ducky-daemon`
   - `reachy-ducky init` (first-time setup wizard)
   - `reachy-ducky-daemon` (starts the HTTP service)

2. **Robot side (this app)**
   - From the Reachy dashboard at `http://<robot>:8000`, one-click
     install this app from its HF Space URL.
   - Set environment variables on the robot:
     - `DAEMON_URL=http://<your-mac>.tailnet.ts.net:8765`
     - `DAEMON_AUTH_TOKEN=<bearer token if you set one on the Mac>`
     - `OPENAI_API_KEY=<for OpenAI Realtime voice>`

## Architecture

Split-brain: voice on the robot, thinking on the Mac.
Full design: https://github.com/Obsidian-Owl/reachy-ducky/blob/main/docs/plans/2026-04-21-reachy-ducky-design.md

Alpha scope: on-demand conversational mode only. Wake-word triggers
a single turn; response speaks back. Listening / thinking state machine
expresses body motion (head tilt, antenna animation) via the Reachy
emotions library. Always-on passive observation (phase C) is deferred.
