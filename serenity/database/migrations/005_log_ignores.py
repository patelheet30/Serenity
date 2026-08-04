"""Migration 005: move logging ignore lists out of JSON columns into their own table.

Previously ignores lived in `log_channels.ignored_channels` / `ignored_users` as JSON
arrays keyed by (guild_id, log_type). That made "ignore this user" a per-log-type
operation and made it impossible to ignore anything for a log type that hadn't been
configured yet.

`log_ignores` stores one row per (guild, target, log_type), where log_type = '*'
means "every log type".
"""

import json
import time

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS log_ignores (
            guild_id    INTEGER NOT NULL,
            target_type TEXT    NOT NULL,
            target_id   INTEGER NOT NULL,
            log_type    TEXT    NOT NULL DEFAULT '*',
            created_at  INTEGER NOT NULL,
            PRIMARY KEY (guild_id, target_type, target_id, log_type),
            CHECK (target_type IN ('user', 'channel')),
            CHECK (log_type IN ('*', 'member', 'message', 'voice', 'server', 'mod'))
        )
        """
    )

    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_log_ignores_lookup ON log_ignores(guild_id, target_type, target_id)"
    )

    # Backfill from the old JSON columns so existing configs keep working.
    now = int(time.time())

    async with db.execute(
        "SELECT guild_id, log_type, ignored_channels, ignored_users FROM log_channels"
    ) as cursor:
        rows = await cursor.fetchall()

    for row in rows:
        guild_id, log_type = row[0], row[1]

        for target_type, raw in (("channel", row[2]), ("user", row[3])):
            try:
                target_ids = json.loads(raw or "[]")
            except (TypeError, ValueError):
                continue

            for target_id in target_ids:
                await db.execute(
                    """INSERT OR IGNORE INTO log_ignores
                       (guild_id, target_type, target_id, log_type, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (guild_id, target_type, int(target_id), log_type, now),
                )

    await db.commit()


async def downgrade(db: aiosqlite.Connection) -> None:
    await db.execute("DROP INDEX IF EXISTS idx_log_ignores_lookup")
    await db.execute("DROP TABLE IF EXISTS log_ignores")
    await db.commit()
