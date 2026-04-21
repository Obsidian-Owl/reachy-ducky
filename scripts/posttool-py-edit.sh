#!/usr/bin/env bash
# PostToolUse hook: runs after Edit/Write/MultiEdit.
# Reads the tool call JSON on stdin, extracts the edited file, and:
#   1) runs `ruff check --fix` on the file if it's Python
#   2) triggers an incremental GitNexus re-analyze in the background
# Output to stdout/stderr is shown to Claude; keep it quiet on success.

set -euo pipefail

# Read the hook JSON payload from stdin
payload="$(cat)"

# Extract file_path. MultiEdit puts the path at the same top-level key.
file_path="$(
  printf '%s' "$payload" \
    | python3 -c 'import json, sys; d=json.load(sys.stdin); print(d.get("tool_input", {}).get("file_path", ""))' \
    2>/dev/null || true
)"

if [[ -z "$file_path" ]]; then
  exit 0
fi

case "$file_path" in
  *.py)
    # Lint-fix the file if ruff is installed; stay silent unless it errors
    if command -v uv >/dev/null 2>&1; then
      uv run ruff check --fix --exit-zero "$file_path" >/dev/null 2>&1 || true
      uv run ruff format "$file_path" >/dev/null 2>&1 || true
    fi

    # Kick off a GitNexus re-analyze in the background (incremental by default).
    # `--skip-agents-md` preserves our hand-curated CLAUDE.md. Do not block.
    if command -v npx >/dev/null 2>&1; then
      (nohup npx --no-install gitnexus analyze --skip-agents-md \
         >/tmp/reachy-ducky-gitnexus.log 2>&1 &) || true
    fi
    ;;
esac

exit 0
