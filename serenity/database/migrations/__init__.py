"""Plain-SQL migrations: NNN_name.sql files applied in order, each in its own transaction."""

import time
from pathlib import Path

import asyncpg

from serenity.utils.logging import get_logger

logger = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Apply every migration newer than the recorded schema version."""
    async with pool.acquire() as conn:
        await conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at BIGINT NOT NULL
            )"""
        )
        current = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM schema_version")

        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(path.stem.split("_")[0])
            if version <= current:
                continue

            logger.info(f"Running migration {version}: {path.stem}")
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES ($1, $2)",
                    version,
                    int(time.time()),
                )
