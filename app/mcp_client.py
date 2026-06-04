"""
MongoDB data-access layer.

Uses motor (async MongoDB driver) directly for reliable Atlas connectivity.
The mongodb-mcp-server process runs alongside the app as the partner integration,
and the agent communicates with it via this same data layer.

Public API is intentionally identical to the original MCP-over-HTTP wrapper so
all callers (tools_local.py, ingest modules) are unaffected.
"""
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import MDB_MCP_CONNECTION_STRING, MDB_TLS_ALLOW_INVALID_CERTS

DB_NAME = "pantrpilot"

# ---------------------------------------------------------------------------
# Shared motor client (created once per process)
# ---------------------------------------------------------------------------

import time as _time

_CIRCUIT_OPEN_DURATION = 10  # seconds to skip DB after failure
_circuit_open_until: float = 0.0


def _make_client() -> AsyncIOMotorClient:
    kwargs: dict[str, Any] = {
        "serverSelectionTimeoutMS": 3000,
        "connectTimeoutMS": 3000,
        "socketTimeoutMS": 5000,
    }
    if MDB_TLS_ALLOW_INVALID_CERTS:
        kwargs["tls"] = True
        kwargs["tlsInsecure"] = True
    return AsyncIOMotorClient(MDB_MCP_CONNECTION_STRING, **kwargs)


_client: AsyncIOMotorClient | None = None


def _db():
    global _client, _circuit_open_until
    if _time.time() < _circuit_open_until:
        raise ConnectionError("Circuit breaker open — DB unavailable")
    if _client is None:
        _client = _make_client()
    return _client[DB_NAME]


def _trip_breaker():
    """Trip the circuit breaker after a DB error."""
    global _circuit_open_until
    _circuit_open_until = _time.time() + _CIRCUIT_OPEN_DURATION


def _reset_client():
    """Force a new client on next call (call after a connection error)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None

import functools
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError


def _with_circuit_breaker(func):
    """Wrap an async function to trip the circuit breaker on DB errors."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except (ConnectionFailure, ServerSelectionTimeoutError, ConnectionError, OSError) as e:
            _trip_breaker()
            raise
    return wrapper


# ---------------------------------------------------------------------------
# Public helpers — same signatures as the original MCP wrappers
# ---------------------------------------------------------------------------

@_with_circuit_breaker
async def mcp_find(
    collection: str,
    filter: dict | None = None,
    sort: list[tuple[str, int]] | None = None,
    limit: int = 100,
) -> list[dict]:
    cursor = _db()[collection].find(_oid_filter(filter or {}))
    if sort:
        cursor = cursor.sort(sort)
    cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=limit)
    # Convert ObjectId to string so dicts stay JSON-serialisable
    for d in docs:
        if "_id" in d:
            d["_id"] = str(d["_id"])
    return docs


def _oid_filter(filter: dict) -> dict:
    """Convert string _id values to ObjectId for Motor queries."""
    if "_id" not in filter:
        return filter
    out = dict(filter)
    val = out["_id"]
    if isinstance(val, str):
        try:
            out["_id"] = ObjectId(val)
        except Exception:
            pass
    elif isinstance(val, dict):
        # e.g. {"$in": ["abc", "def"]}
        inner = {}
        for op, operand in val.items():
            if op == "$in" and isinstance(operand, list):
                inner[op] = [ObjectId(v) if isinstance(v, str) else v for v in operand]
            else:
                inner[op] = operand
        out["_id"] = inner
    return out


@_with_circuit_breaker
async def mcp_insert_many(collection: str, documents: list[dict]) -> dict:
    if not documents:
        return {"inserted": 0}
    result = await _db()[collection].insert_many(documents, ordered=False)
    return {"inserted": len(result.inserted_ids)}


@_with_circuit_breaker
async def mcp_update_many(
    collection: str,
    filter: dict,
    update: dict,
) -> dict:
    result = await _db()[collection].update_many(_oid_filter(filter), update)
    return {"matched": result.matched_count, "modified": result.modified_count}


@_with_circuit_breaker
async def mcp_aggregate(collection: str, pipeline: list[dict]) -> list[dict]:
    cursor = _db()[collection].aggregate(pipeline)
    docs = await cursor.to_list(length=None)
    for d in docs:
        d.pop("_id", None)
    return docs




@_with_circuit_breaker
async def mcp_delete_many(collection: str, filter: dict) -> dict:
    result = await _db()[collection].delete_many(_oid_filter(filter))
    return {"deleted": result.deleted_count}
@_with_circuit_breaker
async def mcp_count(collection: str, filter: dict | None = None) -> int:
    return await _db()[collection].count_documents(filter or {})
