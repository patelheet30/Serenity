import asyncio
import datetime
import logging
import random
import time

import arc
import hikari

from .db import Database
from .utils import calculate_message_rate, determine_optimal_slowmode, message_cache

logger = logging.getLogger("core")

plugin = arc.GatewayPlugin("core")


@plugin.listen()
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    if event.is_bot or not event.is_human:
        return

    if not event.message.guild_id:
        return

    channel_id = event.channel_id
    timestamp = int(event.message.timestamp.timestamp())

    db = plugin.client.get_type_dependency(Database)

    await db.record_message(channel_id, timestamp)

    if channel_id not in message_cache:
        message_cache[channel_id] = []

    message_cache[channel_id].append(time.time())


@arc.utils.interval_loop(minutes=1)
async def update_slowmode(client: arc.GatewayClient, database: Database) -> None:
    try:
        enabled_guilds = await database.get_enabled_guilds()
        logger.debug(f"Found {len(enabled_guilds)} enabled guilds")

        for guild_config in enabled_guilds:
            guild_id = guild_config["guild_id"]

            try:
                logger.debug(f"Processing guild {guild_id} with config: {guild_config}")

                channels = await database.get_enabled_channels(guild_id)
                logger.debug(
                    f"Processing {len(channels)} channels for guild {guild_id}"
                )

                for channel_config in channels:
                    channel_id = channel_config["channel_id"]

                    message_rate = calculate_message_rate(channel_id)

                    current_db_activity = await database.get_channel_activity(
                        channel_id, 300
                    )

                    if message_rate == 0 and current_db_activity == 0:
                        logger.debug(
                            f"Skipping channel {channel_id} with no recent activity"
                        )
                        continue

                    try:
                        channel = await client.app.rest.fetch_channel(channel_id)

                        if not isinstance(channel, hikari.GuildTextChannel):
                            logger.debug(
                                f"Channel {channel_id} is not a text channel, skipping"
                            )
                            continue

                        logger.debug(
                            f"Channel {channel_id} has message rate: {message_rate:.2f} msg/min"
                        )

                        threshold = (
                            channel_config["threshold"]
                            or guild_config["default_threshold"]
                        )
                        logger.debug(
                            f"Channel {channel_id} has threshold: {threshold} msg/min"
                        )

                        optimal_slowmode = await determine_optimal_slowmode(
                            message_rate, threshold
                        )
                        logger.debug(
                            f"Optimal slowmode for channel {channel_id}: {optimal_slowmode}s"
                        )

                        current_slowmode = 0
                        if hasattr(channel, "rate_limit_per_user"):
                            if channel.rate_limit_per_user is not None:
                                if isinstance(
                                    channel.rate_limit_per_user, datetime.timedelta
                                ):
                                    current_slowmode = int(
                                        channel.rate_limit_per_user.total_seconds()
                                    )
                                else:
                                    current_slowmode = int(channel.rate_limit_per_user)

                        logger.debug(
                            f"Current slowmode for channel {channel_id}: {current_slowmode}s"
                        )

                        if current_slowmode != optimal_slowmode:
                            jitter = random.uniform(0.1, 2.0)
                            await asyncio.sleep(jitter)

                            try:
                                await client.app.rest.edit_channel(
                                    channel_id, rate_limit_per_user=optimal_slowmode
                                )

                                if current_slowmode == 0 and optimal_slowmode > 0:
                                    description = f"Slowmode has been enabled ({optimal_slowmode} seconds) due to high message volume."
                                    color = 0xFFA500  # Orange for warning
                                elif current_slowmode > 0 and optimal_slowmode == 0:
                                    description = "Slowmode has been disabled as message volume has returned to normal."
                                    color = 0x00FF00  # Green for positive
                                elif optimal_slowmode > current_slowmode:
                                    description = f"Slowmode increased from {current_slowmode}s to {optimal_slowmode}s due to continued high message volume."
                                    color = 0xFF0000  # Red for restrictive
                                else:
                                    description = f"Slowmode reduced from {current_slowmode}s to {optimal_slowmode}s as message volume decreased."
                                    color = 0x00FFFF  # Cyan for less restrictive

                                embed = hikari.Embed(
                                    title="Auto Slowmode Update",
                                    description=description,
                                    color=color,
                                )

                                try:
                                    await client.app.rest.create_message(
                                        channel.id,
                                        embed=embed,
                                    )
                                except hikari.ForbiddenError:
                                    # Can't send messages but can still set slowmode
                                    logger.warning(
                                        f"No permission to send messages in channel {channel_id}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Error sending notification in channel {channel_id}: {str(e)}"
                                    )

                                logger.info(
                                    f"Updated slowmode for channel {channel_id} from {current_slowmode}s to {optimal_slowmode}s "
                                    f"(rate: {message_rate:.2f} msg/min, threshold: {threshold})"
                                )
                            except hikari.ForbiddenError:
                                logger.warning(
                                    f"No permission to update slowmode for channel {channel_id}"
                                )
                                # Update the database to disable this channel since we can't manage it
                                await database.update_channel_config(
                                    channel_id, is_enabled=0
                                )
                            except Exception as e:
                                logger.error(
                                    f"Error updating slowmode for channel {channel_id}: {str(e)}"
                                )

                    except hikari.ForbiddenError:
                        logger.warning(
                            f"No access to channel {channel_id}, disabling it in auto-slowmode"
                        )
                        # Update the database to disable this channel since we can't access it
                        await database.update_channel_config(channel_id, is_enabled=0)
                    except hikari.NotFoundError:
                        logger.warning(
                            f"Channel {channel_id} not found (deleted?), disabling it in auto-slowmode"
                        )
                        # Update the database to disable this channel since it doesn't exist
                        await database.update_channel_config(channel_id, is_enabled=0)
                    except Exception as e:
                        logger.error(f"Error processing channel {channel_id}: {str(e)}")

            except Exception as e:
                logger.error(f"Error processing guild {guild_id}: {str(e)}")

    except Exception as e:
        logger.error(f"Error in slowmode update loop: {str(e)}")


@arc.utils.interval_loop(hours=1)
async def cleanup_old_data(database: Database = arc.inject()) -> None:
    try:
        await database.cleanup_old_messages(max_age=86400)
        logger.info("Cleaned up old message data")
    except Exception as e:
        logger.error(f"Error cleaning up old message data: {str(e)}")


@plugin.listen()
async def on_started(_: hikari.StartedEvent) -> None:
    await asyncio.sleep(1)

    database = plugin.client.get_type_dependency(Database)

    update_slowmode.start(client=plugin.client, database=database)
    cleanup_old_data.start(database=database)

    logger.info("Auto-slowmode loops started")


@arc.loader
def loader(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)


@arc.unloader
def unloader(client: arc.GatewayClient) -> None:
    update_slowmode.stop()
    cleanup_old_data.stop()
    client.remove_plugin(plugin)
