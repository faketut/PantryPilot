"""Tests for the cook flow: pantry decrement + waste-saved + metrics badge.

All Mongo collaborators are monkeypatched so we can assert exactly which
writes happen when the user marks a plan day as cooked.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app import main as app_main


def _now():
    return datetime.now(timezone.utc)


def _iso(days_from_now: int) -> str:
    return (_now() + timedelta(days=days_from_now)).isoformat()


def _fake_mongo(
    pantry: list[dict],
    plan: dict,
    waste_events: list[dict] | None = None,
):
    """Return (find, update, delete, insert) fakes backed by in-memory state.

    The fakes are intentionally small: they only handle the filter/update
    shapes the cook endpoint actually issues, so a test failure points at a
    real bug rather than a fixture mismatch.
    """
    if waste_events is None:
        waste_events = []

    def _find_pantry(filter_):
        if not filter_:
            return list(pantry)
        if "_id" in filter_ and isinstance(filter_["_id"], dict) and "$in" in filter_["_id"]:
            ids = set(filter_["_id"]["$in"])
            return [p for p in pantry if p["_id"] in ids]
        if "name" in filter_ and isinstance(filter_["name"], dict) and "$in" in filter_["name"]:
            names = {n.lower() for n in filter_["name"]["$in"]}
            return [p for p in pantry if (p.get("name") or "").lower() in names]
        if "_id" in filter_:
            return [p for p in pantry if p["_id"] == filter_["_id"]]
        return []

    async def fake_find(collection, filter=None, sort=None, limit=100):
        filter_ = filter or {}
        if collection == "pantry_items":
            return _find_pantry(filter_)
        if collection == "meal_plans":
            if filter_.get("_id") == plan["_id"]:
                return [plan]
            return []
        return []

    async def fake_update(collection, filter, update, upsert=False):
        if collection == "pantry_items" and "_id" in filter:
            for p in pantry:
                if p["_id"] == filter["_id"]:
                    p.update(update.get("$set", {}))
                    return {"matched": 1, "modified": 1, "upserted_id": None}
            return {"matched": 0, "modified": 0, "upserted_id": None}
        if collection == "meal_plans" and "_id" in filter:
            if plan["_id"] == filter["_id"]:
                add = update.get("$addToSet", {}).get("cooked_days")
                if add is not None:
                    plan.setdefault("cooked_days", [])
                    if add not in plan["cooked_days"]:
                        plan["cooked_days"].append(add)
                return {"matched": 1, "modified": 1, "upserted_id": None}
            return {"matched": 0, "modified": 0, "upserted_id": None}
        return {"matched": 0, "modified": 0, "upserted_id": None}

    async def fake_delete(collection, filter):
        if collection == "pantry_items":
            ids = filter.get("_id", {}).get("$in", []) if isinstance(filter.get("_id"), dict) else []
            before = len(pantry)
            pantry[:] = [p for p in pantry if p["_id"] not in ids]
            return {"deleted": before - len(pantry)}
        return {"deleted": 0}

    async def fake_insert_many(collection, documents):
        if collection == "waste_saved_events":
            waste_events.extend(documents)
        return {"inserted_count": len(documents)}

    async def fake_aggregate(collection, pipeline):
        if collection != "waste_saved_events" or not waste_events:
            return []
        grams = sum(float(d.get("grams_saved", 0)) for d in waste_events)
        return [{
            "_id": None,
            "total_grams": grams,
            "items_rescued": len(waste_events),
            "by_name": [d.get("item_name") for d in waste_events],
        }]

    async def fake_count(collection, filter=None):
        if collection == "meal_plans":
            return 1
        if collection == "pantry_items":
            return len(pantry)
        return 0

    return fake_find, fake_update, fake_delete, fake_insert_many, fake_aggregate, fake_count, waste_events


def _patch_app(monkeypatch, fakes):
    find, update, delete, insert, aggregate, count, _ = fakes
    monkeypatch.setattr(app_main, "mcp_find", find)
    monkeypatch.setattr(app_main, "mcp_update_many", update)
    monkeypatch.setattr(app_main, "mcp_delete_many", delete)
    # tools_local helpers (record_waste_saved, get_waste_stats) go through
    # app.mcp_client too — patch the names imported into both modules.
    from app import mcp_client, tools_local
    monkeypatch.setattr(mcp_client, "mcp_insert_many", insert)
    monkeypatch.setattr(tools_local, "_insert_many", lambda c, d: insert(c, d))
    monkeypatch.setattr(tools_local, "mcp_aggregate", aggregate)
    monkeypatch.setattr(app_main, "mcp_count", count)
    # read_pantry is imported directly into main; keep it backed by `pantry`.
    pantry_ref = [p for p in fakes[6]] if False else None  # noqa: F841 (unused, kept for clarity)


async def test_cook_decrements_quantity_and_records_waste(monkeypatch):
    """Cooking a day must decrement pantry rows AND log waste-saved events
    for near-expiry items, so the badge actually moves."""
    pantry = [
        {"_id": "p1", "name": "spinach", "quantity": 2, "category": "produce",
         "expires_at": _iso(1)},
        {"_id": "p2", "name": "chicken breast", "quantity": 1, "category": "meat",
         "expires_at": _iso(2)},
    ]
    plan = {
        "_id": "plan-1",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{
                "meal": "lunch",
                "recipe": "stir fry",
                "ingredients": ["spinach", "chicken breast"],
            }],
            "pantry_item_ids": ["p1", "p2"],
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    waste_events = fakes[6]
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/plan/plan-1/day/1/cooked")

    assert r.status_code == 200
    body = r.json()
    assert body["consumed"] == 2
    assert body["rescued"] == 2

    # spinach (qty=2) → decremented to 1
    spinach = next(p for p in pantry if p["_id"] == "p1")
    assert spinach["quantity"] == 1
    # chicken (qty=1) → deleted
    assert all(p["_id"] != "p2" for p in pantry)
    # Two waste-saved events written (produce 200g + meat 300g)
    assert len(waste_events) == 2
    grams = sorted(e["grams_saved"] for e in waste_events)
    assert grams == [200.0, 300.0]
    # Plan marked cooked
    assert plan["cooked_days"] == [1]


async def test_cook_updates_metrics_badge(monkeypatch):
    """After cooking, /metrics must reflect the new waste-saved totals
    (this is what the navbar badge polls)."""
    pantry = [
        {"_id": "p1", "name": "milk", "quantity": 1, "category": "dairy",
         "expires_at": _iso(2)},
    ]
    plan = {
        "_id": "plan-2",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{"meal": "breakfast", "recipe": "cereal",
                       "ingredients": ["milk"]}],
            "pantry_item_ids": ["p1"],
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    waste_events = fakes[6]
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Badge before cooking: zeroes.
        before = await c.get("/metrics", headers={"Accept": "application/json"})
        assert before.status_code == 200
        assert before.json()["items_rescued"] == 0

        cook = await c.post("/plan/plan-2/day/1/cooked")
        assert cook.status_code == 200

        # Badge after cooking: dairy = 500g = ~1.10 lbs, 1 item rescued.
        after = await c.get("/metrics", headers={"Accept": "application/json"})
        assert after.status_code == 200
        data = after.json()
        assert data["items_rescued"] == 1
        assert data["total_grams"] == 500.0
        assert data["total_lbs"] == pytest.approx(1.10, rel=0, abs=0.01)


async def test_cook_is_idempotent(monkeypatch):
    """Cooking the same day twice must not double-count waste."""
    pantry = [
        {"_id": "p1", "name": "spinach", "quantity": 5, "category": "produce",
         "expires_at": _iso(1)},
    ]
    plan = {
        "_id": "plan-3",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{"meal": "lunch", "recipe": "salad",
                       "ingredients": ["spinach"]}],
            "pantry_item_ids": ["p1"],
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    waste_events = fakes[6]
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r1 = await c.post("/plan/plan-3/day/1/cooked")
        assert r1.status_code == 200
        r2 = await c.post("/plan/plan-3/day/1/cooked")
        assert r2.status_code == 200
        assert r2.json().get("already_cooked") is True

    # Only the first cook decremented and logged.
    assert pantry[0]["quantity"] == 4
    assert len(waste_events) == 1


async def test_cook_falls_back_to_name_match_when_ids_missing(monkeypatch):
    """Plans saved before pantry_item_ids existed must still work via
    name-based fallback."""
    pantry = [
        {"_id": "p1", "name": "spinach", "quantity": 1, "category": "produce",
         "expires_at": _iso(1)},
    ]
    plan = {
        "_id": "plan-4",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{"meal": "lunch", "recipe": "wilted greens",
                       "ingredients": ["spinach"]}],
            # No pantry_item_ids key — simulates a legacy plan.
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    waste_events = fakes[6]
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/plan/plan-4/day/1/cooked")

    assert r.status_code == 200
    body = r.json()
    assert body["consumed"] == 1
    assert body["rescued"] == 1
    assert len(waste_events) == 1
    assert pantry == []  # spinach was the only row and it had qty=1


async def test_cook_fuzzy_matches_recipe_ingredients(monkeypatch):
    """Recipe text like '1 cup skim milk' must match the 'skim milk' pantry
    row even when the agent didn't attach pantry_item_ids."""
    pantry = [
        {"_id": "p1", "name": "skim milk", "quantity": 2, "unit": "carton",
         "category": "dairy", "expires_at": _iso(3)},
        {"_id": "p2", "name": "chicken breast", "quantity": 1, "category": "meat",
         "expires_at": _iso(4)},
    ]
    plan = {
        "_id": "plan-fuzzy",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{
                "meal": "breakfast",
                "recipe": "warm milk over cereal",
                # Free-form strings that exact $in would miss:
                "ingredients": ["1 cup skim milk", "2 oz chicken breast, diced"],
            }],
            # No pantry_item_ids — simulates the LLM forgetting the field.
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    waste_events = fakes[6]
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/plan/plan-fuzzy/day/1/cooked")

    assert r.status_code == 200
    body = r.json()
    assert body["consumed"] == 2, body
    # skim milk qty 2 -> 1, chicken qty 1 -> deleted
    assert next(p for p in pantry if p["_id"] == "p1")["quantity"] == 1
    assert all(p["_id"] != "p2" for p in pantry)
    # dairy 500g + meat 300g, both within rescue window
    assert sorted(e["grams_saved"] for e in waste_events) == [300.0, 500.0]


async def test_cook_picks_up_new_pantry_row_added_after_plan(monkeypatch):
    """User adds 'skim milk' to the pantry after generating the plan. The
    cook flow must still decrement it, even though the plan's
    pantry_item_ids only know about the older rows."""
    pantry = [
        {"_id": "p1", "name": "chicken breast", "quantity": 1, "category": "meat",
         "expires_at": _iso(2)},
        # Added by the user after the plan was generated:
        {"_id": "p2", "name": "skim milk", "quantity": 1, "category": "dairy",
         "expires_at": _iso(3)},
    ]
    plan = {
        "_id": "plan-mixed",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{
                "meal": "dinner",
                "recipe": "creamy chicken",
                "ingredients": ["chicken breast", "skim milk"],
            }],
            "pantry_item_ids": ["p1"],  # plan only knows about chicken
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/plan/plan-mixed/day/1/cooked")

    assert r.status_code == 200
    assert r.json()["consumed"] == 2
    # Both rows consumed (qty 1 each -> deleted)
    assert pantry == []


async def test_cook_skips_waste_for_non_near_expiry(monkeypatch):
    """Eating a year-old box of pasta isn't 'rescuing' anything — it should
    still decrement pantry but log zero waste-saved events."""
    pantry = [
        {"_id": "p1", "name": "pasta", "quantity": 2, "category": "grain",
         "expires_at": _iso(180)},
    ]
    plan = {
        "_id": "plan-5",
        "days": 1,
        "plan": [{
            "day": 1,
            "meals": [{"meal": "dinner", "recipe": "pasta",
                       "ingredients": ["pasta"]}],
            "pantry_item_ids": ["p1"],
        }],
        "cooked_days": [],
    }
    fakes = _fake_mongo(pantry, plan)
    waste_events = fakes[6]
    _patch_app(monkeypatch, fakes)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/plan/plan-5/day/1/cooked")

    assert r.status_code == 200
    body = r.json()
    assert body["consumed"] == 1
    assert body["rescued"] == 0
    assert pantry[0]["quantity"] == 1
    assert waste_events == []
