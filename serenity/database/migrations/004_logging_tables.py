import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """Create logging tables for log channel configurations"""

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS log_channels (
            guild_id INTEGER NOT NULL,
            log_type TEXT NOT NULL,
            channel_id INTEGER,
            is_enabled INTEGER DEFAULT 1,
            ignored_channels TEXT DEFAULT '[]',
            ignored_users TEXT DEFAULT '[]',
            PRIMARY KEY (guild_id, log_type),
            CHECK (log_type IN ('member', 'message', 'voice', 'server', 'mod'))
        )
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_log_channels_guild ON log_channels(guild_id)
        """
    )

    await db.commit()


async def downgrade(db: aiosqlite.Connection) -> None:
    """Drop logging tables"""

    await db.execute("DROP TABLE IF EXISTS log_channels")
    await db.execute("DROP INDEX IF EXISTS idx_log_channels_guild")

    await db.commit()
