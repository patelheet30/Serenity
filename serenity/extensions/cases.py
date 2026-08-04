from __future__ import annotations

import time
from datetime import datetime, timezone

import arc
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleNotEnabledError, ModuleType
from serenity.database.moderation_repository import ModerationRepository
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACTION_META: dict[str, tuple[str, hikari.Color]] = {
    "ban":       ("🔨 Ban",             hikari.Color(0xED4245)),
    "softban":   ("🔨 Softban",         hikari.Color(0xED4245)),
    "unban":     ("🔓 Unban",           hikari.Color(0x57F287)),
    "kick":      ("👢 Kick",            hikari.Color(0xE67E22)),
    "timeout":   ("⏱️ Timeout",         hikari.Color(0xFEE75C)),
    "untimeout": ("✅ Timeout Removed", hikari.Color(0x57F287)),
    "warn":      ("⚠️ Warning",         hikari.Color(0xFEE75C)),
}

ACTION_EMOJI: dict[str, str] = {k: v[0].split()[0] for k, v in ACTION_META.items()}

# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

plugin = arc.GatewayPlugin(
    "case_management",
    default_permissions=hikari.Permissions.MANAGE_MESSAGES,
)


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
        await ctx.respond(f"❌ {exc}", flags=hikari.MessageFlag.EPHEMERAL)
        return
    raise exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, remainder = divmod(seconds, 3600)
    m = remainder // 60
    return f"{h}h {m}m" if m else f"{h}h"


async def _fetch_user_tag(rest: hikari.api.RESTClient, user_id: int) -> str:
    """Attempt to resolve a user ID to a tag. Returns a fallback string on failure."""
    try:
        user = await rest.fetch_user(user_id)
        return str(user)
    except Exception:
        return f"Unknown User ({user_id})"


def _build_case_embed(case: dict, target_tag: str, mod_tag: str) -> hikari.Embed:
    """Build a detailed embed for a single moderation case."""
    action = case["action"]
    title_str, color = ACTION_META.get(action, (f"🛡️ {action.title()}", hikari.Color(0x99AAB5)))

    embed = hikari.Embed(
        title=f"{title_str} | Case #{case['case_number']}",
        color=color,
        timestamp=datetime.fromtimestamp(case["created_at"], tz=timezone.utc),
    )

    embed.add_field("Target", f"<@{case['target_user_id']}> (`{target_tag}`)", inline=True)
    embed.add_field("Moderator", f"<@{case['moderator_id']}> (`{mod_tag}`)", inline=True)
    now = int(time.time())
    action = case["action"]

    if action == "timeout":
        is_expired = case.get("expires_at") and case["expires_at"] < now
        status = "🔴 Expired" if is_expired else ("🟢 Active" if case["is_active"] else "🔴 Inactive")
    elif action == "warn":
        status = "🟢 Active" if case["is_active"] else "🔴 Cleared"
    else:
        status = "✅ Applied"

    embed.add_field("Status", status, inline=True)

    embed.add_field("Status", status, inline=True)
    embed.add_field("Reason", case["reason"] or "No reason provided", inline=False)

    if case.get("duration_seconds"):
        embed.add_field("Duration", _format_duration(case["duration_seconds"]), inline=True)

    if case.get("expires_at"):
        now = int(time.time())
        if case["expires_at"] > now:
            embed.add_field("Expires", f"<t:{case['expires_at']}:R>", inline=True)
        else:
            embed.add_field("Expired", f"<t:{case['expires_at']}:R>", inline=True)

    embed.set_footer(text=f"Case #{case['case_number']} • Created")
    return embed


# ---------------------------------------------------------------------------
# /case <number>
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("case", "View the details of a moderation case.")
async def view_case(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("The case number to look up", min=1)],
    repo: ModerationRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    case = await repo.get_case(ctx.guild_id, number)
    if not case:
        await ctx.respond(
            f"❌ Case **#{number}** not found in this server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    rest = ctx.client.app.rest
    target_tag, mod_tag = await _fetch_user_tag(rest, case["target_user_id"]), \
                           await _fetch_user_tag(rest, case["moderator_id"])

    await ctx.respond(embed=_build_case_embed(case, target_tag, mod_tag))


# ---------------------------------------------------------------------------
# /cases <user>
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("cases", "View moderation history for a user.")
async def view_cases(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The user to look up")],
    repo: ModerationRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    all_cases = await repo.get_user_cases(ctx.guild_id, user.id)

    if not all_cases:
        embed = hikari.Embed(
            title=f"📋 Cases for {user}",
            description="No moderation history found for this user.",
            color=hikari.Color(0x57F287),
        )
        embed.set_thumbnail(user.display_avatar_url)
        await ctx.respond(embed=embed)
        return

    # Show most recent 10, newest first
    shown = all_cases[:10]
    total = len(all_cases)

    embed = hikari.Embed(
        title=f"📋 Cases for {user}",
        color=hikari.Color(0x5865F2),
    )
    embed.set_thumbnail(user.display_avatar_url)

    lines: list[str] = []
    for case in shown:
        emoji = ACTION_EMOJI.get(case["action"], "🛡️")
        action_label = case["action"].title()
        created = f"<t:{case['created_at']}:R>"
        reason = case["reason"] or "No reason"
        # Truncate reason so lines stay compact
        if len(reason) > 40:
            reason = reason[:37] + "..."
        status = "" if case["is_active"] else " ~~inactive~~"
        lines.append(f"`#{case['case_number']}` {emoji} **{action_label}**{status} • {created}\n　　{reason}")

    embed.description = "\n\n".join(lines)

    if total > 10:
        embed.set_footer(text=f"Showing 10 most recent of {total} total cases.")
    else:
        embed.set_footer(text=f"{total} case{'s' if total != 1 else ''} total.")

    await ctx.respond(embed=embed)


# ---------------------------------------------------------------------------
# /case-edit <number> <reason>  +  /reason <number> <reason>  (alias)
# ---------------------------------------------------------------------------


async def _edit_case_reason(
    ctx: arc.GatewayContext,
    number: int,
    new_reason: str,
    repo: ModerationRepository,
) -> None:
    """Shared logic for case-edit and reason commands."""
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    case = await repo.get_case(ctx.guild_id, number)
    if not case:
        await ctx.respond(
            f"❌ Case **#{number}** not found in this server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    old_reason = case["reason"] or "No reason provided"
    await repo.update_case_reason(ctx.guild_id, number, new_reason)

    embed = hikari.Embed(
        title=f"✏️ Case #{number} Updated",
        color=hikari.Color(0x5865F2),
    )
    embed.add_field("Old Reason", old_reason, inline=False)
    embed.add_field("New Reason", new_reason, inline=False)
    embed.set_footer(text=f"Updated by {ctx.user}")

    await ctx.respond(embed=embed)
    logger.info(
        f"Case #{number} reason updated in guild {ctx.guild_id} by {ctx.user.id}: "
        f"{old_reason!r} → {new_reason!r}"
    )


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("case-edit", "Update the reason for a moderation case.")
async def case_edit(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("The case number to edit", min=1)],
    reason: arc.Option[str, arc.StrParams("The new reason")],
    repo: ModerationRepository = arc.inject(),
) -> None:
    await _edit_case_reason(ctx, number, reason, repo)


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("reason", "Update the reason for a moderation case.")
async def reason(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("The case number to edit", min=1)],
    reason: arc.Option[str, arc.StrParams("The new reason")],
    repo: ModerationRepository = arc.inject(),
) -> None:
    await _edit_case_reason(ctx, number, reason, repo)


# ---------------------------------------------------------------------------
# /warnings <user>
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("warnings", "View active warnings for a user.")
async def warnings(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The user to look up")],
    repo: ModerationRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    active_warnings = await repo.get_active_warnings(ctx.guild_id, user.id)

    embed = hikari.Embed(
        title=f"⚠️ Warnings for {user}",
        color=hikari.Color(0xFEE75C) if active_warnings else hikari.Color(0x57F287),
    )
    embed.set_thumbnail(user.display_avatar_url)

    if not active_warnings:
        embed.description = "This user has no active warnings."
        await ctx.respond(embed=embed)
        return

    embed.description = f"**{len(active_warnings)}** active warning{'s' if len(active_warnings) != 1 else ''}."

    for i, warning in enumerate(active_warnings[:10], start=1):
        case_ref = f" (Case #{warning['case_id']})" if warning.get("case_id") else ""
        issued = f"<t:{warning['created_at']}:R>"
        embed.add_field(
            f"Warning {i}{case_ref}",
            f"**Reason:** {warning['reason'] or 'No reason provided'}\n"
            f"**Issued by:** <@{warning['moderator_id']}> • {issued}",
            inline=False,
        )

    if len(active_warnings) > 10:
        embed.set_footer(text=f"Showing 10 of {len(active_warnings)} warnings.")

    await ctx.respond(embed=embed)


# ---------------------------------------------------------------------------
# /clearwarns <user>
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("clearwarns", "Clear all active warnings for a user.")
async def clearwarns(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The user whose warnings to clear")],
    repo: ModerationRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    if hikari.Permissions.MODERATE_MEMBERS not in ctx.member.permissions:  # type: ignore[union-attr]
        raise SerenityPermissionError(
            "You need the **Moderate Members** permission to clear warnings."
        )

    count = await repo.clear_warnings(ctx.guild_id, user.id)

    if count == 0:
        await ctx.respond(
            f"ℹ️ {user.mention} has no active warnings to clear.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    embed = hikari.Embed(
        title="✅ Warnings Cleared",
        description=f"Cleared **{count}** active warning{'s' if count != 1 else ''} for {user.mention}.",
        color=hikari.Color(0x57F287),
    )
    embed.set_footer(text=f"Cleared by {ctx.user}")
    await ctx.respond(embed=embed)
    logger.info(
        f"Cleared {count} warnings for {user.id} in guild {ctx.guild_id} "
        f"by {ctx.user.id}"
    )


# ---------------------------------------------------------------------------
# /modstats
# ---------------------------------------------------------------------------


@plugin.include
@arc.with_hook(require_module(ModuleType.MODERATION))
@arc.slash_command("modstats", "View moderation statistics for this server.")
async def modstats(
    ctx: arc.GatewayContext,
    days: arc.Option[
        int,
        arc.IntParams("How many days back to look (default: 30)", min=1, max=365),
    ] = 30,
    repo: ModerationRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond("❌ This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL)
        return

    stats = await repo.get_moderation_stats(ctx.guild_id, days=days)

    # Separate warnings count (from warnings table) from case-based actions
    warnings_count = stats.pop("warnings", 0)

    total_cases = sum(stats.values())
    total_actions = total_cases + warnings_count

    embed = hikari.Embed(
        title="📊 Moderation Statistics",
        description=f"Last **{days}** day{'s' if days != 1 else ''} • **{total_actions}** total actions",
        color=hikari.Color(0x5865F2),
        timestamp=datetime.now(tz=timezone.utc),
    )

    # Case-based actions
    action_order = ["ban", "softban", "kick", "timeout", "untimeout", "unban"]
    for action in action_order:
        count = stats.get(action, 0)
        if count > 0:
            emoji = ACTION_EMOJI.get(action, "🛡️")
            label = action.replace("untimeout", "Untimeout").title()
            embed.add_field(f"{emoji} {label}s", str(count), inline=True)

    # Warnings are tracked separately
    if stats.get("warn", 0) > 0 or warnings_count > 0:
        embed.add_field("⚠️ Warns Issued", str(stats.get("warn", 0)), inline=True)
        embed.add_field("⚠️ Active Warnings", str(warnings_count), inline=True)

    if total_actions == 0:
        embed.description = f"No moderation actions in the last **{days}** day{'s' if days != 1 else ''}."

    embed.set_footer(text=f"Requested by {ctx.user}")
    await ctx.respond(embed=embed)


# ---------------------------------------------------------------------------
# Arc loader
# ---------------------------------------------------------------------------


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Case management plugin loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Case management plugin unloaded.")
