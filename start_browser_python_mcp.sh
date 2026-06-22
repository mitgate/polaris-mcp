#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$BASE_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export MCP_TRANSPORT="streamable-http"
export MCP_HOST="127.0.0.1"
export MCP_PORT="8016"

export BROWSER_HEADLESS="${BROWSER_HEADLESS:-true}"
export BROWSER_USE_MODEL="${BROWSER_USE_MODEL:-gpt-4o-mini}"

# Garante que usa o Python do sistema, nunca o venv herdado do ambiente
unset VIRTUAL_ENV
unset PYTHONPATH

exec /usr/bin/python3.11 "$BASE_DIR/browser_python_mcp.py"
