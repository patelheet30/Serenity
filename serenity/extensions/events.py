import arc
import hikari

from serenity.database.repository import Repository
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
    await repo.record_message_activity(event.channel_id, timestamp)


@plugin.listen()
async def on_started(_: hikari.StartedEvent) -> None:
    """Bot startup event handler."""
    logger.info("Event handlers initialised and running.")


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
