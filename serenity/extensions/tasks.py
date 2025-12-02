import asyncio
import random

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


@arc.utils.interval_loop(hours=1)
async def cleanup_old_data(repo: Repository) -> None:
    try:
        await repo.cleanup_old_message_activity(
            hours=SLOWMODE_CONFIG.MESSAGE_ACTIVITY_RETENTION_HOURS
        )
        logger.info("Old message activity data cleaned up.")
    except Exception as e:
        logger.error(f"Error cleaning up old data: {e}", exc_info=True)


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
    cleanup_old_data.start(
        repo=repo,
    )
    logger.info("Background tasks started.")


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    update_slowmode.stop()
    cleanup_old_data.stop()
    client.remove_plugin(plugin)
