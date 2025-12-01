from pathlib import Path

import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """Create initial schema"""

    migrations_dir = Path(__file__).parent
    schema_path = migrations_dir.parent / "schema.sql"

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_path}")

    with open(schema_path, "r") as f:
        schema = f.read()

    await db.executescript(schema)
    await db.commit()


async def downgrade(db: aiosqlite.Connection) -> None:
    """Drop all tables"""
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
