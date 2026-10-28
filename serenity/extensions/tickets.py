from __future__ import annotations

import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, available_timezones

import arc
import asyncpg
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleManager, ModuleNotEnabledError, ModuleType, tickets_guilds
from serenity.database.logging_repository import LoggingRepository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.ticket_hours import format_schedule, parse_schedule
from serenity.services.ticket_service import (
    PRIORITIES,
    TicketError,
    TicketService,
    member_permissions,
    parse_questions,
    render_transcript,
)
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

_GREEN = hikari.Color(0x57F287)
_BLUE = hikari.Color(0x5865F2)

CLAIM_MODES = {"First staff reply": "reply", "Claim button": "button", "Both": "both"}

# Staff-only by default; server admins can grant /ticket to their staff roles in
# Server Settings → Integrations. Openers use the buttons inside their ticket instead.
plugin = arc.GatewayPlugin("tickets", default_permissions=hikari.Permissions.MANAGE_MESSAGES)
plugin.add_hook(require_module(ModuleType.TICKETS))

# Registered only in the TICKETS_GUILDS servers until Tickets launches, so nobody else sees it.
ticket = plugin.include_slash_group(
    "ticket", "Support tickets.", guilds=sorted(tickets_guilds()) or hikari.UNDEFINED
)
type_group = ticket.include_subgroup("type", "Configure the kinds of ticket members can open.")
panel_group = ticket.include_subgroup("panel", "Post the messages members open tickets from.")
blacklist_group = ticket.include_subgroup("blacklist", "Stop members from opening tickets.")
transcript_group = ticket.include_subgroup("transcript", "Closed tickets' transcripts.")
settings_group = ticket.include_subgroup("settings", "Server-wide ticket settings.")

PANEL_STYLES = {"Buttons": "buttons", "Dropdown": "menu"}
MAX_PANEL_TYPES = 25  # five rows of five buttons, or a dropdown's 25 options


@plugin.set_error_handler
async def on_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            "❌ The **Tickets** module is not enabled. Use `/module enable tickets` first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    if isinstance(exc, (TicketError, SerenityPermissionError)):
        await ctx.respond(f"❌ {exc}", flags=hikari.MessageFlag.EPHEMERAL)
        return
    if isinstance(exc, asyncpg.UniqueViolationError):  # type names, case-insensitively
        await ctx.respond(
            "❌ A ticket type with that name already exists.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return
    raise exc


def require_manage_guild(ctx: arc.GatewayContext) -> int:
    """The guild id, if the invoker may change ticket configuration."""
    if not ctx.guild_id or not ctx.member:
        raise TicketError("This command can only be used in a server.")
    if not ctx.member.permissions & (
        hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR
    ):
        raise SerenityPermissionError("You need **Manage Server** to configure tickets.")
    return ctx.guild_id


async def type_names(data: arc.AutocompleteData[arc.GatewayClient, str]) -> list[str]:
    if not data.guild_id:
        return []
    repo = data.client.get_type_dependency(TicketRepository)
    typed = (data.focused_value or "").lower()
    return [t["name"] for t in await repo.list_types(data.guild_id) if typed in t["name"].lower()][:25]


TypeOption = arc.Option[str, arc.StrParams("The ticket type.", autocomplete_with=type_names)]


def describe_type(ticket_type: dict, service: TicketService) -> str:
    roles = ", ".join(f"<@&{r}>" for r in ticket_type["staff_role_ids"]) or "Manage Server only"
    category = f"<#{ticket_type['category_id']}>" if ticket_type["category_id"] else "None"
    questions = "\n".join(f"• {f['label']}" for f in ticket_type["fields"]) or "None"
    retention = ticket_type["retention_days"]
    return (
        f"**{service.type_label(ticket_type)}**\n"
        f"Staff: {roles}\nCategory: {category}\n"
        f"Claiming: {ticket_type['claim_mode']} · Open limit: {ticket_type['open_limit']}\n"
        f"Channel name: `{ticket_type['name_template']}`\n"
        f"Hours: {format_schedule(ticket_type['schedule']) if ticket_type['schedule'] else 'server default'}"
        f" · Outside hours: {'can open' if ticket_type['open_off_hours'] else 'closed'}\n"
        f"Transcripts: {'on' if ticket_type['transcripts'] else 'off'}"
        f" · Retention: {'server default' if retention is None else 'until deleted' if retention == 0 else f'{retention} days'}\n"
        f"Questions:\n{questions}"
    )


@type_group.include
@arc.slash_subcommand("create", "Create a ticket type.")
async def type_create(
    ctx: arc.GatewayContext,
    name: arc.Option[str, arc.StrParams("Shown on the panel button.", max_length=80)],
    staff_role: arc.Option[hikari.Role, arc.RoleParams("Role that handles these tickets.")],
    category: arc.Option[
        Optional[hikari.GuildCategory], arc.ChannelParams("Category new ticket channels go in.")
    ] = None,
    emoji: arc.Option[Optional[str], arc.StrParams("Emoji for the panel button.")] = None,
    description: arc.Option[
        Optional[str], arc.StrParams("Shown in the panel's dropdown.", max_length=100)
    ] = None,
    questions: arc.Option[
        Optional[str], arc.StrParams("Up to 5 form questions, separated by |", max_length=300)
    ] = None,
    claim_mode: arc.Option[
        str, arc.StrParams("How staff take a ticket.", choices=CLAIM_MODES)
    ] = "reply",
    open_limit: arc.Option[
        int, arc.IntParams("Open tickets of this type per member.", min=1, max=10)
    ] = 1,
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    if await repo.get_type_by_name(guild_id, name):
        raise TicketError(f"A ticket type called **{name}** already exists.")
    created = await repo.create_type(
        guild_id,
        name,
        staff_role_ids=[staff_role.id],
        category_id=category.id if category else None,
        emoji=emoji,
        description=description,
        fields=parse_questions(questions or ""),
        claim_mode=claim_mode,
        open_limit=open_limit,
    )
    await ctx.respond(
        embed=hikari.Embed(
            title="✅ Ticket type created",
            description=describe_type(created, service),
            color=_GREEN,
        ).set_footer("Post it with /ticket panel post")
    )


@type_group.include
@arc.slash_subcommand("edit", "Change a ticket type.")
async def type_edit(
    ctx: arc.GatewayContext,
    type: TypeOption,
    name: arc.Option[Optional[str], arc.StrParams("New name.", max_length=80)] = None,
    category: arc.Option[
        Optional[hikari.GuildCategory], arc.ChannelParams("Category new ticket channels go in.")
    ] = None,
    emoji: arc.Option[Optional[str], arc.StrParams("Emoji for the panel button.")] = None,
    description: arc.Option[
        Optional[str], arc.StrParams("Shown in the panel's dropdown.", max_length=100)
    ] = None,
    questions: arc.Option[
        Optional[str],
        arc.StrParams("Up to 5 form questions, separated by | (\"none\" to clear)", max_length=300),
    ] = None,
    claim_mode: arc.Option[
        Optional[str], arc.StrParams("How staff take a ticket.", choices=CLAIM_MODES)
    ] = None,
    open_limit: arc.Option[
        Optional[int], arc.IntParams("Open tickets of this type per member.", min=1, max=10)
    ] = None,
    channel_name: arc.Option[
        Optional[str],
        arc.StrParams("Template: {type} {number} {user} {field:key}", max_length=90),
    ] = None,
    transcripts: arc.Option[Optional[bool], arc.BoolParams("Keep transcripts.")] = None,
    retention_days: arc.Option[
        Optional[int],
        arc.IntParams("Days to keep transcripts (7–365, 0 = until deleted).", min=0, max=365),
    ] = None,
    auto_unclaim_hours: arc.Option[
        Optional[int],
        arc.IntParams("Unclaim after this many hours of staff silence (0 = never).", min=0, max=168),
    ] = None,
    hours: arc.Option[
        Optional[str],
        arc.StrParams('Own support hours, e.g. "mon-fri 09:00-17:00" ("default" = the server\'s).', max_length=200),
    ] = None,
    open_off_hours: arc.Option[
        Optional[bool], arc.BoolParams("Let members open these tickets outside support hours.")
    ] = None,
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    ticket_type = await service.resolve_type(guild_id, type)
    if retention_days is not None and 0 < retention_days < 7:
        raise TicketError("Retention is 0 (until deleted) or 7–365 days.")

    values: dict = {
        "name": name,
        "category_id": category.id if category else None,
        "emoji": emoji,
        "description": description,
        "claim_mode": claim_mode,
        "open_limit": open_limit,
        "name_template": channel_name,
        "transcripts": transcripts,
        "retention_days": retention_days,
        "open_off_hours": open_off_hours,
    }
    values = {k: v for k, v in values.items() if v is not None}
    if questions is not None:
        values["fields"] = [] if questions.strip().lower() == "none" else parse_questions(questions)
    if auto_unclaim_hours is not None:
        values["auto_unclaim_hours"] = auto_unclaim_hours or None
    if hours is not None:
        values["schedule"] = None if hours.strip().lower() == "default" else schedule_from(hours)
    if not values:
        raise TicketError("Nothing to change.")

    updated = await repo.update_type(guild_id, ticket_type["id"], **values)
    if updated is None:
        raise TicketError(f"**{ticket_type['name']}** was deleted in the meantime.")
    await service.refresh_panels(guild_id, ticket_type["id"])
    await ctx.respond(
        embed=hikari.Embed(
            title="✅ Ticket type updated",
            description=describe_type(updated, service),
            color=_GREEN,
        )
    )


@type_group.include
@arc.slash_subcommand("staff", "Add or remove a staff role on a ticket type.")
async def type_staff(
    ctx: arc.GatewayContext,
    type: TypeOption,
    role: arc.Option[hikari.Role, arc.RoleParams("Role to add, or remove if it's already staff.")],
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    ticket_type = await service.resolve_type(guild_id, type)
    roles: list[int] = ticket_type["staff_role_ids"]
    removed = role.id in roles
    roles = [r for r in roles if r != role.id] if removed else [*roles, role.id]
    if await repo.update_type(guild_id, ticket_type["id"], staff_role_ids=roles) is None:
        raise TicketError(f"**{ticket_type['name']}** was deleted in the meantime.")
    await ctx.respond(
        f"✅ {role.mention} {'no longer handles' if removed else 'now handles'} "
        f"**{ticket_type['name']}** tickets. (Open tickets keep their current access.)",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@type_group.include
@arc.slash_subcommand("delete", "Delete a ticket type no panel shows and that has no tickets.")
async def type_delete(
    ctx: arc.GatewayContext,
    type: TypeOption,
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    ticket_type = await service.resolve_type(guild_id, type)
    if not await repo.delete_type(guild_id, ticket_type["id"]):
        raise TicketError(
            f"**{ticket_type['name']}** is still on a panel or has tickets (kept for their "
            "history). Take it off your panels; types with tickets can't be deleted."
        )
    await ctx.respond(f"🗑️ Deleted the **{ticket_type['name']}** ticket type.")


@type_group.include
@arc.slash_subcommand("list", "List this server's ticket types.")
async def type_list(
    ctx: arc.GatewayContext,
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        raise TicketError("This command can only be used in a server.")
    types = await repo.list_types(ctx.guild_id)
    embed = hikari.Embed(
        title="🎫 Ticket types",
        description="\n\n".join(describe_type(t, service) for t in types)[:4096]
        or "None yet. Create one with `/ticket type create`.",
        color=_BLUE,
    )
    await ctx.respond(embed=embed, flags=hikari.MessageFlag.EPHEMERAL)


async def resolve_type_list(service: TicketService, guild_id: int, text: str) -> list[int]:
    names = [n.strip() for n in text.split(",") if n.strip()]
    if not names:
        raise TicketError("List at least one ticket type, separated by commas.")
    if len(names) > MAX_PANEL_TYPES:
        raise TicketError(f"A panel can show at most {MAX_PANEL_TYPES} ticket types.")
    ids = [(await service.resolve_type(guild_id, n))["id"] for n in names]
    return list(dict.fromkeys(ids))  # a type listed twice gets one button


async def resolve_panel(repo: TicketRepository, guild_id: int, panel_id: int) -> dict:
    panel = await repo.get_panel(guild_id, panel_id)
    if panel is None:
        raise TicketError(f"There's no panel #{panel_id}. See `/ticket panel list`.")
    return panel


TypesOption = arc.Option[
    str, arc.StrParams("Ticket types to show, separated by commas.", max_length=1000)
]


@panel_group.include
@arc.slash_subcommand("post", "Post a ticket panel.")
async def panel_post(
    ctx: arc.GatewayContext,
    channel: arc.Option[hikari.GuildTextChannel, arc.ChannelParams("Where to post it.")],
    title: arc.Option[str, arc.StrParams("Panel title.", max_length=256)],
    types: TypesOption,
    description: arc.Option[
        Optional[str], arc.StrParams("Text under the title.", max_length=2000)
    ] = None,
    style: arc.Option[str, arc.StrParams("Buttons or a dropdown.", choices=PANEL_STYLES)] = "buttons",
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    type_ids = await resolve_type_list(service, guild_id, types)
    panel = await repo.create_panel(guild_id, channel.id, title, type_ids, description, style)
    try:
        panel = await service.post_panel(panel)
    except BaseException:  # cancelled too: don't leave an unposted panel behind
        await repo.delete_panel(guild_id, panel["id"])
        raise
    await ctx.respond(
        f"✅ Posted panel #{panel['id']} in {channel.mention}.", flags=hikari.MessageFlag.EPHEMERAL
    )


@panel_group.include
@arc.slash_subcommand("edit", "Change a posted panel; it's updated in place.")
async def panel_edit(
    ctx: arc.GatewayContext,
    panel: arc.Option[int, arc.IntParams("Panel number, from /ticket panel list.")],
    title: arc.Option[Optional[str], arc.StrParams("Panel title.", max_length=256)] = None,
    types: arc.Option[
        Optional[str], arc.StrParams("Ticket types to show, separated by commas.", max_length=1000)
    ] = None,
    description: arc.Option[
        Optional[str], arc.StrParams("Text under the title.", max_length=2000)
    ] = None,
    style: arc.Option[
        Optional[str], arc.StrParams("Buttons or a dropdown.", choices=PANEL_STYLES)
    ] = None,
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    existing = await resolve_panel(repo, guild_id, panel)
    values: dict = {"title": title, "description": description, "style": style}
    values = {k: v for k, v in values.items() if v is not None}
    if types is not None:
        values["type_ids"] = await resolve_type_list(service, guild_id, types)
    updated = await repo.update_panel(guild_id, existing["id"], **values) if values else existing
    await service.post_panel(updated)  # type: ignore[arg-type]
    await ctx.respond(f"✅ Updated panel #{panel}.", flags=hikari.MessageFlag.EPHEMERAL)


@panel_group.include
@arc.slash_subcommand("delete", "Delete a panel and its message.")
async def panel_delete(
    ctx: arc.GatewayContext,
    panel: arc.Option[int, arc.IntParams("Panel number, from /ticket panel list.")],
    repo: TicketRepository = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    await service.delete_panel(await resolve_panel(repo, guild_id, panel))
    await ctx.respond(f"🗑️ Deleted panel #{panel}.", flags=hikari.MessageFlag.EPHEMERAL)


@panel_group.include
@arc.slash_subcommand("list", "List this server's ticket panels.")
async def panel_list(
    ctx: arc.GatewayContext,
    repo: TicketRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        raise TicketError("This command can only be used in a server.")
    names = {t["id"]: t["name"] for t in await repo.list_types(ctx.guild_id)}
    lines = []
    for p in await repo.list_panels(ctx.guild_id):
        where = (
            f"https://discord.com/channels/{p['guild_id']}/{p['channel_id']}/{p['message_id']}"
            if p["message_id"]
            else f"<#{p['channel_id']}> (message deleted; `/ticket panel edit {p['id']}` reposts it)"
        )
        shown = ", ".join(names[i] for i in p["type_ids"] if i in names)
        lines.append(f"**#{p['id']} {p['title']}** · {p['style']} · {shown}\n{where}")
    await ctx.respond(
        embed=hikari.Embed(
            title="🎫 Ticket panels",
            description="\n\n".join(lines)[:4096] or "None yet. Post one with `/ticket panel post`.",
            color=_BLUE,
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@blacklist_group.include
@arc.slash_subcommand("add", "Stop a member from opening tickets.")
async def blacklist_add(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("Who to block.")],
    reason: arc.Option[Optional[str], arc.StrParams("Why (staff only).", max_length=500)] = None,
    repo: TicketRepository = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    if not await repo.blacklist_add(guild_id, user.id, ctx.user.id, reason):
        raise TicketError(f"{user.mention} is already blocked from opening tickets.")
    await ctx.respond(
        f"🚫 {user.mention} can no longer open tickets.", flags=hikari.MessageFlag.EPHEMERAL
    )


@blacklist_group.include
@arc.slash_subcommand("remove", "Let a member open tickets again.")
async def blacklist_remove(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("Who to unblock.")],
    repo: TicketRepository = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    if not await repo.blacklist_remove(guild_id, user.id):
        raise TicketError(f"{user.mention} isn't blocked.")
    await ctx.respond(f"✅ {user.mention} can open tickets again.", flags=hikari.MessageFlag.EPHEMERAL)


@ticket.include
@arc.slash_subcommand("claim", "Take this ticket.")
async def claim(ctx: arc.GatewayContext, service: TicketService = arc.inject()) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    await service.claim(current, ctx.user.id, announce=False)
    await ctx.respond(f"🙋 {ctx.user.mention} claimed this ticket.")


@ticket.include
@arc.slash_subcommand("unclaim", "Give this ticket back to the queue.")
async def unclaim(ctx: arc.GatewayContext, service: TicketService = arc.inject()) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    member = service.require_staff(ctx.member, ticket_type)
    if current["assignee_id"] is None:
        raise TicketError("Nobody has claimed this ticket.")
    if current["assignee_id"] != ctx.user.id and not member_permissions(member) & hikari.Permissions.MANAGE_GUILD:
        raise TicketError(f"Only <@{current['assignee_id']}> or an admin can unclaim this ticket.")
    if not await service.unclaim(
        current, ctx.user.id, f"↩️ <@{current['assignee_id']}> is no longer handling this ticket."
    ):
        raise TicketError("Someone else claimed this ticket a moment ago.")
    await ctx.respond("✅ Unclaimed.", flags=hikari.MessageFlag.EPHEMERAL)


@ticket.include
@arc.slash_subcommand("assign", "Give this ticket to a staff member (admins).")
async def assign(
    ctx: arc.GatewayContext,
    member: arc.Option[hikari.Member, arc.MemberParams("Who should handle it.")],
    service: TicketService = arc.inject(),
) -> None:
    require_manage_guild(ctx)
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(member, ticket_type)
    await service.claim(current, member.id, by=ctx.user.id, announce=False)
    await ctx.respond(
        f"📌 Assigned this ticket to {member.mention}.", user_mentions=[member.id]
    )


@ticket.include
@arc.slash_subcommand("close", "Close this ticket.")
async def close(
    ctx: arc.GatewayContext,
    reason: arc.Option[Optional[str], arc.StrParams("Why it's being closed.", max_length=500)] = None,
    service: TicketService = arc.inject(),
) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    if not service.can_close(ctx.member, current, ticket_type):
        raise TicketError("Only the opener or staff can close this ticket.")
    await ctx.respond("🔒 Closing this ticket…")
    await service.close(current, ticket_type, ctx.user.id, reason)


@ticket.include
@arc.slash_subcommand("close-request", "Ask the opener whether this ticket can be closed.")
async def close_request(ctx: arc.GatewayContext, service: TicketService = arc.inject()) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    await service.request_close(current, ctx.user.id)
    await ctx.respond("✅ Asked the opener.", flags=hikari.MessageFlag.EPHEMERAL)


@ticket.include
@arc.slash_subcommand("reopen", "Reopen a ticket closed in the last 7 days.")
async def reopen(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("Ticket number.", min=1)],
    service: TicketService = arc.inject(),
) -> None:
    if not ctx.guild_id or not ctx.member:
        raise TicketError("This command can only be used in a server.")
    await ctx.defer(flags=hikari.MessageFlag.EPHEMERAL)
    reopened = await service.reopen(ctx.guild_id, number, ctx.member)
    await ctx.respond(f"↩️ Reopened ticket #{number}: <#{reopened['channel_id']}>")


async def closed_ticket(service: TicketService, ctx: arc.GatewayContext, number: int) -> tuple[dict, dict]:
    if not ctx.guild_id:
        raise TicketError("This command can only be used in a server.")
    found = await service.repo.get_ticket(ctx.guild_id, number)
    if found is None:
        raise TicketError(f"There's no ticket #{number}.")
    return found, await service.repo.get_type(ctx.guild_id, found["type_id"])  # type: ignore[return-value]


@ticket.include
@arc.slash_subcommand("add", "Let someone else see and reply in this ticket.")
async def add(
    ctx: arc.GatewayContext,
    member: arc.Option[hikari.Member, arc.MemberParams("Who to add.")],
    service: TicketService = arc.inject(),
) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    await service.add_member(current, ticket_type, member.id, ctx.user.id)
    await ctx.respond(f"➕ Added {member.mention} to this ticket.", user_mentions=[member.id])


@ticket.include
@arc.slash_subcommand("remove", "Remove someone you added to this ticket.")
async def remove(
    ctx: arc.GatewayContext,
    member: arc.Option[hikari.User, arc.UserParams("Who to remove.")],
    service: TicketService = arc.inject(),
) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    await service.remove_member(current, member.id, ctx.user.id)
    await ctx.respond(f"➖ Removed {member.mention} from this ticket.", user_mentions=False)


@ticket.include
@arc.slash_subcommand("priority", "Set this ticket's priority (shown in the channel name).")
async def priority(
    ctx: arc.GatewayContext,
    level: arc.Option[str, arc.StrParams("Priority.", choices={p.title(): p for p in PRIORITIES})],
    service: TicketService = arc.inject(),
) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    if current["priority"] == level:
        raise TicketError(f"This ticket is already {level} priority.")
    # Answer first: a rename can wait up to 10 minutes on Discord's rate limit.
    await ctx.respond(f"🚦 Priority set to **{level}**.")
    try:
        await service.set_priority(current, ticket_type, level, ctx.user.id)
    except TicketError as exc:  # rolled back: correct the answer rather than add to it
        await ctx.edit_initial_response(f"❌ {exc}")


@ticket.include
@arc.slash_subcommand("transfer", "Move this ticket to another type (and its staff).")
async def transfer(
    ctx: arc.GatewayContext,
    type: TypeOption,
    service: TicketService = arc.inject(),
) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    new_type = await service.resolve_type(ctx.guild_id, type)  # type: ignore[arg-type]
    await ctx.respond("🔀 Transferring…", flags=hikari.MessageFlag.EPHEMERAL)
    try:
        await service.transfer(current, ticket_type, new_type, ctx.user.id)
    except TicketError as exc:  # answer with the reason, not a follow-up under "Transferring…"
        await ctx.edit_initial_response(f"❌ {exc}")


@ticket.include
@arc.slash_subcommand("note", "Add a private staff note (never shown to the opener).")
async def note(
    ctx: arc.GatewayContext,
    text: arc.Option[str, arc.StrParams("The note.", max_length=2000)],
    service: TicketService = arc.inject(),
) -> None:
    current, ticket_type = await service.ticket_here(ctx.channel_id)
    service.require_staff(ctx.member, ticket_type)
    if not ticket_type["transcripts"]:
        raise TicketError(
            f"Notes are kept in the transcript, and **{ticket_type['name']}** tickets don't keep one."
        )
    await service.repo.add_note(current["id"], ctx.user.id, text)
    await ctx.respond(
        "🗒️ Note saved. Only staff see it, in the transcript (`/ticket transcript get`).",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@transcript_group.include
@arc.slash_subcommand("get", "Download a ticket's transcript, staff notes included.")
async def transcript_get(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("Ticket number.", min=1)],
    service: TicketService = arc.inject(),
) -> None:
    found, ticket_type = await closed_ticket(service, ctx, number)
    service.require_staff(ctx.member, ticket_type)
    transcript = await service.repo.get_transcript(found["id"])
    if transcript is None:
        raise TicketError(f"Ticket #{number} has no transcript.")
    await ctx.respond(
        f"📄 Transcript of ticket #{number}"
        + (" · ⚠️ the opener asked for it to be deleted" if transcript["deletion_requested_at"] else ""),
        attachment=hikari.Bytes(
            render_transcript(transcript["document"], staff=True).encode(), f"ticket-{number}.txt"
        ),
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@transcript_group.include
@arc.slash_subcommand("delete", "Permanently delete a ticket's transcript (admins).")
async def transcript_delete(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("Ticket number.", min=1)],
    service: TicketService = arc.inject(),
) -> None:
    require_manage_guild(ctx)
    found, _ = await closed_ticket(service, ctx, number)
    if not await service.repo.delete_transcript(found["id"]):
        raise TicketError(f"Ticket #{number} has no transcript.")
    await service.repo.add_event(found["id"], "transcript_deleted", ctx.user.id)
    await ctx.respond(f"🗑️ Deleted the transcript of ticket #{number}.", flags=hikari.MessageFlag.EPHEMERAL)


@transcript_group.include
@arc.slash_subcommand("hold", "Keep a transcript as evidence for a mod case, past its expiry.")
async def transcript_hold(
    ctx: arc.GatewayContext,
    number: arc.Option[int, arc.IntParams("Ticket number.", min=1)],
    case: arc.Option[
        Optional[int], arc.IntParams("Case number (leave out to release the hold).", min=1)
    ] = None,
    service: TicketService = arc.inject(),
) -> None:
    found, ticket_type = await closed_ticket(service, ctx, number)
    service.require_staff(ctx.member, ticket_type)
    if not await service.repo.hold_transcript(found["id"], case):
        raise TicketError(f"Ticket #{number} has no transcript.")
    await ctx.respond(
        f"📌 Ticket #{number}'s transcript is kept as evidence for case #{case}."
        if case
        else f"Released the hold on ticket #{number}'s transcript; it expires as normal.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@settings_group.include
@arc.slash_subcommand("log-channel", "Where ticket events are logged (the ticket log).")
async def settings_log_channel(
    ctx: arc.GatewayContext,
    channel: arc.Option[hikari.TextableGuildChannel, arc.ChannelParams("The ticket log channel.")],
    logs: LoggingRepository = arc.inject(),
) -> None:
    # Here rather than /logging ticket-log while Tickets is allowlisted, since /logging is global.
    guild_id = require_manage_guild(ctx)
    await logs.set_log_channel(guild_id, "ticket", channel.id)
    await ctx.respond(
        f"✅ Ticket events will be logged in {channel.mention} (needs the Logging module on).",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@settings_group.include
@arc.slash_subcommand("retention", "How long transcripts are kept by default.")
async def settings_retention(
    ctx: arc.GatewayContext,
    days: arc.Option[int, arc.IntParams("Days (7–365). Types can override it.", min=7, max=365)],
    modules: ModuleManager = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    config = await modules.get_module_config(guild_id, ModuleType.TICKETS)
    await modules.update_module_settings(
        guild_id, ModuleType.TICKETS, {**config.settings, "retention_days": days}
    )
    await ctx.respond(
        f"✅ Transcripts are now kept for {days} days after a ticket closes. "
        "(Already-closed tickets keep their current expiry.)",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


def schedule_from(text: str) -> list:
    try:
        return parse_schedule(text)
    except ValueError as exc:
        raise TicketError(str(exc)) from None


async def timezones(data: arc.AutocompleteData[arc.GatewayClient, str]) -> list[str]:
    typed = (data.focused_value or "").lower().replace(" ", "_")
    return [z for z in sorted(available_timezones()) if typed in z.lower()][:25]


@settings_group.include
@arc.slash_subcommand("hours", "Support hours. Tickets opened outside them get an auto-reply.")
async def settings_hours(
    ctx: arc.GatewayContext,
    schedule: arc.Option[
        str, arc.StrParams('e.g. "mon-fri 09:00-17:00; sat 10:00-14:00", or "off".', max_length=200)
    ],
    timezone: arc.Option[
        Optional[str], arc.StrParams("Where those times are, e.g. Europe/London.", autocomplete_with=timezones)
    ] = None,
    message: arc.Option[
        Optional[str],
        arc.StrParams("The auto-reply. Placeholders: {user} {hours} {next_open}", max_length=1000),
    ] = None,
    modules: ModuleManager = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    if timezone is not None and timezone not in available_timezones():
        raise TicketError(f"“{timezone}” isn't a timezone I know. Pick one from the list.")
    settings = dict((await modules.get_module_config(guild_id, ModuleType.TICKETS)).settings)
    if schedule.strip().lower() == "off":
        settings.pop("schedule", None)
    else:
        settings["schedule"] = schedule_from(schedule)
    if timezone is not None:
        settings["timezone"] = timezone
    if message is not None:
        settings["off_hours_message"] = message
    await modules.update_module_settings(guild_id, ModuleType.TICKETS, settings)
    await service.refresh_panels(guild_id)
    await ctx.respond(
        f"✅ Support hours: {format_schedule(settings['schedule'])} ({settings.get('timezone', 'UTC')})."
        if "schedule" in settings
        else "✅ Support hours are off: staff count as always around.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


@settings_group.include
@arc.slash_subcommand("away", "Away mode: tell members staff are out (holidays, events).")
async def settings_away(
    ctx: arc.GatewayContext,
    on: arc.Option[bool, arc.BoolParams("Turn away mode on or off.")],
    until: arc.Option[
        Optional[str],
        arc.StrParams("When you're back, e.g. 2026-12-27 09:00 (support hours' timezone).", max_length=16),
    ] = None,
    note: arc.Option[Optional[str], arc.StrParams("Added to the auto-reply.", max_length=500)] = None,
    modules: ModuleManager = arc.inject(),
    service: TicketService = arc.inject(),
) -> None:
    guild_id = require_manage_guild(ctx)
    settings = dict((await modules.get_module_config(guild_id, ModuleType.TICKETS)).settings)
    if not on:
        settings.pop("away", None)
    else:
        until_ts = None
        if until:
            try:
                parsed = datetime.fromisoformat(until.strip())
            except ValueError:
                raise TicketError("Write the date like `2026-12-27 09:00`.") from None
            until_ts = int(parsed.replace(tzinfo=ZoneInfo(settings.get("timezone", "UTC"))).timestamp())
            if until_ts <= time.time():
                raise TicketError("That's in the past.")
        settings["away"] = {"until": until_ts, "note": note}
    await modules.update_module_settings(guild_id, ModuleType.TICKETS, settings)
    await service.refresh_panels(guild_id)
    until_ts = settings.get("away", {}).get("until")
    await ctx.respond(
        "✅ Away mode is off." if not on
        else f"🌙 Away mode is on{f' until <t:{until_ts}:F>' if until_ts else ' until you turn it off'}. "
        "New tickets get the auto-reply.",
        flags=hikari.MessageFlag.EPHEMERAL,
    )


# Buttons, dropdowns and forms. custom_ids are "ticket:<action>:<id>" and handled here rather
# than by in-memory component handlers, so they keep working after a restart.


async def reply(interaction: hikari.PartialInteraction, content: str) -> None:
    """An ephemeral answer, whether or not the interaction was already acknowledged."""
    assert isinstance(interaction, (hikari.ComponentInteraction, hikari.ModalInteraction))
    try:
        await interaction.create_initial_response(
            hikari.ResponseType.MESSAGE_CREATE, content, flags=hikari.MessageFlag.EPHEMERAL
        )
    except hikari.BadRequestError:  # already acknowledged: replace the "thinking…" message
        await interaction.edit_initial_response(content)


async def open_from_panel(
    interaction: hikari.ComponentInteraction, service: TicketService, type_id: int
) -> None:
    guild_id: int = interaction.guild_id  # type: ignore[assignment]
    ticket_type = await service.repo.get_type(guild_id, type_id)
    if ticket_type is None:
        raise TicketError("That ticket type no longer exists.")
    # Checked before the form too, so nobody fills one in only to be refused.
    await service.check_can_open(guild_id, interaction.user.id, ticket_type)

    if not ticket_type["fields"]:
        await create_and_confirm(interaction, service, ticket_type, {})
        return
    rows = [
        service.rest.build_modal_action_row().add_text_input(
            f["key"],
            f["label"],
            style=hikari.TextInputStyle.PARAGRAPH
            if f["style"] == "paragraph"
            else hikari.TextInputStyle.SHORT,
            required=f["required"],
            max_length=f.get("max_length", 1000),
        )
        for f in ticket_type["fields"]
    ]
    await interaction.create_modal_response(
        ticket_type["name"][:45], f"ticket:form:{type_id}", components=rows
    )


def modal_values(interaction: hikari.ModalInteraction) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in interaction.components:
        for component in getattr(row, "components", [row]):
            if getattr(component, "custom_id", None) and hasattr(component, "value"):
                values[component.custom_id] = (component.value or "").strip()
    return values


async def submit_form(
    interaction: hikari.ModalInteraction, service: TicketService, type_id: int
) -> None:
    ticket_type = await service.repo.get_type(interaction.guild_id, type_id)  # type: ignore[arg-type]
    if ticket_type is None:
        raise TicketError("That ticket type no longer exists.")
    answers = modal_values(interaction)
    fields = {f["key"]: answers.get(f["key"], "") for f in ticket_type["fields"]}
    missing = [f["label"] for f in ticket_type["fields"] if f["required"] and not fields[f["key"]]]
    if missing:
        raise TicketError(f"Please answer: {', '.join(missing)}.")
    await create_and_confirm(interaction, service, ticket_type, fields)


async def create_and_confirm(
    interaction: hikari.ComponentInteraction | hikari.ModalInteraction,
    service: TicketService,
    ticket_type: dict,
    fields: dict[str, str],
) -> None:
    await interaction.create_initial_response(
        hikari.ResponseType.DEFERRED_MESSAGE_CREATE, flags=hikari.MessageFlag.EPHEMERAL
    )
    ticket = await service.open_ticket(
        interaction.guild_id, interaction.member, ticket_type, fields  # type: ignore[arg-type]
    )
    notice = ticket.get("off_hours_notice")
    await interaction.edit_initial_response(
        f"✅ Your ticket is open: <#{ticket['channel_id']}>" + (f"\n\n{notice}" if notice else "")
    )


async def ticket_for(
    service: TicketService,
    interaction: hikari.ComponentInteraction | hikari.ModalInteraction,
    ticket_id: int,
) -> tuple[dict, dict]:
    """The open ticket a button belongs to, checked against the guild it was pressed in."""
    found = await service.repo.get_ticket_by_id(ticket_id)
    if found is None or found["guild_id"] != interaction.guild_id or found["status"] != "open":
        raise TicketError("This ticket is already closed.")
    return found, await service.repo.get_type(found["guild_id"], found["type_id"])  # type: ignore[return-value]


async def request_deletion(
    interaction: hikari.ComponentInteraction | hikari.ModalInteraction,
    service: TicketService,
    ticket_id: int,
) -> None:
    if await service.request_transcript_deletion(ticket_id, interaction.user.id):
        await reply(interaction, "✅ Your request was sent to the server's admins.")
    else:
        await reply(interaction, "You've already asked, or the transcript is already gone.")


@plugin.listen()
async def on_interaction(event: hikari.InteractionCreateEvent) -> None:
    interaction = event.interaction
    if not isinstance(interaction, (hikari.ComponentInteraction, hikari.ModalInteraction)):
        return
    if not interaction.custom_id.startswith("ticket:"):
        return

    service = plugin.client.get_type_dependency(TicketService)
    _, action, target = interaction.custom_id.split(":", 2)
    try:
        if action == "delreq":  # from the close DM, so there's no guild
            await request_deletion(interaction, service, int(target))
            return
        if not interaction.guild_id:
            return
        if not await service.modules.is_enabled(interaction.guild_id, ModuleType.TICKETS):
            raise TicketError("Tickets are turned off in this server.")
        if action == "open" and isinstance(interaction, hikari.ComponentInteraction):
            await open_from_panel(interaction, service, int(target))
        elif action == "menu" and isinstance(interaction, hikari.ComponentInteraction):
            await open_from_panel(interaction, service, int(interaction.values[0]))
        elif action == "form" and isinstance(interaction, hikari.ModalInteraction):
            await submit_form(interaction, service, int(target))
        elif action == "claim":
            current, ticket_type = await service.ticket_here(interaction.channel_id)
            service.require_staff(interaction.member, ticket_type)
            await service.claim(current, interaction.user.id, announce=False)
            await interaction.create_initial_response(
                hikari.ResponseType.MESSAGE_CREATE, f"🙋 {interaction.user.mention} claimed this ticket."
            )
        elif action == "close" and isinstance(interaction, hikari.ComponentInteraction):
            current, ticket_type = await ticket_for(service, interaction, int(target))
            if not service.can_close(interaction.member, current, ticket_type):
                raise TicketError("Only the opener or staff can close this ticket.")
            row = service.rest.build_modal_action_row().add_text_input(
                "reason", "Reason (optional)", required=False, max_length=500
            )
            await interaction.create_modal_response(
                f"Close ticket #{current['number']}", f"ticket:closewhy:{target}", component=row
            )
        elif action == "closewhy" and isinstance(interaction, hikari.ModalInteraction):
            current, ticket_type = await ticket_for(service, interaction, int(target))
            if not service.can_close(interaction.member, current, ticket_type):
                raise TicketError("Only the opener or staff can close this ticket.")
            await interaction.create_initial_response(
                hikari.ResponseType.MESSAGE_CREATE, "🔒 Closing this ticket…"
            )
            reason = modal_values(interaction).get("reason") or None
            await service.close(current, ticket_type, interaction.user.id, reason)
        elif action in ("closeyes", "closeno") and isinstance(interaction, hikari.ComponentInteraction):
            current, ticket_type = await ticket_for(service, interaction, int(target))
            if interaction.user.id != current["opener_id"]:
                raise TicketError("Only the person who opened this ticket can answer.")
            if action == "closeno":
                await service.repo.add_event(current["id"], "close_declined", interaction.user.id)
                await interaction.create_initial_response(
                    hikari.ResponseType.MESSAGE_UPDATE,
                    f"<@{current['opener_id']}> would like to keep this ticket open.",
                    components=[],
                )
                return
            await interaction.create_initial_response(
                hikari.ResponseType.MESSAGE_UPDATE, "🔒 Closing this ticket…", components=[]
            )
            await service.close(current, ticket_type, interaction.user.id, "Opener agreed to close")
        else:
            await reply(interaction, "This button isn't available yet.")
    except TicketError as exc:
        await reply(interaction, f"❌ {exc}")
    except Exception:
        logger.exception(f"Ticket component {interaction.custom_id} failed")
        await reply(interaction, "❌ Something went wrong. Please try again.")


@plugin.listen()
async def on_message(event: hikari.GuildMessageCreateEvent) -> None:
    await plugin.client.get_type_dependency(TicketService).on_message(event)


@arc.utils.interval_loop(minutes=10)
async def auto_unclaim(service: TicketService) -> None:
    try:
        await service.auto_unclaim(int(time.time()))
    except Exception:
        logger.exception("Ticket auto-unclaim failed")


@arc.utils.interval_loop(minutes=1)
async def panel_statuses(service: TicketService) -> None:
    try:
        await service.refresh_panel_statuses()
    except Exception:
        logger.exception("Ticket panel status refresh failed")


@arc.utils.cron_loop("30 4 * * *")  # nightly, after the 03:30 UTC backup
async def purge_transcripts(service: TicketService) -> None:
    try:
        await service.purge_transcripts(int(time.time()))
    except Exception:
        logger.exception("Ticket transcript purge failed")


@plugin.listen()
async def on_started(_: hikari.StartedEvent) -> None:
    # StartedEvent can fire before main.py's startup hook has registered TicketService.
    await plugin.client.wait_until_started()
    service = plugin.client.get_type_dependency(TicketService)
    auto_unclaim.start(service=service)
    purge_transcripts.start(service=service)
    panel_statuses.start(service=service)


@plugin.listen()
async def on_message_delete(event: hikari.GuildMessageDeleteEvent) -> None:
    """A panel deleted by hand stays configured; /ticket panel edit reposts it."""
    await plugin.client.get_type_dependency(TicketService).panel_message_deleted(event.message_id)


@plugin.listen()
async def on_bulk_message_delete(event: hikari.GuildBulkMessageDeleteEvent) -> None:
    service = plugin.client.get_type_dependency(TicketService)
    for message_id in event.message_ids:
        await service.panel_message_deleted(message_id)


@arc.loader
def load(client: arc.GatewayClient) -> None:
    if not tickets_guilds():  # otherwise the guild-less group would register globally
        logger.info("Tickets not loaded: TICKETS_GUILDS is empty.")
        return
    client.add_plugin(plugin)
    logger.info(f"Ticket commands loaded for {len(tickets_guilds())} server(s).")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    auto_unclaim.stop()
    purge_transcripts.stop()
    panel_statuses.stop()
    if plugin.name in client.plugins:
        client.remove_plugin(plugin)
