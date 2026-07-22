import json
from typing import List, Optional

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

    async def add_ignored_channel(self, guild_id: int, log_type: str, channel_id: int) -> None:
        """Add a channel to the ignore list for a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        config = await self.get_log_channel(guild_id, log_type)
        if not config:
            raise DatabaseError(f"Log type {log_type} not configured for guild {guild_id}")

        ignored = config["ignored_channels"]
        if channel_id not in ignored:
            ignored.append(channel_id)
            await self.connection.execute(
                """UPDATE log_channels SET ignored_channels = ?
                WHERE guild_id = ? AND log_type = ?""",
                (json.dumps(ignored), guild_id, log_type),
            )
            await self.connection.commit()

    async def remove_ignored_channel(self, guild_id: int, log_type: str, channel_id: int) -> None:
        """Remove a channel from the ignore list for a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        config = await self.get_log_channel(guild_id, log_type)
        if not config:
            return

        ignored = config["ignored_channels"]
        if channel_id in ignored:
            ignored.remove(channel_id)
            await self.connection.execute(
                """UPDATE log_channels SET ignored_channels = ?
                WHERE guild_id = ? AND log_type = ?""",
                (json.dumps(ignored), guild_id, log_type),
            )
            await self.connection.commit()

    async def add_ignored_user(self, guild_id: int, log_type: str, user_id: int) -> None:
        """Add a user to the ignore list for a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        config = await self.get_log_channel(guild_id, log_type)
        if not config:
            raise DatabaseError(f"Log type {log_type} not configured for guild {guild_id}")

        ignored = config["ignored_users"]
        if user_id not in ignored:
            ignored.append(user_id)
            await self.connection.execute(
                """UPDATE log_channels SET ignored_users = ?
                WHERE guild_id = ? AND log_type = ?""",
                (json.dumps(ignored), guild_id, log_type),
            )
            await self.connection.commit()

    async def remove_ignored_user(self, guild_id: int, log_type: str, user_id: int) -> None:
        """Remove a user from the ignore list for a log type"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        config = await self.get_log_channel(guild_id, log_type)
        if not config:
            return

        ignored = config["ignored_users"]
        if user_id in ignored:
            ignored.remove(user_id)
            await self.connection.execute(
                """UPDATE log_channels SET ignored_users = ?
                WHERE guild_id = ? AND log_type = ?""",
                (json.dumps(ignored), guild_id, log_type),
            )
            await self.connection.commit()

    async def should_log_event(
        self,
        guild_id: int,
        log_type: str,
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> tuple[bool, Optional[int]]:
        """
        Check if an event should be logged and return the log channel ID

        Returns: (should_log: bool, log_channel_id: Optional[int])
        """
        config = await self.get_log_channel(guild_id, log_type)

        if not config or not config["is_enabled"] or not config["channel_id"]:
            return False, None

        # Check if channel is ignored
        if channel_id and channel_id in config["ignored_channels"]:
            return False, None

        # Check if user is ignored
        if user_id and user_id in config["ignored_users"]:
            return False, None

        return True, config["channel_id"]

    async def delete_all_log_configs(self, guild_id: int) -> None:
        """Delete all log configurations for a guild (for cleanup)"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute("DELETE FROM log_channels WHERE guild_id = ?", (guild_id,))
        await self.connection.commit()
