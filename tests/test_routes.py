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
