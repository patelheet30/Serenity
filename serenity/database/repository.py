import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import asyncpg

from serenity.core.constants import DATABASE_CONFIG
from serenity.core.types import ChannelConfig, GuildConfig
from serenity.database.migrations import run_migrations
from serenity.utils.errors import DatabaseError
from serenity.utils.logging import get_logger

if TYPE_CHECKING:
    from serenity.core.modules import ModuleConfig, ModuleType
else:
    ModuleConfig = Any
    ModuleType = Any

logger = get_logger(__name__)


class Repository:
    def __init__(self, dsn: Optional[str] = None, pool_size: int = DATABASE_CONFIG.POOL_SIZE):
        # dsn=None makes asyncpg read PGHOST/PGUSER/PGPASSWORD/PGDATABASE from the env
        self.dsn = dsn
        self.pool_size = pool_size
        self.pool: Optional[asyncpg.Pool] = None

        # Message counts not yet written: (channel_id, timestamp) -> count.
        # Capped so a long DB outage can't exhaust memory; see record_message_activity.
        self._activity: Dict[tuple[int, int], int] = {}
        self._activity_max_keys = DATABASE_CONFIG.ACTIVITY_MAX_BUFFERED_KEYS
        self._flush_task: Optional[asyncio.Task] = None
        # Held for the whole write, so a reader that flushes waits for an in-flight batch.
        self._flush_lock = asyncio.Lock()

    async def init(self) -> None:
        """Create the connection pool, run migrations and start the activity flusher"""
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=self.pool_size)
        await run_migrations(self.pool)
        self._flush_task = asyncio.create_task(self._flush_loop())

        logger.info("Database initialised and connected.")

    async def close(self) -> None:
        """Flush buffered activity and close the connection pool"""
        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task

        if self.pool:
            try:
                await self.flush_message_activity()
            except Exception:
                logger.exception(
                    f"Final message activity flush failed; dropping {len(self._activity)} buffered counts."
                )
            finally:
                await self.pool.close()
            logger.info("Database connection closed.")

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(DATABASE_CONFIG.ACTIVITY_FLUSH_INTERVAL)
            try:
                await self.flush_message_activity()
            except Exception:
                logger.exception("Failed to flush message activity; will retry.")

    async def flush_message_activity(self) -> None:
        """Write buffered message counts in one batched upsert.

        Anything that reads message_activity calls this first, so reads see every message.
        """
        async with self._flush_lock:
            if not self._activity or not self.pool:
                return

            batch, self._activity = self._activity, {}

            try:
                await self.pool.execute(
                    """INSERT INTO message_activity (channel_id, timestamp, message_count)
                    SELECT * FROM unnest($1::bigint[], $2::bigint[], $3::int[])
                    ON CONFLICT (channel_id, timestamp) DO UPDATE SET
                    message_count = message_activity.message_count + EXCLUDED.message_count""",
                    [channel_id for channel_id, _ in batch],
                    [timestamp for _, timestamp in batch],
                    list(batch.values()),
                )
            except BaseException:
                # Put the counts back so the next flush retries them. BaseException so a
                # cancelled flush (e.g. close() stopping the timer mid-write) keeps them too.
                for key, count in batch.items():
                    self._activity[key] = self._activity.get(key, 0) + count
                raise

    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        """Get the configuration for a guild, creating a default if not found"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        row = await self.pool.fetchrow("SELECT * FROM guild_config WHERE guild_id = $1", guild_id)

        if row:
            return GuildConfig(
                guild_id=row["guild_id"],
                is_enabled=row["is_enabled"],
                default_threshold=row["default_threshold"],
                update_interval=row["update_interval"],
            )

        await self.pool.execute(
            "INSERT INTO guild_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING", guild_id
        )
        logger.info(f"Created default channel config for guild {guild_id}.")

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
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        updates = []
        params: List[Any] = []

        if is_enabled is not None:
            params.append(is_enabled)
            updates.append(f"is_enabled = ${len(params)}")
        if default_threshold is not None:
            params.append(default_threshold)
            updates.append(f"default_threshold = ${len(params)}")
        if update_interval is not None:
            params.append(update_interval)
            updates.append(f"update_interval = ${len(params)}")

        if not updates:
            return

        params.append(guild_id)

        await self.pool.execute(
            f"UPDATE guild_config SET {', '.join(updates)} WHERE guild_id = ${len(params)}",
            *params,
        )

    async def get_channel_config(self, channel_id: int, guild_id: int) -> ChannelConfig:
        """Get the configuration for a channel, creating a default if not found"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        row = await self.pool.fetchrow(
            "SELECT * FROM channel_config WHERE channel_id = $1", channel_id
        )

        if row:
            return ChannelConfig(
                channel_id=row["channel_id"],
                guild_id=row["guild_id"],
                is_enabled=row["is_enabled"],
                threshold=row["threshold"],
            )

        await self.pool.execute(
            """INSERT INTO channel_config (channel_id, guild_id) VALUES ($1, $2)
            ON CONFLICT DO NOTHING""",
            channel_id,
            guild_id,
        )
        logger.info(f"Created default channel config for channel {channel_id}.")

        return ChannelConfig(
            channel_id=channel_id, guild_id=guild_id, is_enabled=True, threshold=None
        )

    async def get_enabled_channels(self, guild_id: int) -> List[int]:
        """Get all enabled channels for a guild"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        rows = await self.pool.fetch(
            "SELECT channel_id FROM channel_config WHERE guild_id = $1 AND is_enabled",
            guild_id,
        )

        return [row["channel_id"] for row in rows]

    async def update_channel_config(
        self,
        channel_id: int,
        is_enabled: Optional[bool] = None,
        threshold: Optional[int] = None,
    ) -> None:
        """Update the configuration for a channel"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        updates = []
        params: List[Any] = []

        if is_enabled is not None:
            params.append(is_enabled)
            updates.append(f"is_enabled = ${len(params)}")
        if threshold is not None:
            params.append(threshold)
            updates.append(f"threshold = ${len(params)}")

        if not updates:
            return

        params.append(channel_id)

        await self.pool.execute(
            f"UPDATE channel_config SET {', '.join(updates)} WHERE channel_id = ${len(params)}",
            *params,
        )

    async def record_message_activity(
        self, channel_id: int, timestamp: Optional[int] = None
    ) -> None:
        """Count a message for a channel; written to the DB on the next flush"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        if timestamp is None:
            timestamp = int(time.time())

        key = (channel_id, timestamp)
        if key not in self._activity and len(self._activity) >= self._activity_max_keys:
            # Only reachable after the DB has been down a while (the flush loop logs that).
            # Drop the oldest second: slowmode only looks back 15 minutes anyway.
            del self._activity[next(iter(self._activity))]
        self._activity[key] = self._activity.get(key, 0) + 1

    async def get_message_rate(self, channel_id: int, window_seconds: int = 60) -> float:
        """Get the message rate (messages per minute) for a channel over a time window"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        cutoff = int(time.time()) - window_seconds

        await self.flush_message_activity()
        total = await self.pool.fetchval(
            """SELECT SUM(message_count) FROM message_activity
            WHERE channel_id = $1 AND timestamp >= $2""",
            channel_id,
            cutoff,
        )

        return ((total or 0) / window_seconds) * 60  # messages per minute

    async def cleanup_old_message_activity(self, hours: int = 24) -> None:
        """Remove message activity records older than specified hours"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        cutoff = int(time.time()) - (hours * 3600)

        await self.flush_message_activity()
        await self.pool.execute("DELETE FROM message_activity WHERE timestamp < $1", cutoff)

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
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        await self.pool.execute(
            """INSERT INTO slowmode_changes
            (channel_id, old_value, new_value, reason, message_rate, confidence, timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            channel_id,
            old_value,
            new_value,
            reason,
            message_rate,
            confidence,
            int(time.time()),
        )

    async def get_expected_activity(
        self, channel_id: int, day_of_week: int, hour: int
    ) -> Optional[float]:
        """Get the expected message rate for a channel at a specific day and hour"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        row = await self.pool.fetchrow(
            """SELECT avg_message_rate, stddev_message_rate, sample_count FROM channel_patterns
            WHERE channel_id = $1 AND day_of_week = $2 AND hour = $3""",
            channel_id,
            day_of_week,
            hour,
        )

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
        last_updated: Optional[int] = None,
    ) -> None:
        """Update the channel pattern analytics"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        await self.pool.execute(
            """INSERT INTO channel_patterns
            (channel_id, day_of_week, hour, avg_message_rate, stddev_message_rate, sample_count, last_updated)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (channel_id, day_of_week, hour) DO UPDATE SET
            avg_message_rate = EXCLUDED.avg_message_rate,
            stddev_message_rate = EXCLUDED.stddev_message_rate,
            sample_count = EXCLUDED.sample_count,
            last_updated = EXCLUDED.last_updated""",
            channel_id,
            day_of_week,
            hour,
            avg_rate,
            stddev_rate,
            sample_count,
            last_updated if last_updated is not None else int(time.time()),
        )

    async def record_slowmode_effectiveness(
        self,
        channel_id: int,
        slowmode_value: int,
        rate_before: float,
        rate_after: float,
        duration: int,
    ) -> None:
        """Record the effectiveness of a slowmode change"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        was_effective = (rate_after < rate_before * 0.8) if rate_before > 0 else False

        await self.pool.execute(
            """INSERT INTO slowmode_effectiveness
            (channel_id, applied_at, slowmode_value, message_rate_before, message_rate_after, duration_seconds, was_effective)
            VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            channel_id,
            int(time.time()),
            slowmode_value,
            rate_before,
            rate_after,
            duration,
            was_effective,
        )

    async def get_effectiveness_score(self, channel_id: int) -> float:
        """Get the effectiveness score of slowmode changes for a channel"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        score = await self.pool.fetchval(
            """SELECT AVG(was_effective::int)::float8 FROM slowmode_effectiveness
            WHERE channel_id = $1 AND applied_at >= $2""",
            channel_id,
            int(time.time()) - (30 * 86400),
        )

        return score if score is not None else 0.0

    async def aggregate_hourly_analytics(self, channel_id: int) -> None:
        """Aggregate message activity into hourly analytics for a channel"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        hour_timestamp = int(time.time() // 3600) * 3600
        start_time = hour_timestamp - 3600

        await self.flush_message_activity()
        total = await self.pool.fetchval(
            """SELECT SUM(message_count) FROM message_activity
            WHERE channel_id = $1 AND timestamp >= $2 AND timestamp < $3""",
            channel_id,
            start_time,
            hour_timestamp,
        )

        if total:
            await self.pool.execute(
                """INSERT INTO channel_analytics
                (channel_id, hour_timestamp, total_messages, unique_users, avg_slowmode, max_slowmode)
                VALUES ($1, $2, $3, 0, 0, 0)
                ON CONFLICT (channel_id, hour_timestamp) DO UPDATE SET
                total_messages = EXCLUDED.total_messages""",
                channel_id,
                hour_timestamp,
                total,
            )

    async def get_channel_analytics(self, channel_id: int, hours_back: int = 24) -> List[dict]:
        """Get aggregated analytics for a channel"""
        if not self.pool:
            raise DatabaseError("Database not initialized")

        cutoff = int(time.time()) - (hours_back * 3600)

        rows = await self.pool.fetch(
            """SELECT * FROM channel_analytics
               WHERE channel_id = $1 AND hour_timestamp >= $2
               ORDER BY hour_timestamp DESC""",
            channel_id,
            cutoff,
        )

        return [dict(row) for row in rows]

    async def cleanup_old_analytics(self, days: int = 30) -> None:
        """Remove analytics older than specified days"""
        if not self.pool:
            raise DatabaseError("Database not initialized")

        cutoff = int(time.time()) - (days * 86400)

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM channel_analytics WHERE hour_timestamp < $1", cutoff)
            await conn.execute("DELETE FROM slowmode_effectiveness WHERE applied_at < $1", cutoff)
            await conn.execute("DELETE FROM slowmode_changes WHERE timestamp < $1", cutoff)

    async def get_module_config(self, guild_id: int, module_type: ModuleType) -> ModuleConfig:
        """Get configuration for a module"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        from serenity.core.modules import ModuleConfig, ModuleType

        row = await self.pool.fetchrow(
            "SELECT * FROM module_config WHERE guild_id = $1 AND module_type = $2",
            guild_id,
            module_type.value,
        )

        if row:
            return ModuleConfig(
                guild_id=row["guild_id"],
                module_type=ModuleType(row["module_type"]),
                is_enabled=row["is_enabled"],
                settings=json.loads(row["settings"]) if row["settings"] else {},
                updated_at=row["updated_at"],
            )

        await self.pool.execute(
            """INSERT INTO module_config (guild_id, module_type, is_enabled, settings, updated_at)
            VALUES ($1, $2, FALSE, '{}', $3)
            ON CONFLICT DO NOTHING""",
            guild_id,
            module_type.value,
            int(time.time()),
        )

        return ModuleConfig(
            guild_id=guild_id,
            module_type=module_type,
            is_enabled=False,
            settings={},
            updated_at=int(time.time()),
        )

    async def set_module_enabled(
        self, guild_id: int, module_type: ModuleType, enabled: bool
    ) -> None:
        """Enable or disable a module for a guild"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        await self.pool.execute(
            """INSERT INTO module_config (guild_id, module_type, is_enabled, settings, updated_at)
            VALUES ($1, $2, $3, '{}', $4)
            ON CONFLICT (guild_id, module_type) DO UPDATE SET
            is_enabled = EXCLUDED.is_enabled,
            updated_at = EXCLUDED.updated_at""",
            guild_id,
            module_type.value,
            enabled,
            int(time.time()),
        )

    async def get_enabled_modules(self, guild_id: int) -> List[ModuleType]:
        """Get all enabled modules for a guild"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        from serenity.core.modules import ModuleType

        rows = await self.pool.fetch(
            "SELECT module_type FROM module_config WHERE guild_id = $1 AND is_enabled",
            guild_id,
        )

        return [ModuleType(row["module_type"]) for row in rows]

    async def update_module_settings(
        self, guild_id: int, module_type: ModuleType, settings: Dict[str, Any]
    ) -> None:
        """Update settings for a module"""
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")

        await self.pool.execute(
            """UPDATE module_config
            SET settings = $1, updated_at = $2
            WHERE guild_id = $3 AND module_type = $4""",
            json.dumps(settings),
            int(time.time()),
            guild_id,
            module_type.value,
        )
