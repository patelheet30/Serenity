from pathlib import Path

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """Create initial database schema."""
    schema_path = Path(__file__).parent.parent / "schema.sql"
    with open(schema_path) as f:
        schema_sql = f.read()

    await db.executescript(schema_sql)
    await db.commit()


async def downgrade(db: aiosqlite.Connection) -> None:
    """Drop all tables created in the initial schema."""
    tables = [
        "guild_config",
        "channel_config",
        "message_activity",
        "channel_patterns",
        "slowmode_changes",
        "channel_analytics",
        "slowmode_effectiveness",
    ]
    for table in tables:
        await db.execute(f"DROP TABLE IF EXISTS {table}")
    await db.commit()
