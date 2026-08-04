import arc
import hikari

from serenity.services.logging_service import LoggingService
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("logging_events")


# ---------------------------------------------------------------------------
# Message events
# ---------------------------------------------------------------------------


@plugin.listen()
async def on_message_delete(event: hikari.GuildMessageDeleteEvent) -> None:
    if not event.guild_id:
        return
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_message_delete(event)


@plugin.listen()
async def on_message_update(event: hikari.GuildMessageUpdateEvent) -> None:
    if not event.guild_id or event.author is None:
        return
    if event.author.is_bot: # type: ignore
        return
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_message_edit(event)


@plugin.listen()
async def on_guild_bulk_message_delete(event: hikari.GuildBulkMessageDeleteEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_bulk_message_delete(event)


# ---------------------------------------------------------------------------
# Member events
# ---------------------------------------------------------------------------


@plugin.listen()
async def on_member_create(event: hikari.MemberCreateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_member_join(event)


@plugin.listen()
async def on_member_delete(event: hikari.MemberDeleteEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_member_leave(event)


@plugin.listen()
async def on_member_update(event: hikari.MemberUpdateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_member_update(event)


# ---------------------------------------------------------------------------
# Voice events
# ---------------------------------------------------------------------------


@plugin.listen()
async def on_voice_state_update(event: hikari.VoiceStateUpdateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_voice_state_update(event)


# ---------------------------------------------------------------------------
# Server events
# ---------------------------------------------------------------------------


@plugin.listen()
async def on_guild_channel_create(event: hikari.GuildChannelCreateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_channel_create(event)


@plugin.listen()
async def on_guild_channel_delete(event: hikari.GuildChannelDeleteEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_channel_delete(event)


@plugin.listen()
async def on_guild_channel_update(event: hikari.GuildChannelUpdateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_channel_update(event)


@plugin.listen()
async def on_role_create(event: hikari.RoleCreateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_role_create(event)


@plugin.listen()
async def on_role_delete(event: hikari.RoleDeleteEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_role_delete(event)


@plugin.listen()
async def on_role_update(event: hikari.RoleUpdateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_role_update(event)


@plugin.listen()
async def on_guild_update(event: hikari.GuildUpdateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_guild_update(event)


@plugin.listen()
async def on_emojis_update(event: hikari.EmojisUpdateEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_emoji_update(event)


# ---------------------------------------------------------------------------
# Arc loader
# ---------------------------------------------------------------------------


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Logging events plugin loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Logging events plugin unloaded.")
