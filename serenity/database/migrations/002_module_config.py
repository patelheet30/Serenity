import time

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """Create module_config table"""

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS module_config (
            guild_id INTEGER NOT NULL,
            module_type TEXT NOT NULL,
            is_enabled INTEGER DEFAULT 0,
            settings TEXT DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, module_type)
        )
        """
    )

    await db.execute(
        """CREATE INDEX IF NOT EXISTS idx_module_config_guild ON module_config(guild_id)"""
    )

    # Migrate existing slowmode settings from guild_config to module_config
    await db.execute(
        """
        INSERT INTO module_config (guild_id, module_type, is_enabled, settings, updated_at)
        SELECT guild_id, 'slowmode', 1, '{}', ?
        FROM guild_config
        WHERE is_enabled = 1
        """,
        (int(time.time()),),
    )

    await db.commit()


async def downgrade(db: aiosqlite.Connection) -> None:
    """Drop module_config table"""

    await db.execute("DROP TABLE IF EXISTS module_config")
    await db.execute("DROP INDEX IF EXISTS idx_module_config_guild")
    await db.commit()
