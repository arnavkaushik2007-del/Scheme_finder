"""Seed MongoDB with synthetic demo data.

    python -m scripts.seed            # uses MONGO_URL / MONGO_DB from the environment or .env
"""

from __future__ import annotations

import asyncio

from app import db as database
from app.config import get_settings
from app.logging_conf import configure_logging
from app.seed import seed


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.uses_mock_db:
        raise SystemExit("MONGO_URL is mongomock:// (in-memory). Use SEED_ON_STARTUP=true instead.")
    db = database.connect(settings)
    await database.ensure_indexes(db)
    counts = await seed(db)
    print(f"Seeded: {counts}")
    database.close()


if __name__ == "__main__":
    asyncio.run(main())
