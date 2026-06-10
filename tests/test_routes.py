"""Tests for the route-level guards we added (XSS/DoS surface).

These run against the FastAPI ASGI app via httpx.AsyncClient — no real
Mongo, no real Gemini. Heavy collaborators are monkeypatched.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app import main as app_main


@pytest.fixture
async def client(monkeypatch):
    # Stub out the DB-touching helpers used by the routes under test so we
    # don't reach for a real Atlas cluster.
    deleted_filters: list[dict] = []
    found_filters: list[dict] = []

    async def fake_read_pantry(limit: int = 100):
        return []

    async def fake_mcp_delete_many(collection: str, filter: dict):
        deleted_filters.append({"collection": collection, "filter": filter})
        return {"deleted": 0}

    async def fake_mcp_find(collection, filter=None, sort=None, limit=100):
        found_filters.append({"collection": collection, "filter": filter})
        return []

    monkeypatch.setattr(app_main, "read_pantry", fake_read_pantry)
    monkeypatch.setattr(app_main, "mcp_delete_many", fake_mcp_delete_many)
    monkeypatch.setattr(app_main, "mcp_find", fake_mcp_find)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.deleted = deleted_filters  # type: ignore[attr-defined]
        c.found = found_filters  # type: ignore[attr-defined]
        yield c


async def test_consume_batch_rejects_non_list_payload(client):
    r = await client.post("/pantry/consume-batch", json={"ingredients": "spinach"})
    assert r.status_code == 400


async def test_consume_batch_caps_ingredient_count(client):
    huge = ["x"] * 101
    r = await client.post("/pantry/consume-batch", json={"ingredients": huge})
    assert r.status_code == 400


async def test_consume_batch_pushes_filter_into_mongo(client):
    r = await client.post(
        "/pantry/consume-batch",
        json={"ingredients": ["Spinach", " milk ", ""]},
    )
    assert r.status_code == 200
    # The route should ask Mongo for matching names, not pull everything.
    assert client.found, "expected at least one find() call"
    last = client.found[-1]
    assert last["collection"] == "pantry_items"
    assert last["filter"] == {"name": {"$in": ["spinach", "milk"]}}


async def test_sweep_expired_uses_mongo_predicate(client):
    r = await client.post("/pantry/sweep-expired")
    assert r.status_code == 200
    assert client.deleted, "expected a delete_many call"
    f = client.deleted[-1]["filter"]
    assert "expires_at" in f
    assert "$lt" in f["expires_at"]


async def test_plan_rejects_out_of_range_days(client):
    r = await client.post("/plan?days=99")
    assert r.status_code == 400


async def test_consume_batch_decrements_rather_than_deleting(monkeypatch):
    """Cooking one serving of spinach must not nuke the whole bag."""
    updates: list[dict] = []
    deletes: list[dict] = []

    async def fake_read_pantry(limit: int = 100):
        return []

    async def fake_find(collection, filter=None, sort=None, limit=100):
        return [
            {"_id": "a1", "name": "spinach", "quantity": 3},
            {"_id": "a2", "name": "milk", "quantity": 1},
        ]

    async def fake_update(collection, filter, update, upsert=False):
        updates.append({"filter": filter, "update": update})
        return {"matched": 1, "modified": 1, "upserted_id": None}

    async def fake_delete(collection, filter):
        deletes.append(filter)
        return {"deleted": 1}

    monkeypatch.setattr(app_main, "read_pantry", fake_read_pantry)
    monkeypatch.setattr(app_main, "mcp_find", fake_find)
    monkeypatch.setattr(app_main, "mcp_update_many", fake_update)
    monkeypatch.setattr(app_main, "mcp_delete_many", fake_delete)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/pantry/consume-batch",
            json={"ingredients": ["spinach", "milk"]},
        )

    assert r.status_code == 200
    # Spinach had qty=3 → decremented to 2 (update, not delete)
    spinach_update = next(u for u in updates if u["filter"]["_id"] == "a1")
    assert spinach_update["update"] == {"$set": {"quantity": 2.0}}
    # Milk had qty=1 → fully consumed, deleted via $in
    assert deletes, "expected a delete_many for items hitting 0"
    assert deletes[-1] == {"_id": {"$in": ["a2"]}}


async def test_delete_item_returns_snapshot_header(monkeypatch):
    """DELETE /pantry/{id} must echo the original doc so the UI can undo."""
    import base64
    import json as _json

    snapshot_doc = {
        "_id": "x1", "name": "spinach", "quantity": 1, "unit": "bag",
        "category": "produce", "expires_at": "2026-06-12T00:00:00+00:00",
        "added_at": "2026-06-10T00:00:00+00:00", "source": "seed",
    }

    async def fake_read_pantry(limit: int = 100):
        return []

    async def fake_find(collection, filter=None, sort=None, limit=100):
        return [snapshot_doc] if filter == {"_id": "x1"} else []

    async def fake_delete(collection, filter):
        return {"deleted": 1}

    monkeypatch.setattr(app_main, "read_pantry", fake_read_pantry)
    monkeypatch.setattr(app_main, "mcp_find", fake_find)
    monkeypatch.setattr(app_main, "mcp_delete_many", fake_delete)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.delete("/pantry/x1")

    assert r.status_code == 200
    header = r.headers.get("x-undo-snapshot")
    assert header, "missing undo snapshot header"
    decoded = _json.loads(base64.b64decode(header))
    assert decoded["_id"] == "x1"
    assert decoded["name"] == "spinach"


async def test_restore_upserts_item(monkeypatch):
    """POST /pantry/restore upserts the snapshot doc by _id."""
    upserts: list[dict] = []

    async def fake_read_pantry(limit: int = 100):
        return []

    async def fake_update(collection, filter, update, upsert=False):
        upserts.append({"filter": filter, "update": update, "upsert": upsert})
        return {"matched": 0, "modified": 0, "upserted_id": filter["_id"]}

    monkeypatch.setattr(app_main, "read_pantry", fake_read_pantry)
    monkeypatch.setattr(app_main, "mcp_update_many", fake_update)

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # Reject when _id is missing
        bad = await c.post("/pantry/restore", json={"item": {"name": "x"}})
        assert bad.status_code == 400
        # Strip fields not in the whitelist
        ok = await c.post(
            "/pantry/restore",
            json={"item": {
                "_id": "x1", "name": "spinach", "quantity": 2,
                "evil_field": "<script>", "expires_at": "2026-06-12",
            }},
        )
        assert ok.status_code == 200

    assert upserts, "expected an upsert call"
    last = upserts[-1]
    assert last["upsert"] is True
    assert last["filter"] == {"_id": "x1"}
    assert "evil_field" not in last["update"]["$set"]
    assert last["update"]["$set"]["name"] == "spinach"
