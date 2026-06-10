"""
One-time seed script: creates MongoDB collections with indexes and inserts fixtures.

Usage:
  uv run scripts/seed_db.py
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import DB_NAME, MDB_MCP_CONNECTION_STRING, MDB_TLS_ALLOW_INVALID_CERTS


async def seed():
    kwargs = {"tls": True, "tlsInsecure": True} if MDB_TLS_ALLOW_INVALID_CERTS else {}
    client = AsyncIOMotorClient(MDB_MCP_CONNECTION_STRING, **kwargs)
    db = client[DB_NAME]

    print(f"Connected to MongoDB. Seeding database '{DB_NAME}'...")

    # ---- pantry_items (seed with demo near-expiry items) ---------------
    await db["pantry_items"].drop()
    now = datetime.now(timezone.utc)
    demo_pantry = [
        {
            "_id": str(uuid.uuid4()),
            "name": "spinach",
            "quantity": 1,
            "unit": "bag",
            "category": "produce",
            "expires_at": (now + timedelta(days=1)).isoformat(),  # expires TOMORROW — demo hook
            "added_at": now.isoformat(),
            "source": "seed",
        },
        {
            "_id": str(uuid.uuid4()),
            "name": "chicken breast",
            "quantity": 2,
            "unit": "lb",
            "category": "meat",
            "expires_at": (now + timedelta(days=2)).isoformat(),
            "added_at": now.isoformat(),
            "source": "seed",
        },
        {
            "_id": str(uuid.uuid4()),
            "name": "milk",
            "quantity": 1,
            "unit": "gallon",
            "category": "dairy",
            "expires_at": (now + timedelta(days=4)).isoformat(),
            "added_at": now.isoformat(),
            "source": "seed",
        },
        {
            "_id": str(uuid.uuid4()),
            "name": "pasta",
            "quantity": 1,
            "unit": "box",
            "category": "grain",
            "expires_at": (now + timedelta(days=365)).isoformat(),
            "added_at": now.isoformat(),
            "source": "seed",
        },
        {
            "_id": str(uuid.uuid4()),
            "name": "rice",
            "quantity": 2,
            "unit": "lb",
            "category": "grain",
            "expires_at": (now + timedelta(days=365)).isoformat(),
            "added_at": now.isoformat(),
            "source": "seed",
        },
    ]
    await db["pantry_items"].insert_many(demo_pantry)
    await db["pantry_items"].create_index([("expires_at", 1)])
    print(f"  pantry_items: inserted {len(demo_pantry)} demo items (spinach expires tomorrow!)")

    # ---- meal_plans ----------------------------------------------------
    await db["meal_plans"].drop()
    await db["meal_plans"].create_index([("created_at", -1)])
    print("  meal_plans: collection ready (empty)")

    # ---- waste_saved_events --------------------------------------------
    await db["waste_saved_events"].drop()
    await db["waste_saved_events"].create_index([("created_at", -1)])
    print("  waste_saved_events: collection ready (empty)")

    # ---- expiry_learned (Gemini-derived shelf-life cache) --------------
    # Do NOT drop — this is a learned-knowledge cache that should accumulate
    # across deploys. Just ensure the collection + index exist.
    await db["expiry_learned"].create_index([("name", 1), ("category", 1)])
    print("  expiry_learned: collection ready (preserved across runs)")

    client.close()
    print("\nSeeding complete. Run 'bash scripts/start.sh' to start the app.")


if __name__ == "__main__":
    asyncio.run(seed())
