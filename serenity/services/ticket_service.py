from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Optional, Sequence

import hikari
import toolbox

from serenity.core.modules import ModuleManager, ModuleType
from serenity.database.ticket_repository import TicketRepository
from serenity.utils.errors import SerenityError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

MAX_FIELDS = 5  # a Discord modal holds at most five inputs
MAX_LABEL = 45  # and each input's label is at most 45 characters
PANEL_COLOR = hikari.Color(0x5865F2)
CATEGORY_LIMIT = 50  # channels Discord allows in one category
DEFAULT_RETENTION_DAYS = 30

# What the opener and staff can do in a ticket channel.
TICKET_MEMBER_ALLOW = (
    hikari.Permissions.VIEW_CHANNEL
    | hikari.Permissions.SEND_MESSAGES
    | hikari.Permissions.READ_MESSAGE_HISTORY
    | hikari.Permissions.ATTACH_FILES
    | hikari.Permissions.EMBED_LINKS
)
BOT_ALLOW = TICKET_MEMBER_ALLOW | hikari.Permissions.MANAGE_CHANNELS | hikari.Permissions.MANAGE_MESSAGES


class TicketError(SerenityError):
    """A ticket action the member can't do; the message is shown to them as is."""


def parse_questions(text: str) -> list[dict]:
    """`/ticket type` questions ("Order number | What went wrong?") as form fields.

    ponytail: every field is a required paragraph; per-field style and optional fields come
    with the dashboard's type editor.
    """
    labels = [q.strip() for q in text.split("|") if q.strip()]
    if len(labels) > MAX_FIELDS:
        raise TicketError(f"A form can have at most {MAX_FIELDS} questions.")
    fields: list[dict] = []
    for label in labels:
        if len(label) > MAX_LABEL:
            raise TicketError(f"Questions can be at most {MAX_LABEL} characters: “{label}”.")
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32] or f"q{len(fields) + 1}"
        while key in {f["key"] for f in fields}:
            key += "_"
        fields.append(
            {"key": key, "label": label, "style": "paragraph", "required": True, "max_length": 1000}
        )
    return fields


def render_channel_name(
    template: str, *, type_name: str, number: int, username: str, fields: dict[str, str]
) -> str:
    """A ticket's channel name from its type's template, made safe for Discord."""

    def field(match: re.Match) -> str:
        return fields.get(match.group(1), "")[:20]

    name = re.sub(r"\{field:([a-z0-9_]+)\}", field, template)
    name = name.replace("{type}", type_name).replace("{number}", str(number))
    name = name.replace("{user}", username)
    name = re.sub(r"[^\w-]+", "-", name.lower()).strip("-")
    name = re.sub(r"-{2,}", "-", name)[:100]
    return name or f"ticket-{number}"


def overflow_name(base: str, index: int) -> str:
    """The category tickets overflow into once `base` is full: "Support 2", "Support 3", ..."""
    return base if index == 1 else f"{base} {index}"


def is_staff(
    ticket_type: dict, role_ids: Sequence[int], permissions: hikari.Permissions
) -> bool:
    """Staff of a type: one of its staff roles, or Manage Server."""
    if permissions & (hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR):
        return True
    return any(role in ticket_type["staff_role_ids"] for role in role_ids)


def member_permissions(member: hikari.Member) -> hikari.Permissions:
    if isinstance(member, hikari.InteractionMember):
        return member.permissions
    try:
        return toolbox.calculate_permissions(member)
    except Exception:  # roles missing from the cache
        return hikari.Permissions.NONE


class TicketService:
    """Ticket flows shared by the slash commands and the panel/ticket buttons."""

    def __init__(
        self,
        repo: TicketRepository,
        rest: hikari.api.RESTClient,
        cache: hikari.api.Cache,
        modules: ModuleManager,
    ) -> None:
        self.repo = repo
        self.rest = rest
        self.cache = cache
        self.modules = modules
        # Channels of open tickets, so the message listener can skip every other message
        # without a query. ponytail: one bot process; shard across processes and this moves to the DB.
        self.open_channels: set[int] = set()
        # Posted panel messages, so a deleted message only costs a query if it was a panel.
        self.panel_messages: set[int] = set()
        # The creation queue: one ticket channel at a time per guild, so limits are checked
        # against settled counts and overflow categories aren't created twice.
        self._open_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def load(self) -> None:
        self.open_channels = set(await self.repo.open_ticket_channels())
        self.panel_messages = set(await self.repo.panel_message_ids())

    async def resolve_type(self, guild_id: int, name: str) -> dict:
        ticket_type = await self.repo.get_type_by_name(guild_id, name)
        if ticket_type is None:
            raise TicketError(f"There's no ticket type called **{name}**.")
        return ticket_type

    def type_label(self, ticket_type: dict) -> str:
        emoji: Optional[str] = ticket_type["emoji"]
        return f"{emoji} {ticket_type['name']}" if emoji else ticket_type["name"]

    # Panels

    def build_panel(
        self, panel: dict, types: list[dict]
    ) -> tuple[hikari.Embed, list[hikari.api.MessageActionRowBuilder]]:
        """The panel message: one button per type (5 per row), or a dropdown of them."""
        embed = hikari.Embed(
            title=panel["title"], description=panel["description"], color=PANEL_COLOR
        )
        rows: list[hikari.api.MessageActionRowBuilder] = []
        if panel["style"] == "menu":
            menu = self.rest.build_message_action_row().add_text_menu(
                f"ticket:menu:{panel['id']}", placeholder="Choose what you need help with"
            )
            for t in types:
                menu.add_option(
                    t["name"], str(t["id"]), description=t["description"] or hikari.UNDEFINED,
                    emoji=t["emoji"] or hikari.UNDEFINED,
                )
            rows.append(menu.parent)
        else:
            for i, t in enumerate(types):
                if i % 5 == 0:
                    rows.append(self.rest.build_message_action_row())
                rows[-1].add_interactive_button(
                    hikari.ButtonStyle.PRIMARY, f"ticket:open:{t['id']}",
                    label=t["name"], emoji=t["emoji"] or hikari.UNDEFINED,
                )
        return embed, rows

    async def _panel_types(self, panel: dict) -> list[dict]:
        types = {t["id"]: t for t in await self.repo.list_types(panel["guild_id"])}
        return [types[i] for i in panel["type_ids"] if i in types]

    async def post_panel(self, panel: dict) -> dict:
        """Send the panel to its channel, or edit it in place if it's already there."""
        types = await self._panel_types(panel)
        if not types:
            raise TicketError("A panel needs at least one ticket type.")
        embed, rows = self.build_panel(panel, types)
        try:
            if panel["message_id"]:
                try:
                    await self.rest.edit_message(
                        panel["channel_id"], panel["message_id"], embed=embed, components=rows
                    )
                    return panel
                except hikari.NotFoundError:  # deleted while we weren't looking: post a new one
                    self.panel_messages.discard(panel["message_id"])
            message = await self.rest.create_message(
                panel["channel_id"], embed=embed, components=rows
            )
        except hikari.ForbiddenError:
            raise TicketError(
                f"I can't post in <#{panel['channel_id']}>. Check my View Channel, "
                "Send Messages and Embed Links permissions there."
            ) from None
        self.panel_messages.add(message.id)
        return await self.repo.update_panel(  # type: ignore[return-value]
            panel["guild_id"], panel["id"], message_id=message.id
        )

    async def delete_panel(self, panel: dict) -> None:
        if panel["message_id"]:
            try:
                await self.rest.delete_message(panel["channel_id"], panel["message_id"])
            except (hikari.NotFoundError, hikari.ForbiddenError):
                pass
        self.panel_messages.discard(panel["message_id"])
        await self.repo.delete_panel(panel["guild_id"], panel["id"])

    async def panel_message_deleted(self, message_id: int) -> None:
        if message_id in self.panel_messages:
            self.panel_messages.discard(message_id)
            await self.repo.forget_panel_message(message_id)

    async def refresh_panels(self, guild_id: int, type_id: Optional[int] = None) -> None:
        """Re-render posted panels after a type they show changed."""
        for panel in await self.repo.list_panels(guild_id):
            if panel["message_id"] and (type_id is None or type_id in panel["type_ids"]):
                try:
                    await self.post_panel(panel)
                except (TicketError, hikari.HTTPError) as exc:  # one broken panel can't stop the rest
                    logger.warning(f"Couldn't refresh ticket panel {panel['id']}: {exc}")

    # Opening

    async def retention_days(self, guild_id: int, ticket_type: dict) -> int:
        """Days a transcript of this type is kept; 0 means until deleted."""
        if ticket_type["retention_days"] is not None:
            return ticket_type["retention_days"]
        config = await self.modules.get_module_config(guild_id, ModuleType.TICKETS)
        return int(config.settings.get("retention_days", DEFAULT_RETENTION_DAYS))

    async def check_can_open(self, guild_id: int, user_id: int, ticket_type: dict) -> None:
        """Raise if this member may not open a ticket of this type right now."""
        if await self.repo.get_blacklist_entry(guild_id, user_id):
            raise TicketError("You can't open tickets in this server.")
        limit = ticket_type["open_limit"]
        if await self.repo.count_open(guild_id, user_id, ticket_type["id"]) >= limit:
            raise TicketError(
                f"You already have {'an open' if limit == 1 else f'{limit} open'} "
                f"**{ticket_type['name']}** ticket{'' if limit == 1 else 's'}."
            )

    def overwrites(
        self, guild_id: int, ticket_type: dict, member_ids: Sequence[int]
    ) -> list[hikari.PermissionOverwrite]:
        ows = [
            hikari.PermissionOverwrite(
                id=hikari.Snowflake(guild_id),  # @everyone
                type=hikari.PermissionOverwriteType.ROLE,
                deny=hikari.Permissions.VIEW_CHANNEL,
            )
        ]
        ows += [
            hikari.PermissionOverwrite(
                id=hikari.Snowflake(role), type=hikari.PermissionOverwriteType.ROLE,
                allow=TICKET_MEMBER_ALLOW,
            )
            for role in ticket_type["staff_role_ids"]
        ]
        ows += [
            hikari.PermissionOverwrite(
                id=hikari.Snowflake(user), type=hikari.PermissionOverwriteType.MEMBER,
                allow=TICKET_MEMBER_ALLOW,
            )
            for user in member_ids
        ]
        me = self.cache.get_me()
        if me:
            ows.append(
                hikari.PermissionOverwrite(
                    id=me.id, type=hikari.PermissionOverwriteType.MEMBER, allow=BOT_ALLOW
                )
            )
        return ows

    async def _category(self, guild_id: int, base_id: Optional[int]) -> Optional[int]:
        """Where a new ticket channel goes: the type's category, or its first overflow with room.

        ponytail: counts come from the cache, which can trail a just-created channel by a moment;
        if Discord rejects a 51st channel the open fails with a retryable error.
        """
        if base_id is None:
            return None
        channels = list(self.cache.get_guild_channels_view_for_guild(guild_id).values())
        base = next((c for c in channels if c.id == base_id), None)
        if base is None:
            raise TicketError("This ticket type's category no longer exists. Ask an admin to fix it.")

        def children(category_id: int) -> int:
            return sum(1 for c in channels if c.parent_id == category_id)

        by_name = {c.name: c for c in channels if c.type == hikari.ChannelType.GUILD_CATEGORY}
        for index in range(1, 11):
            category = base if index == 1 else by_name.get(overflow_name(base.name or "", index))
            if category is None:
                created = await self.rest.create_guild_category(
                    guild_id,
                    overflow_name(base.name or "Tickets", index),
                    position=base.position + index - 1,
                    permission_overwrites=list(base.permission_overwrites.values()),
                    reason="Ticket category full",
                )
                return created.id
            if children(category.id) < CATEGORY_LIMIT:
                return category.id
        raise TicketError("Every ticket category is full. Ask an admin to close some tickets.")

    async def open_ticket(
        self, guild_id: int, member: hikari.Member, ticket_type: dict, fields: dict[str, str]
    ) -> dict:
        """Create a ticket and its channel, and post the welcome message."""
        async with self._open_locks[guild_id]:
            await self.check_can_open(guild_id, member.id, ticket_type)
            ticket = await self.repo.create_ticket(guild_id, ticket_type["id"], member.id, fields)
            try:
                category_id = await self._category(guild_id, ticket_type["category_id"])
                channel = await self.rest.create_guild_text_channel(
                    guild_id,
                    render_channel_name(
                        ticket_type["name_template"],
                        type_name=ticket_type["name"],
                        number=ticket["number"],
                        username=member.username,
                        fields=fields,
                    ),
                    category=category_id if category_id is not None else hikari.UNDEFINED,
                    permission_overwrites=self.overwrites(guild_id, ticket_type, [member.id]),
                    topic=f"Ticket #{ticket['number']} · {ticket_type['name']} · opened by {member.username}",
                    reason=f"Ticket #{ticket['number']} opened by {member.username}",
                )
            except BaseException as exc:
                await self.repo.delete_ticket(ticket["id"])
                if isinstance(exc, hikari.ForbiddenError):
                    raise TicketError(
                        "I couldn't create your ticket channel. An admin needs to give me "
                        "Manage Channels and Manage Roles."
                    ) from None
                raise
            try:
                await self.repo.set_ticket_channel(ticket["id"], channel.id)
            except BaseException:  # don't leave a channel and an open ticket nobody can find
                try:
                    await self.rest.delete_channel(channel.id)
                except hikari.HTTPError:
                    logger.exception(f"Couldn't remove the channel of failed ticket {ticket['id']}")
                finally:
                    await self.repo.delete_ticket(ticket["id"])
                raise
            self.open_channels.add(channel.id)
            ticket["channel_id"] = channel.id

        try:
            await self.post_welcome(ticket, ticket_type, member)
        except hikari.HTTPError:  # the ticket is open and the opener gets its link regardless
            logger.exception(f"Couldn't post the welcome message for ticket {ticket['id']}")
        return ticket

    async def post_welcome(self, ticket: dict, ticket_type: dict, opener: hikari.User) -> None:
        days = await self.retention_days(ticket["guild_id"], ticket_type)
        if not ticket_type["transcripts"]:
            privacy = "This ticket isn't recorded."
        elif days == 0:
            privacy = "This ticket is recorded; the transcript is kept until an admin deletes it."
        else:
            privacy = f"This ticket is recorded; the transcript is kept for {days} days after it closes."

        embed = hikari.Embed(
            title=f"{self.type_label(ticket_type)} · #{ticket['number']}",
            description="Thanks for reaching out. Staff will be with you shortly.",
            color=PANEL_COLOR,
        )
        labels = {f["key"]: f["label"] for f in ticket_type["fields"]}
        for key, answer in ticket["fields"].items():
            embed.add_field(labels.get(key, key), answer[:1024] or "—")
        embed.set_footer(privacy)

        row = self.rest.build_message_action_row().add_interactive_button(
            hikari.ButtonStyle.DANGER, f"ticket:close:{ticket['id']}", label="Close", emoji="🔒"
        )
        if ticket_type["claim_mode"] in ("button", "both"):
            row.add_interactive_button(
                hikari.ButtonStyle.SUCCESS, f"ticket:claim:{ticket['id']}", label="Claim", emoji="🙋"
            )
        staff = " ".join(f"<@&{r}>" for r in ticket_type["staff_role_ids"])
        await self.rest.create_message(
            ticket["channel_id"],
            f"{opener.mention} {staff}".strip(),
            embed=embed,
            components=[row],
            user_mentions=[opener.id],
            role_mentions=ticket_type["staff_role_ids"],
        )

    # Claiming

    async def ticket_here(self, channel_id: int) -> tuple[dict, dict]:
        """The open ticket in this channel and its type."""
        ticket = await self.repo.get_ticket_by_channel(channel_id)
        if ticket is None or ticket["status"] != "open":
            raise TicketError("This isn't an open ticket channel.")
        ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
        return ticket, ticket_type  # type: ignore[return-value]

    def is_staff(self, member: hikari.Member, ticket_type: dict) -> bool:
        return is_staff(ticket_type, member.role_ids, member_permissions(member))

    def require_staff(self, member: Optional[hikari.Member], ticket_type: dict) -> hikari.Member:
        if member is None or not self.is_staff(member, ticket_type):
            raise TicketError(f"Only **{ticket_type['name']}** staff can do that.")
        return member

    async def claim(
        self, ticket: dict, user_id: int, *, by: Optional[int] = None, announce: bool = True
    ) -> dict:
        """Give the ticket to `user_id`. `by` is the admin reassigning it, if it isn't them."""
        updated = await self.repo.claim(ticket["id"], user_id, force=by is not None)
        if updated is None:
            current = await self.repo.get_ticket_by_id(ticket["id"])
            assignee = current["assignee_id"] if current else None
            raise TicketError(f"<@{assignee}> already has this ticket." if assignee else "This ticket is closed.")
        await self.repo.add_event(
            ticket["id"], "assigned" if by else "claimed", by or user_id, {"assignee": user_id}
        )
        if announce and ticket["channel_id"]:
            text = (
                f"📌 <@{by}> assigned this ticket to <@{user_id}>."
                if by
                else f"🙋 <@{user_id}> claimed this ticket."
            )
            await self.rest.create_message(ticket["channel_id"], text, user_mentions=[user_id])
        return updated

    async def unclaim(self, ticket: dict, actor_id: Optional[int], note: str) -> bool:
        """Unassign `ticket`'s assignee, unless someone else has claimed it since."""
        if await self.repo.unclaim(ticket["id"], expected=ticket["assignee_id"]) is None:
            return False
        await self.repo.add_event(
            ticket["id"], "unclaimed", actor_id, {"previous": ticket["assignee_id"]}
        )
        if ticket["channel_id"]:
            await self.rest.create_message(ticket["channel_id"], note, role_mentions=True)
        return True

    async def on_message(self, event: hikari.GuildMessageCreateEvent) -> None:
        """Count replies in ticket channels; a first staff reply claims in reply mode."""
        if event.channel_id not in self.open_channels or event.is_bot or event.member is None:
            return
        ticket = await self.repo.get_ticket_by_channel(event.channel_id)
        if ticket is None or ticket["status"] != "open":
            return
        ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
        staff = event.author_id != ticket["opener_id"] and self.is_staff(event.member, ticket_type)  # type: ignore[arg-type]
        await self.repo.record_message(ticket["id"], event.author_id, staff=staff)
        if staff and ticket["assignee_id"] is None and ticket_type["claim_mode"] in ("reply", "both"):  # type: ignore[index]
            try:
                await self.claim(ticket, event.author_id, announce=False)
            except TicketError:
                pass  # someone claimed it a moment ago

    async def auto_unclaim(self, now: int) -> None:
        """Unclaim tickets whose opener has waited too long, and ping the type's staff."""
        for ticket in await self.repo.due_for_unclaim(now):
            roles = " ".join(f"<@&{r}>" for r in ticket["staff_role_ids"])
            try:
                await self.unclaim(
                    ticket,
                    None,
                    f"⏰ No staff reply for {ticket['auto_unclaim_hours']}h, so <@{ticket['assignee_id']}> "
                    f"was unassigned. {roles}".strip(),
                )
            except Exception:  # one deleted or locked channel can't hold up the rest
                logger.exception(f"Auto-unclaim failed for ticket {ticket['id']}")
