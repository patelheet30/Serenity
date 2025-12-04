import asyncio
import math
import random
import time
from datetime import datetime
from typing import List

import arc
import hikari

from serenity.core.constants import SLOWMODE_CONFIG
from serenity.database.repository import Repository
from serenity.services.slowmode_engine import SlowmodeEngine
from serenity.utils.logging import channel_id as ctx_channel_id
from serenity.utils.logging import get_logger
from serenity.utils.logging import guild_id as ctx_guild_id

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("tasks")


@arc.utils.interval_loop(seconds=SLOWMODE_CONFIG.SLOWMODE_CHECK_INTERVAL)
async def update_slowmode(
    client: arc.GatewayClient,
    repo: Repository,
    engine: SlowmodeEngine,
) -> None:
    """Periodically update slowmode for all enabled channels."""
    try:
        guilds = client.app.cache.get_available_guilds_view()

        for guild_id in guilds.keys():
            ctx_guild_id.set(guild_id)

            guild_config = await repo.get_guild_config(guild_id)
            if not guild_config.is_enabled:
                ctx_guild_id.set(None)
                continue

            channel_ids = await repo.get_enabled_channels(guild_id)

            if channel_ids:
                await asyncio.sleep(random.uniform(0.1, 1.0))

            for channel_id in channel_ids:
                ctx_channel_id.set(channel_id)

                try:
                    discord_channel = client.app.cache.get_guild_channel(channel_id)
                    if not discord_channel or not isinstance(
                        discord_channel, hikari.GuildTextChannel
                    ):
                        continue

                    current_slowmode = discord_channel.rate_limit_per_user.total_seconds() or 0

                    decision = await engine.calculate_with_current(
                        channel_id,
                        guild_id,
                        current_slowmode,  # type: ignore
                    )

                    if decision.slowmode_seconds != current_slowmode:
                        await client.app.rest.edit_channel(
                            channel_id, rate_limit_per_user=decision.slowmode_seconds
                        )

                        current_rate = await repo.get_message_rate(channel_id, 60)

                        await repo.record_slowmode_change(
                            channel_id,
                            current_slowmode,  # type: ignore
                            decision.slowmode_seconds,
                            decision.reasoning,
                            current_rate,
                            decision.confidence,
                        )

                        logger.info(
                            f"Updated slowmode: {current_slowmode}s -> {decision.slowmode_seconds}s "
                            f"(rate: {current_rate:.1f} msg/min, confidence: {decision.confidence:.2f})"
                        )
                except Exception as e:
                    logger.error(
                        f"Error updating slowmode for channel {channel_id} in guild {guild_id}: {e}",
                        exc_info=True,
                    )
                finally:
                    ctx_channel_id.set(None)

            ctx_guild_id.set(None)
    except Exception as e:
        logger.error(f"Error in update_slowmode task: {e}", exc_info=True)


@arc.utils.interval_loop(seconds=3600)
async def aggregate_historical_patterns(repo: Repository) -> None:
    """Aggregate message activity into historical patterns.

    This builds up a profile of "normal" activity for each channel at specific days and hours, which the slowmode engine uses to detect anomalies.
    """
    logger.info("Starting historical pattern aggregation...")

    try:
        current_time = int(time.time())
        hour_timestamp = (current_time // 3600) * 3600
        completed_hour_start = hour_timestamp - 3600

        hour_dt = datetime.fromtimestamp(completed_hour_start)
        day_of_week = hour_dt.weekday()
        hour_of_day = hour_dt.hour

        channels_with_activity = await _get_active_channels(
            repo, completed_hour_start, hour_timestamp
        )

        logger.info(f"Found {len(channels_with_activity)} channels with activity in the last hour.")

        updated_count = 0
        for channel_id in channels_with_activity:
            try:
                message_count = await _get_message_count(
                    repo, channel_id, completed_hour_start, hour_timestamp
                )

                messages_per_minute = message_count / 60.0

                existing = await repo.get_expected_activity(channel_id, day_of_week, hour_of_day)

                if existing is not None:
                    new_avg, new_stddev, new_count = await _update_pattern_stats(
                        repo, channel_id, day_of_week, hour_of_day, messages_per_minute
                    )
                else:
                    new_avg = messages_per_minute
                    new_stddev = 0.0
                    new_count = 1

                await repo.update_channel_pattern(
                    channel_id, day_of_week, hour_of_day, new_avg, new_stddev, new_count
                )

                updated_count += 1
            except Exception as e:
                logger.error(
                    f"Error updating pattern for channel {channel_id}: {e}",
                    exc_info=True,
                )
        logger.info(
            f"Historical pattern aggregation complete. Updated {updated_count} channel patterns "
            f"for day={day_of_week}, hour={hour_of_day}"
        )
    except Exception as e:
        logger.error(f"Error in aggregate_historical_patterns task: {e}", exc_info=True)


@arc.utils.interval_loop(seconds=3600)
async def aggregate_hourly_analytics(repo: Repository) -> None:
    """Aggrgate message activity into hourly analytics summaries.

    This provides the data for the /stats command and other analytics features.
    """

    logger.info("Starting hourly analytics aggregation...")

    try:
        current_time = int(time.time())
        hour_timestamp = (current_time // 3600) * 3600
        completed_hour_start = hour_timestamp - 3600

        channels_with_activity = await _get_active_channels(
            repo, completed_hour_start, hour_timestamp
        )

        updated_count = 0
        for channel_id in channels_with_activity:
            try:
                await repo.aggregate_hourly_analytics(channel_id)
                updated_count += 1
            except Exception as e:
                logger.error(
                    f"Error aggregating analytics for channel {channel_id}: {e}",
                    exc_info=True,
                )
        logger.info(
            f"Hourly analytics aggregation complete. Updated analytics for {updated_count} channels."
        )
    except Exception as e:
        logger.error(f"Error in aggregate_hourly_analytics task: {e}", exc_info=True)


@arc.utils.interval_loop(hours=1)
async def cleanup_old_data(repo: Repository) -> None:
    try:
        await repo.cleanup_old_message_activity(
            hours=SLOWMODE_CONFIG.MESSAGE_ACTIVITY_RETENTION_HOURS
        )
        logger.info("Old message activity data cleaned up.")
    except Exception as e:
        logger.error(f"Error cleaning up old data: {e}", exc_info=True)


async def _get_active_channels(repo: Repository, start_time: int, end_time: int) -> List[int]:
    if not repo.connection:
        return []

    async with repo.connection.execute(
        """SELECT DISTINCT channel_id FROM message_activity
           WHERE timestamp >= ? AND timestamp < ?""",
        (start_time, end_time),
    ) as cursor:
        rows = await cursor.fetchall()

    return [row["channel_id"] for row in rows]


async def _get_message_count(
    repo: Repository, channel_id: int, start_time: int, end_time: int
) -> int:
    if not repo.connection:
        return 0

    async with repo.connection.execute(
        """SELECT sum(message_count) as total FROM message_activity
        WHERE channel_id = ? AND timestamp >= ? AND timestamp < ?""",
        (channel_id, start_time, end_time),
    ) as cursor:
        row = await cursor.fetchone()

    return row["total"] if row and row["total"] else 0


async def _update_pattern_stats(
    repo: Repository,
    channel_id: int,
    day_of_week: int,
    hour: int,
    new_value: float,
) -> tuple[float, float, int]:
    if not repo.connection:
        return new_value, 0.0, 1

    async with repo.connection.execute(
        """SELECT average_rate, stddev_message_rate, sample_count
        FROM channel_patterns
        WHERE channel_id = ? AND day_of_week = ? AND hour = ?""",
        (channel_id, day_of_week, hour),
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return new_value, 0.0, 1

    old_avg = row["average_rate"]
    old_stddev = row["stddev_message_rate"]
    old_count = row["sample_count"]

    new_count = old_count + 1
    new_avg = old_avg + (new_value - old_avg) / new_count

    old_variance = old_stddev**2
    old_sum_sq = old_variance * old_count

    new_sum_sq = old_sum_sq + (new_value - old_avg) * (new_value - new_avg)
    new_variance = new_sum_sq / new_count
    new_stddev = math.sqrt(new_variance)

    return new_avg, new_stddev, new_count


@plugin.listen()
async def on_started(_: hikari.StartedEvent) -> None:
    """Start background tasks."""

    await asyncio.sleep(2)

    repo = plugin.client.get_type_dependency(Repository)
    engine = plugin.client.get_type_dependency(SlowmodeEngine)

    update_slowmode.start(
        client=plugin.client,
        repo=repo,
        engine=engine,
    )
    aggregate_historical_patterns.start(repo=repo)
    aggregate_hourly_analytics.start(repo=repo)
    cleanup_old_data.start(repo=repo)

    logger.info("Background tasks started.")


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    update_slowmode.stop()
    cleanup_old_data.stop()
    client.remove_plugin(plugin)
