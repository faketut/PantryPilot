"""Pure helpers for the cook/consume flow.

Kept free of any Mongo I/O so they're trivially unit-testable and so the
route in ``app.main`` remains the single place tests need to monkeypatch
``mcp_*`` against.
"""
import re
from datetime import datetime, timezone

# Cap how many ingredients we'll process per cook/consume call.
MAX_BATCH_INGREDIENTS = 100

# Estimated grams per consumed unit, used to log waste-saved events when a
# near-expiry pantry row is cooked. Matches the heuristic the planning agent
# previously used for its projection, so the post-cook number lands close to
# what the user saw in the generated plan.
GRAMS_PER_UNIT_BY_CATEGORY = {
    "produce": 200.0,
    "dairy": 500.0,
    "meat": 300.0,
    "condiments": 100.0,
    "spices": 100.0,
}
GRAMS_PER_UNIT_DEFAULT = 400.0

# Only items expiring within this many days count toward "waste rescued" —
# eating a year-old box of pasta isn't rescuing anything.
RESCUE_WINDOW_DAYS = 5


def grams_for(item: dict) -> float:
    """Estimated grams rescued per cook of this pantry row.

    Fixed per item (per cook event), not multiplied by quantity — a unit of
    "chicken breast" already represents a serving; multiplying by qty would
    overcount when one bag of spinach gets used across two meals.
    """
    cat = (item.get("category") or "").lower()
    return GRAMS_PER_UNIT_BY_CATEGORY.get(cat, GRAMS_PER_UNIT_DEFAULT)


def is_near_expiry(item: dict, now: datetime) -> bool:
    exp = item.get("expires_at")
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp)
    except (TypeError, ValueError):
        return False
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    return (exp_dt - now).days <= RESCUE_WINDOW_DAYS


_INGREDIENT_TOKEN_RE = re.compile(r"[a-z]+")


def tokens(text: str) -> set[str]:
    """Word tokens (>=3 chars) from a lowercased string. Drops digits,
    units, and short connectors like 'of'/'a'/'to'."""
    return {t for t in _INGREDIENT_TOKEN_RE.findall(text.lower()) if len(t) >= 3}


def match_pantry_to_ingredients(
    pantry: list[dict], ingredients: list[str]
) -> list[dict]:
    """Return pantry rows that match the recipe ingredients.

    A pantry row matches an ingredient iff every word in the pantry name
    (>=3 chars) appears as a word in the ingredient string. So "skim milk"
    in the pantry matches "1 cup skim milk" and "skim milk, cold", but not
    "milk" alone. A pantry row of just "milk" matches any ingredient
    containing the word "milk". Each ingredient slot is consumed by at
    most one pantry row, so duplicates of the same pantry item don't all
    get decremented from a single ingredient mention.
    """
    used_ingredients: set[int] = set()
    matched: list[dict] = []
    ing_tokens = [tokens(i) for i in ingredients]
    for item in pantry:
        name = (item.get("name") or "").strip().lower()
        if not name:
            continue
        name_tokens = tokens(name)
        if not name_tokens:
            continue
        for idx, toks in enumerate(ing_tokens):
            if idx in used_ingredients:
                continue
            if name_tokens.issubset(toks):
                matched.append(item)
                used_ingredients.add(idx)
                break
    return matched


def build_pantry_rows_view(items: list[dict]) -> list[dict]:
    """Convert raw pantry docs into the view-model the partial expects."""
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for item in items:
        exp = item.get("expires_at", "")
        try:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            days_left = (exp_dt - now).days
            expired = days_left < 0
            if days_left <= 2:
                expiry_class = "expiry-urgent"
            elif days_left <= 5:
                expiry_class = "expiry-soon"
            else:
                expiry_class = "expiry-ok"
            exp_str = exp[:10]
        except Exception:
            expired = False
            expiry_class = "expiry-ok"
            exp_str = exp[:10] if exp else "—"
        rows.append({
            "item_id": str(item.get("_id", "")),
            "name": item.get("name", ""),
            "qty": item.get("quantity", ""),
            "category": item.get("category", ""),
            "exp_str": exp_str,
            "expiry_class": expiry_class,
            "expired": expired,
        })
    return rows
