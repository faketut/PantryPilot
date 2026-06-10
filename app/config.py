"""Central configuration — reads .env and validates required vars."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


GOOGLE_API_KEY: str = _require("GOOGLE_API_KEY")
MDB_MCP_CONNECTION_STRING: str = _require("MDB_MCP_CONNECTION_STRING")

# MCP transport: "http" (default, dev/demo) or "stdio" (single-process judge mode)
MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "http").lower()
MCP_HTTP_URL: str = os.getenv("MCP_HTTP_URL", "http://127.0.0.1:3001")

# When true, the ADK agent's read/write tools (read_pantry, save_meal_plan,
# record_waste_saved) route through the mongodb-mcp-server sidecar via the
# official MCP protocol instead of the direct motor connection. Keeps the
# HTMX render path on direct motor for snappy UI; only the planning agent
# pays the protocol round-trip cost.
PLAN_VIA_MCP: bool = os.getenv("PLAN_VIA_MCP", "false").lower() == "true"

PORT: int = int(os.getenv("PORT", "8000"))

# Set true when behind a corporate TLS-intercepting proxy
MDB_TLS_ALLOW_INVALID_CERTS: bool = os.getenv("MDB_TLS_ALLOW_INVALID_CERTS", "false").lower() == "true"

DB_NAME = "pantrpilot"
