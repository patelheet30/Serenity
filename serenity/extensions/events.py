import arc
import hikari

from serenity.database.repository import Repository
from serenity.services.metrics import COMMANDS_USED, MESSAGES_PROCESSED, init_guild_log_events
from serenity.utils.logging import channel_id as ctx_channel_id
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("events")


@plugin.listen()
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    """Track message activity"""

    if event.is_bot or not event.is_human or not event.message.guild_id:
        return

    repo = plugin.client.get_type_dependency(Repository)

    ctx_channel_id.set(event.channel_id)

    timestamp = int(event.message.timestamp.timestamp())
    await repo.record_message_activity(event.channel_id, timestamp, event.author_id)

    MESSAGES_PROCESSED.labels(guild_id=str(event.message.guild_id)).inc()


@plugin.listen(hikari.GuildAvailableEvent, hikari.GuildJoinEvent)
async def on_guild_ready(event: hikari.GuildAvailableEvent | hikari.GuildJoinEvent) -> None:
    init_guild_log_events(event.guild_id)


@plugin.listen()
async def on_started(_: hikari.StartedEvent) -> None:
    """Bot startup event handler."""
    # Start every command at 0 so first uses register and unused commands still show up
    for command in plugin.client.walk_commands(hikari.CommandType.SLASH, callable_only=True):
        COMMANDS_USED.labels(command=" ".join(command.qualified_name))
    logger.info("Event handlers initialised and running.")


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
