"""ADK FunctionTools — thin wrappers that call mcp_client helpers.

When ``PLAN_VIA_MCP=true``, reads/writes that the planning agent performs
are routed through the official MongoDB MCP sidecar (``app.mcp_sidecar``)
so the integration is exercised end-to-end. The UI hot-path still uses
direct motor via ``mcp_client`` regardless of the flag.
"""
import logging
import uuid
from datetime import datetime, timezone

from app.config import PLAN_VIA_MCP
from app.mcp_client import mcp_aggregate, mcp_find, mcp_insert_many, mcp_update_many
from app.mcp_sidecar import mcp_sidecar_find, mcp_sidecar_insert_many

log = logging.getLogger(__name__)


async def _find(collection: str, **kwargs) -> list[dict]:
    """Pick the read transport based on PLAN_VIA_MCP, with motor fallback."""
    if PLAN_VIA_MCP:
        try:
            return await mcp_sidecar_find(collection, **kwargs)
        except Exception:
            log.warning(
                "PLAN_VIA_MCP=true but sidecar find failed; falling back to "
                "direct motor for this call.",
                exc_info=True,
            )
    return await mcp_find(collection, **kwargs)


async def _insert_many(collection: str, documents: list[dict]) -> dict:
    """Pick the write transport based on PLAN_VIA_MCP, with motor fallback."""
    if PLAN_VIA_MCP:
        try:
            return await mcp_sidecar_insert_many(collection, documents)
        except Exception:
            log.warning(
                "PLAN_VIA_MCP=true but sidecar insert-many failed; falling "
                "back to direct motor for this call.",
                exc_info=True,
            )
    return await mcp_insert_many(collection, documents)


# ---------------------------------------------------------------------------
# Pantry helpers
# ---------------------------------------------------------------------------

async def ingest_items(items: list[dict]) -> dict:
    """
    Insert normalized pantry items into MongoDB.
    Each item must have: name, quantity, unit, category, expires_at, source.
    Returns insert summary.
    """
    now = datetime.now(timezone.utc).isoformat()
    docs = [
        {
            "_id": str(uuid.uuid4()),
            "name": item["name"],
            "quantity": item.get("quantity", 1),
            "unit": item.get("unit", "unit"),
            "category": item.get("category", "general"),
            "expires_at": item.get("expires_at"),
            "added_at": now,
            "source": item.get("source", "manual"),
        }
        for item in items
    ]
    result = await _insert_many("pantry_items", docs)
    return {"inserted": len(docs), "detail": result}


async def read_pantry(limit: int = 50) -> list[dict]:
    """Return pantry items sorted by expiry (soonest first)."""
    return await _find(
        "pantry_items",
        filter={},
        sort=[("expires_at", 1)],
        limit=limit,
    )


async def mark_item_used(item_id: str) -> dict:
    """Mark a pantry item as used (set used_at timestamp)."""
    return await mcp_update_many(
        "pantry_items",
        filter={"_id": item_id},
        update={"$set": {"used_at": datetime.now(timezone.utc).isoformat()}},
    )


async def save_meal_plan(plan: dict) -> dict:
    """Persist a meal plan document."""
    plan.setdefault("_id", str(uuid.uuid4()))
    plan.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    result = await _insert_many("meal_plans", [plan])
    return {"plan_id": plan["_id"], "detail": result}


async def record_waste_saved(item_id: str, item_name: str, grams: float) -> dict:
    """Record a waste-saved event when a near-expiry item is used in a plan."""
    doc = {
        "_id": str(uuid.uuid4()),
        "item_id": item_id,
        "item_name": item_name,
        "grams_saved": grams,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await _insert_many("waste_saved_events", [doc])
    return {"event_id": doc["_id"], "grams_saved": grams, "detail": result}


async def get_waste_stats() -> dict:
    """Aggregate total waste saved across all events."""
    pipeline = [
        {
            "$group": {
                "_id": None,
                "total_grams": {"$sum": "$grams_saved"},
                "items_rescued": {"$sum": 1},
                "by_name": {"$push": "$item_name"},
            }
        }
    ]
    rows = await mcp_aggregate("waste_saved_events", pipeline)
    if not rows:
        return {"total_grams": 0.0, "total_lbs": 0.0, "items_rescued": 0}
    row = rows[0]
    grams = float(row.get("total_grams", 0))
    return {
        "total_grams": round(grams, 1),
        "total_lbs": round(grams / 453.6, 2),
        "items_rescued": row.get("items_rescued", 0),
    }
