#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$BASE_DIR/.env"

# Kill any existing Polaris process before starting a fresh one
EXISTING=$(pgrep -f "browser_python_mcp.py" 2>/dev/null || true)
if [[ -n "$EXISTING" ]]; then
    echo "[polaris] Encerrando sessão anterior (PID: $EXISTING)..."
    kill $EXISTING 2>/dev/null || true
    sleep 1
fi

# Load env file if present
if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export MCP_TRANSPORT="${MCP_TRANSPORT:-streamable-http}"
export MCP_HOST="${MCP_HOST:-127.0.0.1}"
export MCP_PORT="${MCP_PORT:-8016}"
export BROWSER_HEADLESS="${BROWSER_HEADLESS:-true}"
export BROWSER_USE_MODEL="${BROWSER_USE_MODEL:-gpt-4o-mini}"
export POLARIS_SESSIONS_DIR="${POLARIS_SESSIONS_DIR:-/tmp/polaris_sessions}"

# Never inherit a venv from the calling environment
unset VIRTUAL_ENV
unset PYTHONPATH

exec /usr/bin/python3.11 "$BASE_DIR/browser_python_mcp.py"
