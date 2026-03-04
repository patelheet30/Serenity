import time
from typing import List, Optional

import aiosqlite

from serenity.utils.errors import DatabaseError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)


class ModerationRepository:
    """Repository for moderation-related database operations"""

    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection

    async def create_case(
        self,
        guild_id: int,
        target_user_id: int,
        moderator_id: int,
        action: str,
        reason: Optional[str] = "No reason provided.",
        duration_seconds: Optional[int] = None,
        expires_at: Optional[int] = None,
    ) -> int:
        """Create a new moderation case and return the case number"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised")

        async with self.connection.execute(
            "SELECT MAX(case_number) FROM mod_cases WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
            next_case_number = (row[0] or 0) + 1  # type: ignore

        await self.connection.execute(
            """INSERT INTO mod_cases
            (guild_id, case_number, target_user_id, moderator_id, action, reason, duration_sections, created_at, expires_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                guild_id,
                next_case_number,
                target_user_id,
                moderator_id,
                action,
                reason,
                duration_seconds,
                int(time.time()),
                expires_at,
            ),
        )

        await self.connection.commit()

        logger.info(
            f"Created mod case #{next_case_number} for user {target_user_id} in guild {guild_id} with action {action}"
        )
        return next_case_number

    async def get_case(self, guild_id: int, case_number: int) -> Optional[dict]:
        """Get a moderation case by guild ID and case number"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised")

        async with self.connection.execute(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND case_number = ?",
            (guild_id, case_number),
        ) as cursor:
            row = await cursor.fetchone()

        return dict(row) if row else None

    async def get_user_cases(self, guild_id: int, user_id: int, limit: int = 10) -> List[dict]:
        """Get moderation cases for a specific user in a guild"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised")

        async with self.connection.execute(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND target_user_id = ? ORDER BY created_at DESC LIMIT ?",
            (guild_id, user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def get_active_timeouts(self, guild_id: int) -> List[dict]:
        """Get all active timeouts in a guild"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised")

        async with self.connection.execute(
            "SELECT * FROM mod_cases WHERE guild_id = ? AND action = 'timeout' AND is_active = 1 AND expires_at > ?",
            (guild_id, int(time.time())),
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def add_warning(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        reason: str,
        case_id: Optional[int] = None,
    ) -> int:
        """Add a warning for a user"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        cursor = await self.connection.execute(
            """INSERT INTO warnings (guild_id, user_id, moderator_id, reason, case_id, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (guild_id, user_id, moderator_id, reason, case_id, int(time.time())),
        )
        await self.connection.commit()

        logger.info(f"Added warning for user {user_id} in guild {guild_id} with reason: {reason}")

        return cursor.lastrowid  # type: ignore

    async def get_active_warnings(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all active warnings for a user"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? AND is_active = 1
            ORDER BY created_at DESC""",
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def get_warning_count(self, guild_id: int, user_id: int) -> int:
        """Get count of active warnings for a user"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ? AND is_active = 1""",
            (guild_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()

        return row[0] if row else 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        """Clear all active warnings for a user, returns count cleared"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        count = await self.get_warning_count(guild_id, user_id)

        await self.connection.execute(
            """UPDATE warnings SET is_active = 0 WHERE guild_id = ? AND user_id = ? AND is_active = 1""",
            (guild_id, user_id),
        )
        await self.connection.commit()

        return count

    async def get_moderation_stats(self, guild_id: int, days: int = 30) -> dict:
        """Get moderation statistics for a guild"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        cutoff = int(time.time()) - (days * 86400)

        async with self.connection.execute(
            """SELECT action, COUNT(*) as count
            FROM mod_cases WHERE guild_id = ? AND created_at >= ? GROUP BY action""",
            (guild_id, cutoff),
        ) as cursor:
            rows = await cursor.fetchall()

        stats = {row["action"]: row["count"] for row in rows}

        async with self.connection.execute(
            """SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND created_at >= ?""",
            (guild_id, cutoff),
        ) as cursor:
            row = await cursor.fetchone()
            stats["warnings"] = row[0] if row else 0

        return stats
