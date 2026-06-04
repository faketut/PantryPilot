"""Expiry estimation: JSON lookup table + Gemini fallback."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from app.config import GOOGLE_API_KEY

client = genai.Client(api_key=GOOGLE_API_KEY)

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


async def estimate_expiry_days(name: str, category: str) -> int:
    """Return number of days until expiry for the given item."""
    days = _days_for_category(category, name)
    if days is not None:
        return days

    # Gemini fallback
    prompt = (
        f"How many days does '{name}' (category: {category or 'unknown'}) "
        "typically last after purchase before going bad? "
        "Reply with only an integer number, no units or explanation."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = response.text.strip()
    try:
        return int("".join(c for c in text if c.isdigit()))
    except ValueError:
        return 14  # 2-week safe default


def expires_at(days: int) -> str:
    """Return ISO datetime string for now + days."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
