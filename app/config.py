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

PORT: int = int(os.getenv("PORT", "8000"))

# Set true when behind a corporate TLS-intercepting proxy
MDB_TLS_ALLOW_INVALID_CERTS: bool = os.getenv("MDB_TLS_ALLOW_INVALID_CERTS", "false").lower() == "true"

DB_NAME = "pantrpilot"
