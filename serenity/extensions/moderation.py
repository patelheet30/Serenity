from __future__ import annotations

from typing import Optional

import arc
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleNotEnabledError, ModuleType
from serenity.services.moderation_service import ModerationService
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

plugin = arc.GatewayPlugin("moderation_commands")


@plugin.set_error_handler
async def on_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            "❌ The **Moderation** module is not enabled. "
            "Use `/module enable moderation` first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    if isinstance(exc, SerenityPermissionError):
        await ctx.respond(
            f"❌ {exc}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    raise exc


# ---------------------------------------------------------------------------
# Hierarchy / sanity check helper
# ---------------------------------------------------------------------------

_DELETE_MESSAGE_OPTIONS = {
    "Don't delete":   0,
    "Last hour":      3600,
    "Last 24 hours":  86400,
    "Last 7 days":    604800,
}

_TIMEOUT_UNITS = {
    "minutes": 60,
    "hours":   3600,
    "days":    86400,
}


async def _fetch_member(ctx: arc.GatewayContext, user_id: int) -> Optional[hikari.Member]:
    """Try cache first, fall back to REST."""
    member = ctx.client.app.cache.get_member(ctx.guild_id, user_id) # type: ignore
    if member:
        return member
    try:
        return await ctx.client.app.rest.fetch_member(ctx.guild_id, user_id) # type: ignore
    except hikari.NotFoundError:
        return None


def _get_top_role_position(member: hikari.Member, roles: hikari.api.Cache) -> int:
    """Return the highest role position for a member, 0 if none found."""
    guild_roles = roles.get_roles_view_for_guild(member.guild_id)
    if not guild_roles:
        return 0
    return max(
        (guild_roles[r].position for r in (member.role_ids or []) if r in guild_roles),
        default=0,
    )


async def _check_hierarchy(ctx: arc.GatewayContext, target: hikari.Member) -> None:
    """
    Validate that the moderator outranks the target.

    Raises SerenityPermissionError with a user-friendly message if not.
    Note: bot hierarchy against the target is left to Discord (ForbiddenError).
    """
    guild = ctx.client.app.cache.get_guild(ctx.guild_id) # type: ignore

    # Can't action the guild owner
    if guild and target.id == guild.owner_id:
        raise SerenityPermissionError("You cannot moderate the server owner.")

    # Can't action yourself
    if target.id == ctx.user.id:
        raise SerenityPermissionError("You cannot moderate yourself.")

    # Can't action the bot
    bot_user = ctx.client.app.get_me()
    if bot_user and target.id == bot_user.id:
        raise SerenityPermissionError("You cannot moderate me.")

    # Moderator must outrank target
    cache = ctx.client.app.cache
    mod_position = _get_top_role_position(ctx.member, cache)  # type: ignore[arg-type]
    target_position = _get_top_role_position(target, cache)

    if mod_position <= target_position:
        raise SerenityPermissionError(
            "You cannot moderate someone with an equal or higher role than yours."
        )


def _success_embed(
    title: str,
    target: hikari.User,
    case_number: int,
    reason: Optional[str],
    extra: Optional[str] = None,
) -> hikari.Embed:
    embed = hikari.Embed(title=title, color=hikari.Color(0x57F287))
    embed.add_field("User", f"{target.mention} (`{target}`)", inline=True)
    embed.add_field("Case", f"#{case_number}", inline=True)
    embed.add_field("Reason", reason or "No reason provided", inline=False)
    if extra:
        embed.add_field("Info", extra, inline=False)
    return embed


# ---------------------------------------------------------------------------
# /ban
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("ban", "Ban a user from the server.")
async def ban(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The user to ban")],
    reason: arc.Option[Optional[str], arc.StrParams("Reason for the ban")] = None,
    delete_messages: arc.Option[
        str,
        arc.StrParams(
            "How far back to delete their messages",
            choices=list(_DELETE_MESSAGE_OPTIONS.keys()),
        ),
    ] = "Don't delete",
    svc: ModerationService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.BAN_MEMBERS not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError("You need the **Ban Members** permission to use this command.")

    # If target is in the server, check hierarchy
    target_member = await _fetch_member(ctx, user.id)
    if target_member:
        await _check_hierarchy(ctx, target_member)

    delete_seconds = _DELETE_MESSAGE_OPTIONS[delete_messages]

    case_number = await svc.ban(
        guild_id=ctx.guild_id,
        target=user,
        moderator=ctx.user,
        reason=reason,
        delete_message_seconds=delete_seconds,
    )

    await ctx.respond(
        embed=_success_embed("🔨 User Banned", user, case_number, reason),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# ---------------------------------------------------------------------------
# /unban
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("unban", "Unban a user from the server.")
async def unban(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The user to unban (accepts ID if not in server)")],
    reason: arc.Option[Optional[str], arc.StrParams("Reason for the unban")] = None,
    svc: ModerationService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.BAN_MEMBERS not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError("You need the **Ban Members** permission to use this command.")

    case_number = await svc.unban(
        guild_id=ctx.guild_id,
        target=user,
        moderator=ctx.user,
        reason=reason,
    )

    await ctx.respond(
        embed=_success_embed("🔓 User Unbanned", user, case_number, reason),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# ---------------------------------------------------------------------------
# /kick
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("kick", "Kick a member from the server.")
async def kick(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The member to kick")],
    reason: arc.Option[Optional[str], arc.StrParams("Reason for the kick")] = None,
    svc: ModerationService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.KICK_MEMBERS not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError("You need the **Kick Members** permission to use this command.")

    target_member = await _fetch_member(ctx, user.id)
    if not target_member:
        await ctx.respond(
            "❌ That user is not in this server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await _check_hierarchy(ctx, target_member)

    case_number = await svc.kick(
        guild_id=ctx.guild_id,
        target=target_member,
        moderator=ctx.user,
        reason=reason,
    )

    await ctx.respond(
        embed=_success_embed("👢 User Kicked", user, case_number, reason),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# ---------------------------------------------------------------------------
# /timeout
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("timeout", "Timeout a member, preventing them from sending messages.")
async def timeout(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The member to timeout")],
    duration: arc.Option[int, arc.IntParams("Duration of the timeout", min=1, max=672)],
    unit: arc.Option[
        str,
        arc.StrParams("Unit of time", choices=list(_TIMEOUT_UNITS.keys())),
    ],
    reason: arc.Option[Optional[str], arc.StrParams("Reason for the timeout")] = None,
    svc: ModerationService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.MODERATE_MEMBERS not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError(
            "You need the **Moderate Members** permission to use this command."
        )

    target_member = await _fetch_member(ctx, user.id)
    if not target_member:
        await ctx.respond("❌ That user is not in this server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    await _check_hierarchy(ctx, target_member)

    duration_seconds = duration * _TIMEOUT_UNITS[unit]

    # Discord cap: 28 days
    if duration_seconds > 2_419_200:
        await ctx.respond(
            "❌ Timeout duration cannot exceed **28 days**.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    case_number = await svc.timeout(
        guild_id=ctx.guild_id,
        target=target_member,
        moderator=ctx.user,
        duration_seconds=duration_seconds,
        reason=reason,
    )

    await ctx.respond(
        embed=_success_embed(
            "⏱️ Member Timed Out",
            user,
            case_number,
            reason,
            extra=f"Duration: **{duration} {unit}**",
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# ---------------------------------------------------------------------------
# /untimeout
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("untimeout", "Remove an active timeout from a member.")
async def untimeout(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The member to un-timeout")],
    reason: arc.Option[Optional[str], arc.StrParams("Reason for removing the timeout")] = None,
    svc: ModerationService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.MODERATE_MEMBERS not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError(
            "You need the **Moderate Members** permission to use this command."
        )

    target_member = await _fetch_member(ctx, user.id)
    if not target_member:
        await ctx.respond("❌ That user is not in this server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if not target_member.communication_disabled_until():
        await ctx.respond(
            "❌ That member is not currently timed out.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await _check_hierarchy(ctx, target_member)

    case_number = await svc.untimeout(
        guild_id=ctx.guild_id,
        target=target_member,
        moderator=ctx.user,
        reason=reason,
    )

    await ctx.respond(
        embed=_success_embed("✅ Timeout Removed", user, case_number, reason),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# ---------------------------------------------------------------------------
# /warn
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("warn", "Issue a warning to a member.")
async def warn(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The member to warn")],
    reason: arc.Option[Optional[str], arc.StrParams("Reason for the warning")] = None,
    svc: ModerationService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.MANAGE_MESSAGES not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError(
            "You need the **Manage Messages** permission to use this command."
        )

    target_member = await _fetch_member(ctx, user.id)
    if not target_member:
        await ctx.respond("❌ That user is not in this server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    await _check_hierarchy(ctx, target_member)

    case_number, total_warnings = await svc.warn(
        guild_id=ctx.guild_id,
        target=target_member,
        moderator=ctx.user,
        reason=reason,
    )

    embed = _success_embed("⚠️ Member Warned", user, case_number, reason)
    embed.add_field(
        "Total Warnings",
        f"This member now has **{total_warnings}** active warning{'s' if total_warnings != 1 else ''}.",
        inline=False,
    )

    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


# ---------------------------------------------------------------------------
# Arc loader
# ---------------------------------------------------------------------------


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Moderation commands plugin loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Moderation commands plugin unloaded.")
