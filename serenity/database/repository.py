import time
from pathlib import Path
from typing import List, Optional

import aiosqlite

from serenity.core.constants import DATABASE_CONFIG
from serenity.core.types import ChannelConfig, GuildConfig
from serenity.database.migrations import MigrationManager
from serenity.utils.errors import DatabaseError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)


class Repository:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DATABASE_CONFIG.DEFAULT_PATH
        self.connection: Optional[aiosqlite.Connection] = None

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        """Initialise the database connection and run migrations"""
        self.connection = await aiosqlite.connect(self.db_path)
        self.connection.row_factory = aiosqlite.Row

        migration_manager = MigrationManager(self.db_path)
        await migration_manager.run_migrations_with_connection(self.connection)

        logger.info("Database initialised and connected.")

    async def close(self) -> None:
        """Close the database connection"""
        if self.connection:
            await self.connection.close()
            logger.info("Database connection closed.")

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        """Get the configuration for a guild, creating a default if not found"""
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
            guild_id=guild_id, is_enabled=True, default_threshold=10, update_interval=30
        )

    async def update_guild_config(
        self,
        guild_id: int,
        is_enabled: Optional[bool] = None,
        default_threshold: Optional[int] = None,
        update_interval: Optional[int] = None,
    ) -> None:
        """Update the configuration for a guild"""
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
        """Get the configuration for a channel, creating a default if not found"""
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
        """Get all enabled channels for a guild"""
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
        """Update the configuration for a channel"""
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
        """Record a message activity for a channel at a given timestamp"""
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
        """Get the message rate (messages per minute) for a channel over a time window"""
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
        """Remove message activity records older than specified hours"""
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
        """Record a slowmode change event"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute(
            """INSERT INTO slowmode_changes
            (channel_id, old_value, new_value, reason, message_rate, confidence, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, old_value, new_value, reason, message_rate, confidence, int(time.time())),
        )
        await self.connection.commit()

    async def get_expected_activity(
        self, channel_id: int, day_of_week: int, hour: int
    ) -> Optional[float]:
        """Get the expected message rate for a channel at a specific day and hour"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT avg_message_rate, stddev_message_rate, sample_count FROM channel_patterns
            WHERE channel_id = ? AND day_of_week = ? AND hour = ?""",
            (channel_id, day_of_week, hour),
        ) as cursor:
            row = await cursor.fetchone()

        if row and row["sample_count"] >= 10:
            return row["avg_message_rate"]

        return None

    async def update_channel_pattern(
        self,
        channel_id: int,
        day_of_week: int,
        hour: int,
        avg_rate: float,
        stddev_rate: float,
        sample_count: int,
    ) -> None:
        """Update the channel pattern analytics"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        await self.connection.execute(
            """INSERT INTO channel_patterns
            (channel_id, day_of_week, hour, avg_message_rate, stddev_message_rate, sample_count, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, day_of_week, hour) DO UPDATE SET
            avg_message_rate = ?,
            stddev_message_rate = ?,
            sample_count = ?,
            last_updated = ?""",
            (
                channel_id,
                day_of_week,
                hour,
                avg_rate,
                stddev_rate,
                sample_count,
                int(time.time()),
                avg_rate,
                stddev_rate,
                sample_count,
                int(time.time()),
            ),
        )
        await self.connection.commit()

    async def record_slowmode_effectiveness(
        self,
        channel_id: int,
        slowmode_value: int,
        rate_before: float,
        rate_after: float,
        duration: int,
    ) -> None:
        """Record the effectiveness of a slowmode change"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        was_effective = (rate_after < rate_before * 0.8) if rate_before > 0 else False

        await self.connection.execute(
            """INSERT INTO slowmode_effectiveness
            (channel_id, applied_at, slowmode_value, message_rate_before, message_rate_after, duration_seconds, was_effective)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                int(time.time()),
                slowmode_value,
                rate_before,
                rate_after,
                duration,
                int(was_effective),
            ),
        )

        await self.connection.commit()

    async def get_effectiveness_score(self, channel_id: int) -> float:
        """Get the effectiveness score of slowmode changes for a channel"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        async with self.connection.execute(
            """SELECT AVG(CAST(was_effective AS FLOAT)) as score FROM slowmode_effectiveness
            WHERE channel_id = ? AND applied_at >= ?""",
            (channel_id, int(time.time()) - (30 * 86400)),
        ) as cursor:
            row = await cursor.fetchone()

        return row["score"] if row and row["score"] is not None else 0.0

    async def aggregate_hourly_analytics(self, channel_id: int) -> None:
        """Aggregate message activity into hourly analytics for a channel"""
        if not self.connection:
            raise DatabaseError("Database connection is not initialised.")

        hour_timestamp = int(time.time() // 3600) * 3600
        start_time = hour_timestamp - 3600

        async with self.connection.execute(
            """SELECT SUM(message_count) as total, COUNT(DISTINCT timestamp) as samples
            FROM message_activity
            WHERE channel_id = ? AND timestamp >= ? AND timestamp < ?""",
            (channel_id, start_time, hour_timestamp),
        ) as cursor:
            row = await cursor.fetchone()

        if row and row["total"]:
            await self.connection.execute(
                """INSERT INTO channel_analytics
                (channel_id, hour_timestamp, total_messages, unique_users, avg_slowmode, max_slowmode)
                VALUES (?, ?, ?, 0, 0, 0)
                ON CONFLICT(channel_id, hour_timestamp) DO UPDATE SET
                total_messages = ?""",
                (
                    channel_id,
                    hour_timestamp,
                    row["total"],
                    row["total"],
                ),
            )
        await self.connection.commit()

    async def get_channel_analytics(self, channel_id: int, hours_back: int = 24) -> List[dict]:
        """Get aggregated analytics for a channel"""
        if not self.connection:
            raise DatabaseError("Database not initialized")

        cutoff = int(time.time()) - (hours_back * 3600)

        async with self.connection.execute(
            """SELECT * FROM channel_analytics
               WHERE channel_id = ? AND hour_timestamp >= ?
               ORDER BY hour_timestamp DESC""",
            (channel_id, cutoff),
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(row) for row in rows]

    async def cleanup_old_analytics(self, days: int = 30) -> None:
        """Remove analytics older than specified days"""
        if not self.connection:
            raise DatabaseError("Database not initialized")

        cutoff = int(time.time()) - (days * 86400)

        await self.connection.execute(
            "DELETE FROM channel_analytics WHERE hour_timestamp < ?", (cutoff,)
        )
        await self.connection.execute(
            "DELETE FROM slowmode_effectiveness WHERE applied_at < ?", (cutoff,)
        )
        await self.connection.commit()
