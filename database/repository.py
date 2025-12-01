import time
from pathlib import Path
from typing import List, Optional

import aiosqlite

from core.constants import DATABASE_CONFIG
from core.types import ChannelConfig, GuildConfig
from database.migrations import MigrationManager
from utils.errors import DatabaseError
from utils.logging import get_logger

logger = get_logger(__name__)


class Repository:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DATABASE_CONFIG.DEFAULT_PATH
        self.connection: Optional[aiosqlite.Connection] = None

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        migration_manager = MigrationManager(self.db_path)
        await migration_manager.run_migrations()

        self.connection = await aiosqlite.connect(self.db_path)
        self.connection.row_factory = aiosqlite.Row

        logger.info("Database initialised and connected.")

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()
            logger.info("Database connection closed.")

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            return GuildConfig(
                guild_id=row["guild_id"],
                is_enabled=bool(row["is_enabled"]),
                default_threshold=row["default_threshold"],
                update_interval=row["update_interval"],
            )

        await self.connection.execute(
            """INSERT INTO guild_config (guild_id) VALUES (?)""", (guild_id,)
        )
        await self.connection.commit()

        return GuildConfig(
            guild_id=guild_id, is_enabled=False, default_threshold=10, update_interval=30
        )

    async def update_guild_config(
        self,
        guild_id: int,
        is_enabled: Optional[bool] = None,
        default_threshold: Optional[int] = None,
        update_interval: Optional[int] = None,
    ) -> None:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        updates = []
        params = []

        if is_enabled is not None:
            updates.append("is_enabled = ?")
            params.append(int(is_enabled))
        if default_threshold is not None:
            updates.append("default_threshold = ?")
            params.append(default_threshold)
        if update_interval is not None:
            updates.append("update_interval = ?")
            params.append(update_interval)

        if not updates:
            return

        params.append(guild_id)

        await self.connection.execute(
            f"UPDATE guild_config SET {', '.join(updates)} WHERE guild_id = ?", params
        )
        await self.connection.commit()

    async def get_channel_config(self, channel_id: int, guild_id: int) -> ChannelConfig:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            "SELECT * FROM channel_config WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            return ChannelConfig(
                channel_id=row["channel_id"],
                guild_id=row["guild_id"],
                is_enabled=bool(row["is_enabled"]),
                threshold=row["threshold"],
            )

        await self.connection.execute(
            """INSERT INTO channel_config (channel_id, guild_id) VALUES (?, ?)""",
            (channel_id, guild_id),
        )
        await self.connection.commit()

        return ChannelConfig(
            channel_id=channel_id, guild_id=guild_id, is_enabled=True, threshold=None
        )

    async def get_enabled_channels(self, guild_id: int) -> List[int]:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT channel_id FROM channel_config
            WHERE guild_id = ? AND is_enabled = 1""",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        return [row["channel_id"] for row in rows]

    async def update_channel_config(
        self,
        channel_id: int,
        is_enabled: Optional[bool] = None,
        threshold: Optional[int] = None,
    ) -> None:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        updates = []
        params = []

        if is_enabled is not None:
            updates.append("is_enabled = ?")
            params.append(int(is_enabled))
        if threshold is not None:
            updates.append("threshold = ?")
            params.append(threshold)

        if not updates:
            return

        params.append(channel_id)

        await self.connection.execute(
            f"UPDATE channel_config SET {', '.join(updates)} WHERE channel_id = ?", params
        )
        await self.connection.commit()

    async def record_message_activity(
        self, channel_id: int, timestamp: Optional[int] = None
    ) -> None:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        if timestamp is None:
            timestamp = int(time.time())

        await self.connection.execute(
            """INSERT INTO message_activity (channel_id, timestamp, message_count)
            VALUES (?, ?, 1)
            ON CONFLICT(channel_id, timestamp) DO UPDATE SET
            message_count = message_count + 1""",
            (channel_id, timestamp),
        )
        await self.connection.commit()

    async def get_message_rate(self, channel_id: int, window_seconds: int = 60) -> float:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        cutoff = int(time.time()) - window_seconds

        async with self.connection.execute(
            """SELECT SUM(message_count) as total FROM message_activity
            WHERE channel_id = ? AND timestamp >= ?""",
            (channel_id, cutoff),
        ) as cursor:
            row = await cursor.fetchone()

        total = row["total"] if row and row["total"] else 0
        return (total / window_seconds) * 60  # messages per minute

    async def cleanup_old_message_activity(self, hours: int = 24) -> None:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        cutoff = int(time.time()) - (hours * 3600)

        await self.connection.execute("DELETE FROM message_activity WHERE timestamp < ?", (cutoff,))
        await self.connection.commit()

    async def record_slowmode_change(
        self,
        channel_id: int,
        old_value: int,
        new_value: int,
        reason: str,
        message_rate: float,
        confidence: float,
    ) -> None:
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute(
            """INSERT INTO slowmode_changes
            (channel_id, old_value, new_value, reason, message_rate, confidence, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, old_value, new_value, reason, message_rate, confidence, int(time.time())),
        )
        await self.connection.commit()
