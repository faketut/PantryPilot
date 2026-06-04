#!/usr/bin/env bash
# PantryPilot startup script
# Usage: bash scripts/start.sh  (from any directory)
# Starts MongoDB MCP server (HTTP) on :3001, then FastAPI on :8000.

# -- Resolve project root regardless of cwd --------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# -- Augment PATH for common tool locations --------------------------------
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# nvm / fnm node shims
[ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh"
[ -f "$HOME/.fnm/fnm" ] && eval "$(fnm env 2>/dev/null)" || true

# Verify required commands
for cmd in uv npx; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' not found in PATH. Install it before running this script."
    echo "  uv:  https://astral.sh/uv/  |  npx: ships with Node.js 20+"
    exit 1
  fi
done

# -- Load .env -------------------------------------------------------------
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MCP_TRANSPORT="${MCP_TRANSPORT:-http}"
MCP_PID=""

# -- MongoDB MCP sidecar (optional — app still works without it) -----------
if [ "$MCP_TRANSPORT" = "http" ]; then
  lsof -ti:3001 | xargs kill -9 2>/dev/null || true

  echo "Starting MongoDB MCP server on :3001..."
  MDB_MCP_CONNECTION_STRING="$MDB_MCP_CONNECTION_STRING" \
  MDB_MCP_TELEMETRY=disabled \
  NODE_TLS_REJECT_UNAUTHORIZED=0 \
    npx -y mongodb-mcp-server@latest --transport http --httpPort=3001 &>/tmp/mcp-server.log &
  MCP_PID=$!
  echo "  MCP PID: $MCP_PID  (logs: /tmp/mcp-server.log)"

  # Wait up to 8 s for MCP server to accept connections
  for i in $(seq 1 8); do
    sleep 1
    if curl -sf http://localhost:3001/ &>/dev/null || \
       curl -sf http://localhost:3001/health &>/dev/null || \
       lsof -ti:3001 &>/dev/null; then
      echo "  MCP server ready (${i}s)"
      break
    fi
    if [ "$i" -eq 8 ]; then
      echo "  MCP server not responding — app will run in direct-motor mode."
    fi
  done
fi

# -- FastAPI app -----------------------------------------------------------
echo "Starting PantryPilot on :${PORT:-8000}..."
uv run python -m app.main &
APP_PID=$!

cleanup() {
  echo "Shutting down..."
  kill "$APP_PID" 2>/dev/null || true
  [ -n "$MCP_PID" ] && kill "$MCP_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo ""
echo "  App:  http://localhost:${PORT:-8000}"
[ -n "$MCP_PID" ] && echo "  MCP:  http://localhost:3001"
echo ""
echo "Press Ctrl+C to stop."

wait "$APP_PID"
