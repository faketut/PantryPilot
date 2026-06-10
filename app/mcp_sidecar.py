"""MongoDB MCP sidecar client (feature-flagged transport for the agent path).

When ``PLAN_VIA_MCP=true``, the ADK agent's read/write operations against
Mongo are routed through the official ``mongodb-mcp-server`` sidecar at
``MCP_HTTP_URL`` instead of the direct ``motor`` connection used by the
HTMX-render hot path.

This is what makes the MongoDB MCP integration end-to-end verifiable: any
external MCP client (Claude Desktop, an external ADK agent, the MCP
Inspector) can point at the same sidecar URL and see the same pantry items
and meal plans that PantryPilot's in-app agent wrote through the protocol.

A fresh MCP session is opened per call. That's deliberate: ``/plan`` runs
once on a click, takes seconds to complete on the Gemini side, and the
extra ~100ms of session setup is worth the operational simplicity of not
having to track session liveness or recover from sidecar restarts.
"""
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import DB_NAME, MCP_HTTP_URL

log = logging.getLogger(__name__)


@asynccontextmanager
async def _session():
    url = MCP_HTTP_URL.rstrip("/") + "/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _extract_json(result: Any) -> list[dict]:
    """Pull list[dict] payloads out of an MCP tool-call response.

    The mongodb-mcp-server returns JSON-serialized text blocks; we parse
    each block and flatten lists into one return list.
    """
    out: list[dict] = []
    for block in (result.content or []):
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            out.extend(p for p in payload if isinstance(p, dict))
        elif isinstance(payload, dict):
            # Some sidecar responses wrap rows under "documents" / "data".
            for key in ("documents", "data", "results"):
                if key in payload and isinstance(payload[key], list):
                    out.extend(p for p in payload[key] if isinstance(p, dict))
                    break
            else:
                out.append(payload)
    return out


async def mcp_sidecar_find(
    collection: str,
    filter: dict | None = None,
    sort: list[tuple[str, int]] | None = None,
    limit: int = 100,
) -> list[dict]:
    args: dict[str, Any] = {
        "database": DB_NAME,
        "collection": collection,
        "filter": filter or {},
        "limit": limit,
    }
    if sort:
        # mongodb-mcp-server expects sort as a {field: direction} object.
        args["sort"] = {k: v for k, v in sort}
    async with _session() as session:
        result = await session.call_tool("find", args)
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP sidecar find failed: {result}")
    return _extract_json(result)


async def mcp_sidecar_insert_many(collection: str, documents: list[dict]) -> dict:
    if not documents:
        return {"inserted": 0}
    args = {
        "database": DB_NAME,
        "collection": collection,
        "documents": documents,
    }
    async with _session() as session:
        result = await session.call_tool("insert-many", args)
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP sidecar insert-many failed: {result}")
    return {"inserted": len(documents)}
