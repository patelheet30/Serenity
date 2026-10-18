"""One-off copy of the old SQLite database into Postgres (issue #16).

    python -m serenity.database.import_sqlite /data/auto_slowmode.db

The bot runs this automatically at startup (see cutover_if_needed). The CLI commits too,
so point it at a scratch database for dry runs.

Connects with the PG* env vars (or --dsn), creates the schema if needed, and copies
every table in one transaction. Refuses to run if any target table already has rows.
Row counts are compared before commit; any mismatch rolls the whole import back.
A row in sqlite_import, committed in that same transaction, marks the import as done.
The SQLite file is opened read-only and left untouched.
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import time
from contextlib import closing
from typing import Any, Callable, Dict, Iterator, Optional

import asyncpg

from serenity.database.migrations import run_migrations
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

# Every table the bot uses. schema_version is Postgres's own and isn't copied.
TABLES = [
    "guild_config",
    "channel_config",
    "message_activity",
    "channel_patterns",
    "slowmode_changes",
    "channel_analytics",
    "slowmode_effectiveness",
    "module_config",
    "mod_cases",
    "warnings",
    "log_channels",
    "log_ignores",
]

# SQLite is loosely typed (booleans are 0/1, mod_cases.duration_sections is TEXT);
# coerce each value to the Postgres column's type so COPY accepts it.
COERCE: Dict[str, Callable[[Any], Any]] = {
    "bigint": int,
    "integer": int,
    "boolean": bool,
    "double precision": float,
    "text": str,
}


async def import_sqlite(sqlite_path: str, dsn: Optional[str] = None) -> Optional[Dict[str, int]]:
    """Copy every table; returns {table: rows copied}, or None if already imported."""
    with closing(sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)) as src:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
        try:
            await run_migrations(pool)
            async with pool.acquire() as conn, conn.transaction():
                return await _copy_all(src, conn)
        finally:
            await pool.close()


async def _copy_all(src: sqlite3.Connection, conn: asyncpg.Connection) -> Optional[Dict[str, int]]:
    # sqlite_import is only ever created, with its row, in the same transaction as the data
    if await conn.fetchval("SELECT to_regclass('sqlite_import') IS NOT NULL"):
        return None

    for table in TABLES:
        if await conn.fetchval(f"SELECT EXISTS (SELECT 1 FROM {table})"):
            raise RuntimeError(f"{table} already has rows; import only into an empty DB")

    copied: Dict[str, int] = {}
    for table in TABLES:
        columns = await conn.fetch(
            """SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = $1
            ORDER BY ordinal_position""",
            table,
        )
        names = [c["column_name"] for c in columns]
        coerce = [COERCE[c["data_type"]] for c in columns]

        await conn.copy_records_to_table(
            table, records=_coerced(src, table, names, coerce), columns=names
        )

        if "id" in names:
            # Identity columns don't advance on explicit ids; continue after the max.
            await conn.execute(
                f"""SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                                  COALESCE(MAX(id), 0) + 1, false) FROM {table}"""
            )

        expected = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        actual = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
        if actual != expected:
            raise RuntimeError(f"{table}: SQLite has {expected} rows, Postgres {actual}")
        copied[table] = actual

    await conn.execute(
        """CREATE TABLE sqlite_import (imported_at BIGINT NOT NULL, rows_copied BIGINT NOT NULL)"""
    )
    await conn.execute(
        "INSERT INTO sqlite_import VALUES ($1, $2)", int(time.time()), sum(copied.values())
    )
    return copied


def _coerced(src: sqlite3.Connection, table: str, names: list, coerce: list) -> Iterator[tuple]:
    """Stream rows from SQLite, coerced to the Postgres column types."""
    for row in src.execute(f"SELECT {', '.join(names)} FROM {table}"):
        yield tuple(None if v is None else f(v) for f, v in zip(coerce, row))


def cutover_if_needed(sqlite_path: Optional[str], dsn: Optional[str] = None) -> bool:
    """Import the old SQLite file once, then rename it so it's never imported again.

    Called by main.py before the bot connects, so nothing can write to Postgres mid-import.
    The renamed *.imported file is the rollback copy. Returns True if an import ran.
    """
    if not sqlite_path or not os.path.exists(sqlite_path):
        return False

    copied = asyncio.run(import_sqlite(sqlite_path, dsn))
    # Rename even if an earlier run committed the import but stopped before renaming.
    os.rename(sqlite_path, sqlite_path + ".imported")
    if copied is None:
        logger.info(f"{sqlite_path} was already imported; renamed it to {sqlite_path}.imported")
        return False

    logger.info(
        f"Imported {sum(copied.values())} rows from {sqlite_path} into Postgres; "
        f"kept the original as {sqlite_path}.imported"
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy the old SQLite database into Postgres.")
    parser.add_argument("sqlite_path")
    parser.add_argument("--dsn", help="Postgres DSN; defaults to the PG* env vars")
    args = parser.parse_args()

    try:
        copied = asyncio.run(import_sqlite(args.sqlite_path, args.dsn))
    except Exception as e:
        print(f"Import failed, nothing was committed: {e}", file=sys.stderr)
        sys.exit(1)

    if copied is None:
        print("This database already holds an import (sqlite_import); nothing to do.")
        return

    for table, count in copied.items():
        print(f"{table:24} {count:>8}")
    print(f"{'total':24} {sum(copied.values()):>8}  (row counts match SQLite)")


if __name__ == "__main__":
    main()
