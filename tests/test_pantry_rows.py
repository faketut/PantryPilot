"""Tests for the pantry rows view-model and Jinja partial.

These tests pin the XSS-escape behaviour we shipped in commit history. If
someone re-introduces raw f-string HTML, the assertions will scream.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import main as app_main


def _future(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_build_pantry_rows_view_classifies_expiry_bucket():
    items = [
        {"_id": "u", "name": "spinach", "quantity": 1, "category": "produce",
         "expires_at": _future(1)},
        {"_id": "s", "name": "milk",    "quantity": 1, "category": "dairy",
         "expires_at": _future(4)},
        {"_id": "o", "name": "rice",    "quantity": 1, "category": "grain",
         "expires_at": _future(30)},
        {"_id": "x", "name": "old",     "quantity": 1, "category": "x",
         "expires_at": _past(2)},
    ]
    rows = app_main._build_pantry_rows_view(items)
    classes = {r["name"]: r["expiry_class"] for r in rows}
    assert classes["spinach"] == "expiry-urgent"
    assert classes["milk"] == "expiry-soon"
    assert classes["rice"] == "expiry-ok"
    expired_flags = {r["name"]: r["expired"] for r in rows}
    assert expired_flags["old"] is True
    assert expired_flags["spinach"] is False


def test_build_pantry_rows_view_handles_bad_expiry_string():
    rows = app_main._build_pantry_rows_view([
        {"_id": "n", "name": "n", "quantity": 1, "category": "c", "expires_at": ""},
    ])
    assert rows[0]["expired"] is False
    assert rows[0]["expiry_class"] == "expiry-ok"
    assert rows[0]["exp_str"] == "—"


@pytest.mark.asyncio
async def test_pantry_rows_html_escapes_xss(monkeypatch):
    async def fake_read(limit: int = 100):
        return [{
            "_id": 'abc"><script>x</script>',
            "name": "<img src=x onerror=alert(1)>",
            "quantity": 1,
            "category": "<b>p</b>",
            "expires_at": _future(10),
        }]

    monkeypatch.setattr(app_main, "read_pantry", fake_read)
    html = await app_main._pantry_rows_html()

    # Raw payload must not appear unescaped.
    assert "<script>x</script>" not in html
    assert "<img src=x onerror=" not in html
    # But the escaped form should.
    assert "&lt;script&gt;" in html or "&lt;script" in html
    assert "&lt;img" in html


@pytest.mark.asyncio
async def test_pantry_rows_html_db_unreachable_shows_banner(monkeypatch):
    async def boom(limit: int = 100):
        raise RuntimeError("nope")

    monkeypatch.setattr(app_main, "read_pantry", boom)
    html = await app_main._pantry_rows_html()
    assert "Database unreachable" in html
    # Banner row should not include any pantry buttons.
    assert "btn-row-consume" not in html
    assert "btn-row-remove" not in html


@pytest.mark.asyncio
async def test_pantry_rows_html_empty_state(monkeypatch):
    async def empty(limit: int = 100):
        return []

    monkeypatch.setattr(app_main, "read_pantry", empty)
    html = await app_main._pantry_rows_html()
    assert "No items yet." in html
