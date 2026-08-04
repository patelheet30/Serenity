import json
from typing import List, Optional
import time

import aiosqlite

from serenity.utils.errors import DatabaseError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)


class LoggingRepository:
    """Handles database operations related to log channel configurations"""

    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection

    async def get_log_channel(self, guild_id: int, log_type: str) -> Optional[dict]:
        """Get log channel configuration for a specific log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT * FROM log_channels WHERE guild_id = ? AND log_type = ?""",
            (guild_id, log_type),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            return None

        return {
            "guild_id": row["guild_id"],
            "log_type": row["log_type"],
            "channel_id": row["channel_id"],
            "is_enabled": bool(row["is_enabled"]),
            "ignored_channels": json.loads(row["ignored_channels"])
            if row["ignored_channels"]
            else [],
            "ignored_users": json.loads(row["ignored_users"]) if row["ignored_users"] else [],
        }

    async def set_log_channel(
        self, guild_id: int, log_type: str, channel_id: int, is_enabled: bool = True
    ) -> None:
        """Set or update log channel configuration for a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute(
            """
            INSERT INTO log_channels (guild_id, log_type, channel_id, is_enabled, ignored_channels, ignored_users)
            VALUES (?, ?, ?, ?, '[]', '[]')
            ON CONFLICT(guild_id, log_type) DO UPDATE SET
                channel_id = ?,
                is_enabled = ?
            """,
            (guild_id, log_type, channel_id, int(is_enabled), channel_id, int(is_enabled)),
        )
        await self.connection.commit()

        logger.info(
            f"Set log channel for guild {guild_id}, log type {log_type} to channel {channel_id} (enabled: {is_enabled})"
        )

    async def enable_log_channel(self, guild_id: int, log_type: str) -> None:
        """Enable a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute(
            """UPDATE log_channels SET is_enabled = 1 WHERE guild_id = ? AND log_type = ?""",
            (guild_id, log_type),
        )
        await self.connection.commit()

    async def disable_log_channel(self, guild_id: int, log_type: str) -> None:
        """Disable a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute(
            """UPDATE log_channels SET is_enabled = 0 WHERE guild_id = ? AND log_type = ?""",
            (guild_id, log_type),
        )
        await self.connection.commit()

    async def get_all_log_channels(self, guild_id: int) -> List[dict]:
        """Get all log channel configurations for a guild"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            "SELECT * FROM log_channels WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()

        configs = []
        for row in rows:
            configs.append(
                {
                    "guild_id": row["guild_id"],
                    "log_type": row["log_type"],
                    "channel_id": row["channel_id"],
                    "is_enabled": bool(row["is_enabled"]),
                    "ignored_channels": json.loads(row["ignored_channels"])
                    if row["ignored_channels"]
                    else [],
                    "ignored_users": json.loads(row["ignored_users"])
                    if row["ignored_users"]
                    else [],
                }
            )

        return configs

    async def is_ignored(
        self,
        guild_id: int,
        target_type: str,
        target_id: int,
        log_type: str,
    ) -> bool:
        """Check whether a user/channel is ignored for this log type (or globally)."""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT 1 FROM log_ignores
               WHERE guild_id = ? AND target_type = ? AND target_id = ?
                 AND log_type IN (?, '*')
               LIMIT 1""",
            (guild_id, target_type, target_id, log_type),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def add_ignore(
        self,
        guild_id: int,
        target_type: str,
        target_id: int,
        log_type: str = "*",
    ) -> bool:
        """Ignore a user/channel. Returns False if it was already ignored."""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        already = await self.is_ignored(guild_id, target_type, target_id, log_type)

        # A global ignore supersedes any per-type rows for the same target.
        if log_type == "*":
            await self.connection.execute(
                """DELETE FROM log_ignores
                   WHERE guild_id = ? AND target_type = ? AND target_id = ?""",
                (guild_id, target_type, target_id),
            )

        await self.connection.execute(
            """INSERT OR IGNORE INTO log_ignores
               (guild_id, target_type, target_id, log_type, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (guild_id, target_type, target_id, log_type, int(time.time())),
        )
        await self.connection.commit()
        return not already

    async def remove_ignore(
        self,
        guild_id: int,
        target_type: str,
        target_id: int,
        log_type: Optional[str] = None,
    ) -> int:
        """Un-ignore a user/channel. log_type=None removes every ignore for that target.

        Returns the number of rows removed.
        """
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        if log_type is None:
            cursor = await self.connection.execute(
                """DELETE FROM log_ignores
                   WHERE guild_id = ? AND target_type = ? AND target_id = ?""",
                (guild_id, target_type, target_id),
            )
        else:
            cursor = await self.connection.execute(
                """DELETE FROM log_ignores
                   WHERE guild_id = ? AND target_type = ? AND target_id = ? AND log_type = ?""",
                (guild_id, target_type, target_id, log_type),
            )

        await self.connection.commit()
        return cursor.rowcount

    async def list_ignores(self, guild_id: int) -> list[dict]:
        """All ignore entries for a guild, for /logging ignores."""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT target_type, target_id, log_type FROM log_ignores
               WHERE guild_id = ?
               ORDER BY target_type, log_type, created_at""",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def should_log_event(
        self,
        guild_id: int,
        log_type: str,
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> tuple[bool, Optional[int]]:
        config = await self.get_log_channel(guild_id, log_type)

        if not config or not config["is_enabled"] or not config["channel_id"]:
            return False, None

        if channel_id is not None and await self.is_ignored(
            guild_id, "channel", channel_id, log_type
        ):
            return False, None

        if user_id is not None and await self.is_ignored(guild_id, "user", user_id, log_type):
            return False, None

        return True, config["channel_id"]

    async def delete_all_log_configs(self, guild_id: int) -> None:
        """Delete all log configurations for a guild (for cleanup)"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute("DELETE FROM log_channels WHERE guild_id = ?", (guild_id,))
        await self.connection.commit()
