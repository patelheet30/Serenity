from __future__ import annotations

from typing import Optional

import arc
import asyncpg
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleNotEnabledError, ModuleType
from serenity.database.ticket_repository import TicketRepository
from serenity.services.ticket_service import TicketError, TicketService, parse_questions
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

ticket = plugin.include_slash_group("ticket", "Support tickets.")
type_group = ticket.include_subgroup("type", "Configure the kinds of ticket members can open.")


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
    }
    values = {k: v for k, v in values.items() if v is not None}
    if questions is not None:
        values["fields"] = [] if questions.strip().lower() == "none" else parse_questions(questions)
    if auto_unclaim_hours is not None:
        values["auto_unclaim_hours"] = auto_unclaim_hours or None
    if not values:
        raise TicketError("Nothing to change.")

    updated = await repo.update_type(guild_id, ticket_type["id"], **values)
    await ctx.respond(
        embed=hikari.Embed(
            title="✅ Ticket type updated",
            description=describe_type(updated, service),  # type: ignore[arg-type]
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
    await repo.update_type(guild_id, ticket_type["id"], staff_role_ids=roles)
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


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Ticket commands loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
