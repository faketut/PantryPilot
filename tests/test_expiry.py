"""Tests for the expiry estimator: fixture → in-process cache → Mongo → Gemini."""
import pytest

from app.ingest import expiry


@pytest.fixture(autouse=True)
def _reset_cache():
    expiry._GEMINI_CACHE.clear()
    yield
    expiry._GEMINI_CACHE.clear()


def test_known_category_skips_gemini_and_db():
    # 'meat' is in fixtures/expiry_days.json — must resolve without any IO.
    days = expiry._days_for_category("meat", "chicken breast")
    assert days == 3


@pytest.mark.asyncio
async def test_estimate_uses_fixture_lookup_first(monkeypatch):
    db_calls = []

    async def fake_db_lookup(name_key, category_key):
        db_calls.append((name_key, category_key))
        return None

    monkeypatch.setattr(expiry, "_db_lookup", fake_db_lookup)
    monkeypatch.setattr(
        expiry,
        "client",
        type("F", (), {"models": None}),  # would crash if called
    )

    days = await expiry.estimate_expiry_days("chicken breast", "meat")
    assert days == 3
    assert db_calls == []  # fixture hit, never even checked the DB


@pytest.mark.asyncio
async def test_estimate_persists_gemini_result_to_db(monkeypatch):
    persisted = {}

    async def fake_db_lookup(name_key, category_key):
        return None

    async def fake_db_persist(name_key, category_key, days):
        persisted["args"] = (name_key, category_key, days)

    class _FakeResp:
        text = "9"

    def fake_generate(model, contents):
        return _FakeResp()

    # to_thread wraps the sync call; patch the underlying function.
    monkeypatch.setattr(expiry, "_db_lookup", fake_db_lookup)
    monkeypatch.setattr(expiry, "_db_persist", fake_db_persist)
    monkeypatch.setattr(
        expiry.client.models,
        "generate_content",
        fake_generate,
        raising=False,
    )

    days = await expiry.estimate_expiry_days("dragonfruit", "exotic-fruit")
    assert days == 9
    assert persisted["args"] == ("dragonfruit", "exotic-fruit", 9)
    # Second call should hit the in-process cache, not Gemini or DB.
    persisted.clear()
    days2 = await expiry.estimate_expiry_days("dragonfruit", "exotic-fruit")
    assert days2 == 9
    assert "args" not in persisted  # not re-persisted


@pytest.mark.asyncio
async def test_estimate_uses_db_cache_before_gemini(monkeypatch):
    gemini_calls = []

    async def fake_db_lookup(name_key, category_key):
        return 42

    def fake_generate(*args, **kwargs):
        gemini_calls.append((args, kwargs))
        raise AssertionError("Gemini should not be called when DB has the value")

    monkeypatch.setattr(expiry, "_db_lookup", fake_db_lookup)
    monkeypatch.setattr(
        expiry.client.models,
        "generate_content",
        fake_generate,
        raising=False,
    )

    days = await expiry.estimate_expiry_days("durian", "exotic-fruit")
    assert days == 42
    assert gemini_calls == []


@pytest.mark.asyncio
async def test_estimate_falls_back_to_14_on_gemini_error(monkeypatch):
    async def fake_db_lookup(name_key, category_key):
        return None

    async def fake_db_persist(name_key, category_key, days):
        pass

    def boom(*args, **kwargs):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(expiry, "_db_lookup", fake_db_lookup)
    monkeypatch.setattr(expiry, "_db_persist", fake_db_persist)
    monkeypatch.setattr(
        expiry.client.models,
        "generate_content",
        boom,
        raising=False,
    )

    days = await expiry.estimate_expiry_days("unknownium", "alien")
    assert days == 14
