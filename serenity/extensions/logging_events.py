import time

import arc
import hikari
from hikari.events.auto_mod_events import AutoModActionExecutionEvent

from serenity.core.modules import ModuleManager, ModuleType
from serenity.database.repository import Repository
from serenity.services.audit_actors import AuditActors
from serenity.services.logging_service import LoggingService
from serenity.services.moderation_service import EXTERNAL_TYPES, ModerationService
from serenity.services.security_service import SecurityService
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
# Audit log (needs the bot's View Audit Log permission; Discord sends nothing without it)
# ---------------------------------------------------------------------------


@plugin.listen()
async def on_automod_action(event: AutoModActionExecutionEvent) -> None:
    svc: LoggingService = plugin.client.get_type_dependency(LoggingService)
    await svc.log_automod_action(event)


MANUAL_SLOWMODE_PAUSE = 3600  # seconds auto-slowmode leaves a hand-set slowmode alone (#150)


async def pause_for_manual_slowmode(
    entry: hikari.AuditLogEntry, *, me: int, repo: Repository, logging_svc: LoggingService
) -> None:
    """Someone other than Serenity changed a channel's slowmode: stop adjusting it for an hour."""
    if entry.user_id in (None, me) or entry.target_id is None or entry.action_type not in (
        hikari.AuditLogEventType.CHANNEL_UPDATE,
        hikari.AuditLogEventType.THREAD_UPDATE,
    ):
        return
    change = next(
        (c for c in entry.changes if c.key == hikari.AuditLogChangeKey.RATE_LIMIT_PER_USER), None
    )
    if change is None or not (await repo.get_guild_config(entry.guild_id)).is_enabled:
        return
    until = int(time.time()) + MANUAL_SLOWMODE_PAUSE
    if await repo.pause_channel(entry.target_id, until):
        await logging_svc.log_slowmode_paused(
            guild_id=entry.guild_id,
            channel_id=entry.target_id,
            moderator_id=entry.user_id,  # type: ignore[arg-type]
            old=int(change.old_value.total_seconds()) if change.old_value else 0,
            new=int(change.new_value.total_seconds()) if change.new_value else 0,
            until=until,
        )


@plugin.listen()
async def on_audit_log_entry(event: hikari.AuditLogEntryCreateEvent) -> None:
    me = plugin.client.app.get_me().id  # type: ignore[union-attr]
    get = plugin.client.get_type_dependency
    entry = event.entry
    get(AuditActors).feed(entry)

    async def record_external() -> None:
        if entry.action_type in EXTERNAL_TYPES and await get(ModuleManager).is_enabled(
            event.guild_id, ModuleType.MODERATION
        ):
            await get(ModerationService).record_external(entry, me=me)

    # Each feature runs even if an earlier one fails on this entry; each step is only built when
    # its turn comes, so a failure can't leave the others un-awaited.
    for name, step in (
        ("logs", lambda: get(LoggingService).log_audit_entry(entry)),
        ("anti-nuke", lambda: get(SecurityService).on_entry(entry, me=me)),
        ("warnings", lambda: get(SecurityService).check_dangerous(entry, me=me)),
        ("slowmode pause", lambda: pause_for_manual_slowmode(
            entry, me=me, repo=get(Repository), logging_svc=get(LoggingService)
        )),
        ("external cases", record_external),
    ):
        try:
            await step()
        except Exception:
            logger.exception(f"Audit log entry {entry.id} failed in {name}")


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
