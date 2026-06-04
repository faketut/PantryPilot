# PantryPilot

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136-009688?logo=fastapi&logoColor=white)
![Uvicorn](https://img.shields.io/badge/Uvicorn-0.48-499848?logo=gunicorn&logoColor=white)
![Jinja](https://img.shields.io/badge/Jinja-3.1-B41717?logo=jinja&logoColor=white)
![HTMX](https://img.shields.io/badge/HTMX-2.0-3366CC?logo=htmx&logoColor=white)
![MongoDB Atlas](https://img.shields.io/badge/MongoDB_Atlas-Motor_3.7-47A248?logo=mongodb&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-2.5_Flash-4285F4?logo=googlegemini&logoColor=white)
![Google ADK](https://img.shields.io/badge/Google_ADK-2.1-4285F4?logo=google&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-1.27-000000?logo=anthropic&logoColor=white)
![Bright Data](https://img.shields.io/badge/Bright_Data-MCP-0F62FE?logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20+-339933?logo=nodedotjs&logoColor=white)
![uv](https://img.shields.io/badge/uv-package_manager-DE5FE9?logo=astral&logoColor=white)

Zero-effort food waste reduction. Import grocery purchases from any retailer via Bright Data MCP, scan receipts with Gemini Vision, and let an AI agent generate meal plans that use near-expiry items first — backed by MongoDB Atlas through the official MongoDB MCP server.

Partner integrations: **MongoDB**, **Bright Data**.

---

## Workflows

### Import items into the pantry

```mermaid
flowchart LR
    A[Grocery URL] --> B[Bright Data MCP<br/>scrape_as_markdown]
    B --> C[Gemini 2.5 Flash<br/>structured extraction]
    C --> D[(MongoDB Atlas<br/>pantry_items)]
    G[Receipt photo] --> H[Gemini Vision OCR]
    H --> D
```

### Plan meals and track waste

```mermaid
flowchart LR
    A[(pantry_items<br/>sorted by expiry)] --> B[Google ADK Agent<br/>gemini-2.5-flash]
    B --> C[Multi-day meal plan]
    B --> D[Shopping list for gaps]
    C --> E[Mark day as cooked]
    E --> F[Batch consume<br/>pantry_items]
    F --> G[(waste_saved_events)]
    G --> H[Live impact badge]
```

---

## Quick start

```bash
git clone <repo> && cd pantrpilot
cp .env.example .env                # fill in required keys
uv sync                             # install Python deps
uv run scripts/seed_db.py           # seed MongoDB fixtures
bash scripts/start.sh               # start MCP sidecar + FastAPI
```

Open `http://localhost:8000`. Smoke check: `bash scripts/smoke.sh`.

**Requirements:** Python 3.13, Node.js 20+, [uv](https://astral.sh/uv/), a MongoDB Atlas M0 cluster, and a Google AI Studio key.

---

## Configuration & integrations

Configuration lives in `.env`. Required: `GOOGLE_API_KEY` (Gemini Vision + ADK) and `MDB_MCP_CONNECTION_STRING` (Atlas). Optional: `BRIGHTDATA_API_TOKEN` (5,000 free scrapes/month — enables URL import), `MDB_TLS_ALLOW_INVALID_CERTS=true` (corporate networks with TLS inspection), `MCP_TRANSPORT` (`http` default or `stdio`), `PORT` (default `8000`). Two MCP servers run as sidecars: `mongodb-mcp-server` exposes Atlas to the ADK agent, and `@brightdata/mcp` powers cross-retailer URL scraping.
