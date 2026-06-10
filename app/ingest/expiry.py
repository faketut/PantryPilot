"""Expiry estimation: JSON lookup table + Mongo cache + Gemini fallback."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google import genai

from app.config import GOOGLE_API_KEY

log = logging.getLogger(__name__)
client = genai.Client(api_key=GOOGLE_API_KEY)

# In-process cache: fastest tier, populated from Mongo + Gemini lookups.
_GEMINI_CACHE: dict[tuple[str, str], int] = {}

# Mongo-persisted cache survives restarts so each (name, category) costs at
# most one Gemini call per cluster lifetime instead of per process lifetime.
_LEARNED_COLLECTION = "expiry_learned"

_LOOKUP: dict[str, int] = {}
_LOOKUP_PATH = Path(__file__).parent.parent.parent / "fixtures" / "expiry_days.json"


def _load_lookup() -> dict[str, int]:
    global _LOOKUP
    if not _LOOKUP and _LOOKUP_PATH.exists():
        _LOOKUP = json.loads(_LOOKUP_PATH.read_text())
    return _LOOKUP


def _days_for_category(category: str, name: str) -> int | None:
    lookup = _load_lookup()
    key = category.lower().strip() if category else ""
    if key in lookup:
        return lookup[key]
    # Try partial match on name
    name_lower = name.lower()
    for k, v in lookup.items():
        if k in name_lower:
            return v
    return None


async def _db_lookup(name_key: str, category_key: str) -> int | None:
    """Read a previously-learned shelf life from Mongo. None on miss/error."""
    # Imported here to avoid a circular import at module load time.
    from app.mcp_client import mcp_find

    cache_id = f"{name_key}|{category_key}"
    try:
        rows = await mcp_find(_LEARNED_COLLECTION, {"_id": cache_id}, limit=1)
    except Exception:
        log.debug("expiry_learned lookup failed (DB unreachable?)", exc_info=True)
        return None
    if rows:
        days = rows[0].get("days")
        if isinstance(days, (int, float)) and days > 0:
            return int(days)
    return None


async def _db_persist(name_key: str, category_key: str, days: int) -> None:
    """Write a Gemini-derived shelf life back to Mongo so it survives restart."""
    from app.mcp_client import mcp_update_many

    cache_id = f"{name_key}|{category_key}"
    doc = {
        "name": name_key,
        "category": category_key,
        "days": days,
        "learned_at": datetime.now(timezone.utc).isoformat(),
        "source": "gemini",
    }
    try:
        # Upsert keeps the collection idempotent if two requests learn the
        # same item concurrently.
        await mcp_update_many(
            _LEARNED_COLLECTION,
            {"_id": cache_id},
            {"$set": doc, "$setOnInsert": {"_id": cache_id}},
            upsert=True,
        )
    except Exception:
        log.debug("expiry_learned persist failed", exc_info=True)


async def estimate_expiry_days(name: str, category: str) -> int:
    """Return number of days until expiry for the given item."""
    days = _days_for_category(category, name)
    if days is not None:
        return days

    name_key = name.lower().strip()
    category_key = (category or "").lower().strip()
    cache_key = (name_key, category_key)
    if cache_key in _GEMINI_CACHE:
        return _GEMINI_CACHE[cache_key]

    # Mongo-backed cross-process cache.
    db_days = await _db_lookup(name_key, category_key)
    if db_days is not None:
        _GEMINI_CACHE[cache_key] = db_days
        return db_days

    # Gemini fallback (sync SDK call → run in a thread so we don't block).
    prompt = (
        f"How many days does '{name}' (category: {category or 'unknown'}) "
        "typically last after purchase before going bad? "
        "Reply with only an integer number, no units or explanation."
    )
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = (response.text or "").strip()
        digits = "".join(c for c in text if c.isdigit())
        result = int(digits) if digits else 14
    except Exception:
        log.exception("Gemini expiry estimate failed for %r/%r", name, category)
        result = 14  # 2-week safe default

    _GEMINI_CACHE[cache_key] = result
    await _db_persist(name_key, category_key, result)
    return result


def expires_at(days: int) -> str:
    """Return ISO datetime string for now + days."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
