"""/audit-history: the Audit history module's settings (#154)."""

from __future__ import annotations

import arc
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleManager, ModuleNotEnabledError, ModuleType
from serenity.database.audit_repository import RETENTION_DEFAULT, RETENTION_MAX, AuditRepository
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("audit_history_commands", default_permissions=hikari.Permissions.MANAGE_GUILD)
group = plugin.include_slash_group("audit-history", "The server's stored audit log history.")


@plugin.set_error_handler
async def on_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            "❌ The **Audit history** module is not enabled. Use `/module enable audit_history` first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    raise exc


@group.include
@arc.with_hook(require_module(ModuleType.AUDIT_HISTORY))
@arc.slash_subcommand("retention", "How many days of audit history to keep.")
async def retention(
    ctx: arc.GatewayContext,
    days: arc.Option[
        int, arc.IntParams(f"Days to keep (default {RETENTION_DEFAULT})", min=7, max=RETENTION_MAX)
    ],
    modules: ModuleManager = arc.inject(),
) -> None:
    config = await modules.get_module_config(ctx.guild_id, ModuleType.AUDIT_HISTORY)  # type: ignore[arg-type]
    await modules.update_module_settings(
        ctx.guild_id, ModuleType.AUDIT_HISTORY, {**config.settings, "retention_days": days}  # type: ignore[arg-type]
    )
    await ctx.respond(f"✅ Audit history is now kept for **{days}** days.")


@group.include
@arc.slash_subcommand("clear", "Delete all of this server's stored audit history.")
async def clear(
    ctx: arc.GatewayContext,
    confirm: arc.Option[bool, arc.BoolParams("Yes, delete it all. This can't be undone.")],
    repo: AuditRepository = arc.inject(),
) -> None:
    if not confirm:
        await ctx.respond("Nothing was deleted.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    count = await repo.clear(ctx.guild_id)  # type: ignore[arg-type]
    await ctx.respond(f"🗑️ Deleted **{count}** stored audit log entries.")
    logger.info(f"Audit history cleared in guild {ctx.guild_id} by user {ctx.user.id}")


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Audit history commands plugin loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Audit history commands plugin unloaded.")
