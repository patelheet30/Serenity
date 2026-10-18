import time
from typing import List, Optional

import asyncpg

from serenity.utils.errors import DatabaseError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)


class ModerationRepository:
    """Repository for moderation-related database operations"""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

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
        if not self.pool:
            raise DatabaseError("Database connection is not initialised")

        async with self.pool.acquire() as conn, conn.transaction():
            # Per-guild lock so concurrent cases in one guild get consecutive numbers
            # instead of colliding on UNIQUE(guild_id, case_number). Released at commit.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", guild_id)
            next_case_number = await conn.fetchval(
                """INSERT INTO mod_cases
                (guild_id, case_number, target_user_id, moderator_id, action, reason, duration_sections, created_at, expires_at, is_active)
                SELECT $1, COALESCE(MAX(case_number), 0) + 1, $2, $3, $4, $5, $6, $7, $8, TRUE
                FROM mod_cases WHERE guild_id = $1
                RETURNING case_number""",
                guild_id,
                target_user_id,
                moderator_id,
                action,
                reason,
                duration_seconds,
                int(time.time()),
                expires_at,
            )

        logger.info(
            f"Created mod case #{next_case_number} for user {target_user_id} in guild {guild_id} with action {action}"
        )
        return next_case_number

    async def update_case_reason(
        self, guild_id: int, case_number: int, new_reason: str
    ) -> None:
        """Update the reason for a case"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        await self.pool.execute(
            "UPDATE mod_cases SET reason = $1 WHERE guild_id = $2 AND case_number = $3",
            new_reason,
            guild_id,
            case_number,
        )

    async def deactivate_case(self, guild_id: int, case_number: int) -> None:
        """Mark a case as inactive."""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        await self.pool.execute(
            "UPDATE mod_cases SET is_active = FALSE WHERE guild_id = $1 AND case_number = $2",
            guild_id,
            case_number,
        )

    async def get_case(self, guild_id: int, case_number: int) -> Optional[dict]:
        """Get a moderation case by guild ID and case number"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised")

        row = await self.pool.fetchrow(
            "SELECT * FROM mod_cases WHERE guild_id = $1 AND case_number = $2",
            guild_id,
            case_number,
        )

        return dict(row) if row else None

    async def get_user_cases(self, guild_id: int, user_id: int, limit: int = 10) -> List[dict]:
        """Get moderation cases for a specific user in a guild"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised")

        rows = await self.pool.fetch(
            "SELECT * FROM mod_cases WHERE guild_id = $1 AND target_user_id = $2 ORDER BY created_at DESC LIMIT $3",
            guild_id,
            user_id,
            limit,
        )

        return [dict(row) for row in rows]

    async def get_active_timeouts(self, guild_id: int) -> List[dict]:
        """Get all active timeouts in a guild"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised")

        rows = await self.pool.fetch(
            "SELECT * FROM mod_cases WHERE guild_id = $1 AND action = 'timeout' AND is_active AND expires_at > $2",
            guild_id,
            int(time.time()),
        )

        return [dict(row) for row in rows]

    async def get_all_expired_timeouts(self) -> list[dict]:
        """Get all timeout cases that have expired but are still marked active.

        Used by the background task to keep is_active in sync with reality.
        """
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        rows = await self.pool.fetch(
            """SELECT * FROM mod_cases
            WHERE action = 'timeout'
            AND is_active
            AND expires_at IS NOT NULL
            AND expires_at < $1""",
            int(time.time()),
        )

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
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        warning_id = await self.pool.fetchval(
            """INSERT INTO warnings (guild_id, user_id, moderator_id, reason, case_id, created_at, is_active)
            VALUES ($1, $2, $3, $4, $5, $6, TRUE)
            RETURNING id""",
            guild_id,
            user_id,
            moderator_id,
            reason,
            case_id,
            int(time.time()),
        )

        logger.info(f"Added warning for user {user_id} in guild {guild_id} with reason: {reason}")

        return warning_id

    async def get_active_warnings(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all active warnings for a user"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        rows = await self.pool.fetch(
            """SELECT * FROM warnings WHERE guild_id = $1 AND user_id = $2 AND is_active
            ORDER BY created_at DESC""",
            guild_id,
            user_id,
        )

        return [dict(row) for row in rows]

    async def get_warning_count(self, guild_id: int, user_id: int) -> int:
        """Get count of active warnings for a user"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        return await self.pool.fetchval(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = $1 AND user_id = $2 AND is_active",
            guild_id,
            user_id,
        )

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        """Clear all active warnings for a user. Returns count cleared."""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        async with self.pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                """SELECT case_id FROM warnings
                WHERE guild_id = $1 AND user_id = $2 AND is_active AND case_id IS NOT NULL""",
                guild_id,
                user_id,
            )

            case_numbers = [row["case_id"] for row in rows]

            status = await conn.execute(
                """UPDATE warnings SET is_active = FALSE
                WHERE guild_id = $1 AND user_id = $2 AND is_active""",
                guild_id,
                user_id,
            )
            count = len(case_numbers) or int(status.split()[-1])  # status is e.g. "UPDATE 2"

            await conn.execute(
                """UPDATE mod_cases SET is_active = FALSE
                WHERE guild_id = $1 AND case_number = ANY($2::int[])""",
                guild_id,
                case_numbers,
            )

        return count

    async def get_moderation_stats(self, guild_id: int, days: int = 30) -> dict:
        """Get moderation statistics for a guild"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        cutoff = int(time.time()) - (days * 86400)

        rows = await self.pool.fetch(
            """SELECT action, COUNT(*) as count
            FROM mod_cases WHERE guild_id = $1 AND created_at >= $2 GROUP BY action""",
            guild_id,
            cutoff,
        )

        stats = {row["action"]: row["count"] for row in rows}

        stats["warnings"] = await self.pool.fetchval(
            "SELECT COUNT(*) FROM warnings WHERE guild_id = $1 AND created_at >= $2",
            guild_id,
            cutoff,
        )

        return stats
