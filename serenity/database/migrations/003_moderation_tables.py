import aiosqlite


async def upgrade(db: aiosqlite.Connection) -> None:
    """Create moderation tables for mod cases and warnings"""

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            case_number INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            duration_sections TEXT,
            created_at INTEGER NOT NULL,
            expires_at INTEGER,
            is_active INTEGER DEFAULT 1,
            UNIQUE(guild_id, case_number)
        )
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mod_cases_guild ON mod_cases(guild_id)
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mod_cases_target ON mod_cases(guild_id, target_user_id)
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_mod_cases_created ON mod_cases(guild_id, created_at)
        """
    )

    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL,
            reason TEXT,
            case_id INTEGER,
            created_at INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (case_id) REFERENCES mod_cases(id)
        )
        """
    )

    await db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(guild_id, user_id)
        """
    )

    await db.commit()


async def downgrade(db: aiosqlite.Connection) -> None:
    """Drop moderation tables"""

    await db.execute("DROP TABLE IF EXISTS warnings")
    await db.execute("DROP TABLE IF EXISTS mod_cases")

    await db.execute("DROP INDEX IF EXISTS idx_mod_cases_guild")
    await db.execute("DROP INDEX IF EXISTS idx_mod_cases_target")
    await db.execute("DROP INDEX IF EXISTS idx_mod_cases_created")
    await db.execute("DROP INDEX IF EXISTS idx_warnings_user")

    await db.commit()
