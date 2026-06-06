"""FastAPI application — routes, lifespan, response models."""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

from app.agent import run_plan_agent
from app.brightdata_mcp import scrape_url
from app.ingest.expiry import estimate_expiry_days, expires_at
from app.ingest.receipt import parse_markdown_text, parse_receipt
from app.mcp_client import mcp_delete_many, mcp_find, mcp_insert_many, mcp_update_many
from app.tools_local import get_waste_stats, ingest_items, read_pantry


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="PantryPilot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

async def _pantry_rows_html() -> str:
    try:
        pantry = await read_pantry(limit=100)
    except Exception:
        return '<tr><td colspan="5" style="text-align:center;color:#ef4444;padding:1.5rem">Database unreachable — retrying…</td></tr>'

    now = datetime.now(timezone.utc)
    if not pantry:
        return '<tr><td colspan="5" style="text-align:center;color:#9ca3af;padding:1.5rem">No items yet.</td></tr>'
    rows = ""
    for item in pantry:
        item_id = item.get("_id", "")
        exp = item.get("expires_at", "")
        try:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            days_left = (exp_dt - now).days
            expired = days_left < 0
            cls = "expiry-urgent" if days_left <= 2 else ("expiry-soon" if days_left <= 5 else "expiry-ok")
            exp_str = exp[:10]
        except Exception:
            expired = False
            cls = "expiry-ok"
            exp_str = exp[:10] if exp else "—"
        qty = item.get("quantity", "")
        try:
            qty_num = float(qty)
        except (TypeError, ValueError):
            qty_num = 1
        item_name = item.get("name", "")
        consume_btn = (
            f"<button class='btn-row btn-row-consume' title='Mark one used'"
            f" hx-patch='/pantry/{item_id}/consume'"
            f" hx-target='#pantry-body' hx-swap='innerHTML'"
            f" hx-confirm='Mark one {item_name} as used?'>"
            "<svg viewBox='0 0 24 24' width='14' height='14' fill='none' stroke='currentColor' "
            "stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'>"
            "<polyline points='20 6 9 17 4 12'/></svg></button>"
        ) if not expired else ""
        remove_btn = (
            f"<button class='btn-row btn-row-remove' title='Remove'"
            f" hx-delete='/pantry/{item_id}'"
            f" hx-target='#pantry-body' hx-swap='innerHTML'"
            f" hx-confirm='Remove {item_name} from pantry?'>"
            "<svg viewBox='0 0 24 24' width='14' height='14' fill='none' stroke='currentColor' "
            "stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'>"
            "<polyline points='3 6 5 6 21 6'/>"
            "<path d='M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6'/>"
            "<path d='M10 11v6M14 11v6'/></svg></button>"
        )
        row_cls = " class='row-expired'" if expired else ""
        rows += (
            f"<tr{row_cls}>"
            f"<td style='font-weight:500'>{item_name}</td>"
            f"<td>{qty}</td>"
            f"<td>{item.get('category','')}</td>"
            f'<td class="{cls}">{exp_str}</td>'
            f"<td class='row-actions'>{consume_btn}{remove_btn}</td>"
            "</tr>"
        )
    return rows


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        pantry = await read_pantry(limit=50)
    except Exception:
        pantry = []
    try:
        stats = await get_waste_stats()
    except Exception:
        stats = {"total_grams": 0.0, "total_lbs": 0.0, "items_rescued": 0}
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "pantry": pantry, "stats": stats},
    )


# ---------------------------------------------------------------------------
# Receipt OCR
# ---------------------------------------------------------------------------

@app.post("/receipt", response_class=HTMLResponse)
async def upload_receipt(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")
    image_bytes = await file.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")
    parsed = await parse_receipt(image_bytes, file.content_type)
    if not parsed:
        raise HTTPException(status_code=422, detail="No grocery items found in image")
    enriched = []
    for item in parsed:
        days = await estimate_expiry_days(item.name, item.category)
        enriched.append({
            "name": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "category": item.category,
            "expires_at": expires_at(days),
            "source": "receipt",
        })
    await ingest_items(enriched)
    return HTMLResponse(await _pantry_rows_html())


# ---------------------------------------------------------------------------
# URL import (Bright Data MCP → Gemini parse)
# ---------------------------------------------------------------------------

@app.post("/import/url", response_class=HTMLResponse)
async def import_from_url(url: str):
    """Scrape any grocery URL via Bright Data MCP, parse items with Gemini."""
    if not url or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="A valid http/https URL is required")

    scrape = await scrape_url(url)

    if scrape.no_token:
        raise HTTPException(
            status_code=503,
            detail="BRIGHTDATA_API_TOKEN not configured. Add it to .env and restart.",
        )
    if not scrape.success:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {scrape.error}")

    parsed = await parse_markdown_text(scrape.markdown)
    if not parsed:
        raise HTTPException(status_code=422, detail="No grocery items found on that page")

    enriched = []
    for item in parsed:
        days = await estimate_expiry_days(item.name, item.category)
        enriched.append({
            "name": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "category": item.category,
            "expires_at": expires_at(days),
            "source": "brightdata",
        })

    await ingest_items(enriched)
    return HTMLResponse(await _pantry_rows_html())


# ---------------------------------------------------------------------------
# Pantry rows (HTMX partial)
# ---------------------------------------------------------------------------

@app.get("/pantry-rows", response_class=HTMLResponse)
async def pantry_rows():
    return HTMLResponse(await _pantry_rows_html())


# ---------------------------------------------------------------------------
# Pantry item actions
# ---------------------------------------------------------------------------

@app.patch("/pantry/{item_id}/consume", response_class=HTMLResponse)
async def consume_item(item_id: str):
    """Decrement quantity by 1; remove the item when it reaches 0."""
    results = await mcp_find("pantry_items", {"_id": item_id}, limit=1)
    if not results:
        raise HTTPException(status_code=404, detail="Item not found")
    item = results[0]
    qty = item.get("quantity", 1)
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        qty = 1
    if qty <= 1:
        await mcp_delete_many("pantry_items", {"_id": item_id})
    else:
        await mcp_update_many(
            "pantry_items",
            {"_id": item_id},
            {"$set": {"quantity": qty - 1}},
        )
    return HTMLResponse(await _pantry_rows_html())


@app.delete("/pantry/{item_id}", response_class=HTMLResponse)
async def delete_item(item_id: str):
    """Hard-delete a pantry item (expired, discarded, etc.)."""
    await mcp_delete_many("pantry_items", {"_id": item_id})
    return HTMLResponse(await _pantry_rows_html())


@app.post("/pantry/sweep-expired", response_class=HTMLResponse)
async def sweep_expired():
    """Remove all items whose expiry date has passed."""
    all_items = await mcp_find("pantry_items", {}, limit=1000)
    expired_ids = []
    for item in all_items:
        exp = item.get("expires_at", "")
        try:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt < datetime.now(timezone.utc):
                expired_ids.append(item["_id"])
        except Exception:
            pass
    if expired_ids:
        await mcp_delete_many("pantry_items", {"_id": {"$in": expired_ids}})
    return HTMLResponse(await _pantry_rows_html())


@app.post("/pantry/consume-batch", response_class=HTMLResponse)
async def consume_batch(request: Request):
    """Remove a list of ingredient names from the pantry (post-meal cleanup)."""
    body = await request.json()
    names = [n.lower().strip() for n in body.get("ingredients", [])]
    if not names:
        return HTMLResponse(await _pantry_rows_html())
    pantry = await mcp_find("pantry_items", {}, limit=1000)
    ids_to_delete = []
    for item in pantry:
        if item.get("name", "").lower().strip() in names:
            ids_to_delete.append(item["_id"])
    if ids_to_delete:
        await mcp_delete_many("pantry_items", {"_id": {"$in": ids_to_delete}})
    return HTMLResponse(await _pantry_rows_html())



# ---------------------------------------------------------------------------
# Demo reset — wipe pantry_items/meal_plans/waste_saved_events and re-seed
# ---------------------------------------------------------------------------

@app.post("/pantry/reset", response_class=HTMLResponse)
async def reset_pantry():
    import uuid as _uuid
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    await mcp_delete_many("pantry_items", {})
    await mcp_delete_many("meal_plans", {})
    await mcp_delete_many("waste_saved_events", {})
    seed_items = [
        {"_id": str(_uuid.uuid4()), "name": "spinach", "quantity": 1, "unit": "bag",
         "category": "produce", "expires_at": (now + timedelta(days=1)).isoformat(),
         "added_at": now.isoformat(), "source": "seed"},
        {"_id": str(_uuid.uuid4()), "name": "chicken breast", "quantity": 2, "unit": "lb",
         "category": "meat", "expires_at": (now + timedelta(days=2)).isoformat(),
         "added_at": now.isoformat(), "source": "seed"},
        {"_id": str(_uuid.uuid4()), "name": "milk", "quantity": 1, "unit": "gallon",
         "category": "dairy", "expires_at": (now + timedelta(days=4)).isoformat(),
         "added_at": now.isoformat(), "source": "seed"},
        {"_id": str(_uuid.uuid4()), "name": "pasta", "quantity": 1, "unit": "box",
         "category": "grain", "expires_at": (now + timedelta(days=365)).isoformat(),
         "added_at": now.isoformat(), "source": "seed"},
        {"_id": str(_uuid.uuid4()), "name": "rice", "quantity": 2, "unit": "lb",
         "category": "grain", "expires_at": (now + timedelta(days=365)).isoformat(),
         "added_at": now.isoformat(), "source": "seed"},
    ]
    await mcp_insert_many("pantry_items", seed_items)
    return HTMLResponse(await _pantry_rows_html())

# ---------------------------------------------------------------------------
# Meal plan
# ---------------------------------------------------------------------------

@app.post("/plan")
async def generate_plan(days: int = 5):
    if not 1 <= days <= 14:
        raise HTTPException(status_code=400, detail="days must be 1-14")
    result = await run_plan_agent(days=days)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Health check (used for degraded-mode banner)
# ---------------------------------------------------------------------------

@app.get("/health", response_class=HTMLResponse)
async def health():
    from app.mcp_client import mcp_count
    try:
        await mcp_count("pantry_items", {})
        db_ok = True
    except Exception:
        db_ok = False
    if db_ok:
        return HTMLResponse("")  # empty — banner hidden
    return HTMLResponse(
        "<div class='db-banner'>"
        "<span class='db-banner-icon'><svg viewBox='0 0 24 24' width='16' height='16' fill='none' "
        "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
        "<path d='M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z'/>"
        "<line x1='12' y1='9' x2='12' y2='13'/><line x1='12' y1='17' x2='12.01' y2='17'/></svg></span>"
        "<span>Database is temporarily unreachable. The app is running in offline mode "
        "&mdash; some features are limited.</span>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@app.get("/metrics")
async def metrics(request: Request):
    accept = request.headers.get("accept", "")
    try:
        stats = await get_waste_stats()
    except Exception:
        stats = {"total_grams": 0.0, "total_lbs": 0.0, "items_rescued": 0}
    try:
        pantry_count = len(await read_pantry(limit=200))
    except Exception:
        pantry_count = 0
    if "application/json" not in accept:
        lbs = stats.get("total_lbs", 0.0)
        rescued = stats.get("items_rescued", 0)
        return HTMLResponse(f"<span class='impact-dot'></span> {lbs} lbs saved · {rescued} items rescued")
    return {**stats, "pantry_count": pantry_count}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import uvicorn
    from app.config import PORT
    # Reload only in local dev; Cloud Run / production must run a stable worker
    # otherwise WatchFiles cycles the process and kills in-flight requests
    # (e.g. the slow Bright Data scrape), surfacing as a 503 from the LB.
    reload = os.getenv("UVICORN_RELOAD", "0") == "1"
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=reload)
