from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, Sequence

import hikari
import toolbox

from serenity.core.modules import ModuleManager, ModuleType
from serenity.database.ticket_repository import TicketRepository
from serenity.services.logging_service import LoggingService
from serenity.utils.errors import SerenityError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

MAX_FIELDS = 5  # a Discord modal holds at most five inputs
MAX_LABEL = 45  # and each input's label is at most 45 characters
PANEL_COLOR = hikari.Color(0x5865F2)
PRIORITIES = ("low", "normal", "high", "urgent")
CATEGORY_LIMIT = 50  # channels Discord allows in one category
DEFAULT_RETENTION_DAYS = 30
REOPEN_WINDOW = 7 * 86400
TRANSCRIPT_MESSAGE_LIMIT = 10_000  # ponytail: a cap, not paging; no real ticket gets close
MAX_STAGES = 25  # the stage dropdown's option limit
TOPIC_INTERVAL = 300  # Discord allows two topic edits per channel per 10 minutes

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


def parse_color(text: str) -> int:
    """A stage colour from "#5865F2" or "5865f2"."""
    try:
        color = int(text.strip().lstrip("#"), 16)
    except ValueError:
        color = -1
    if not 0 <= color <= 0xFFFFFF:
        raise TicketError(f"“{text}” isn't a hex colour like #5865F2.")
    return color


def with_stage(stages: list[dict], stage: dict, position: Optional[int] = None) -> list[dict]:
    """`stages` with `stage` added, or replacing the one with its name in place.

    `position` is 1-based; without it a new stage goes last and an existing one stays put.
    """
    names = [s["name"].lower() for s in stages]
    rest = [s for s in stages if s["name"].lower() != stage["name"].lower()]
    if stage["name"].lower() in names and position is None:
        position = names.index(stage["name"].lower()) + 1
    if len(rest) >= MAX_STAGES:
        raise TicketError(f"A ticket type can have at most {MAX_STAGES} stages.")
    index = len(rest) if position is None else min(max(position, 1) - 1, len(rest))
    return [*rest[:index], stage, *rest[index:]]


def find_stage(ticket_type: dict, name: Optional[str]) -> Optional[dict]:
    if name is None:
        return None
    return next((s for s in ticket_type["stages"] if s["name"].lower() == name.lower()), None)


def render_topic(ticket: dict, ticket_type: dict) -> str:
    """A ticket channel's topic: where it stands and whose turn it is."""
    parts = [f"Ticket #{ticket['number']}", ticket_type["name"]]
    if ticket["stage"]:
        parts.append(ticket["stage"])
    # No opener name: it would need a member fetch on every refresh, and topics don't resolve mentions.
    parts.append(f"waiting on {'staff' if ticket['waiting_on'] == 'staff' else 'member'}")
    return " · ".join(parts)[:1024]


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


def message_entry(message: hikari.Message, color: Optional[int]) -> dict:
    """One message as stored in a transcript: content plus a snapshot of who sent it."""
    author = message.author
    return {
        "id": message.id,
        "author": {
            "id": author.id,
            "name": author.display_name or author.username,
            "avatar": str(author.display_avatar_url),
            "color": color,
            "bot": author.is_bot,
        },
        "content": message.content or "",
        "timestamp": int(message.timestamp.timestamp()),
        "edited": message.edited_timestamp is not None,
        "embeds": [
            {
                "title": e.title,
                "description": e.description,
                "fields": [{"name": f.name, "value": f.value} for f in e.fields],
            }
            for e in message.embeds
        ],
        # Metadata only: files aren't copied, so these links stop working when Discord expires them.
        "attachments": [
            {"filename": a.filename, "size": a.size, "url": a.url, "content_type": a.media_type}
            for a in message.attachments
        ],
    }


def render_transcript(document: dict, *, staff: bool) -> str:
    """A plain-text transcript. Staff notes are only included for staff."""

    def when(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    t = document["ticket"]
    lines = [f"Ticket #{t['number']} · {t['type']}", f"Opened {when(t['opened_at'])} by {t['opener_id']}"]
    if t.get("closed_at"):
        lines.append(f"Closed {when(t['closed_at'])} by {t['closed_by']}: {t.get('reason') or 'no reason'}")
    for label, answer in document["fields"].items():
        lines.append(f"{label}: {answer}")
    lines.append("")
    for m in document["messages"]:
        lines.append(f"[{when(m['timestamp'])}] {m['author']['name']}: {m['content']}")
        for e in m["embeds"]:
            lines.append(f"    [embed] {e.get('title') or ''} {e.get('description') or ''}".rstrip())
        for a in m["attachments"]:
            lines.append(f"    [file] {a['filename']} {a['url']}")
    if staff and document.get("notes"):
        lines += ["", "Staff notes (never shown to the opener):"]
        lines += [f"[{when(n['created_at'])}] {n['author_id']}: {n['body']}" for n in document["notes"]]
    return "\n".join(lines) + "\n"


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
        logging: Optional[LoggingService] = None,
    ) -> None:
        self.repo = repo
        self.rest = rest
        self.cache = cache
        self.modules = modules
        self.logging = logging
        # Channels of open tickets, so the message listener can skip every other message
        # without a query. ponytail: one bot process; shard across processes and this moves to the DB.
        self.open_channels: set[int] = set()
        # Posted panel messages, so a deleted message only costs a query if it was a panel.
        self.panel_messages: set[int] = set()
        # The creation queue: one ticket channel at a time per guild, so limits are checked
        # against settled counts and overflow categories aren't created twice.
        self._open_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Pending topic edits and when each channel's topic was last edited (monotonic).
        # ponytail: in memory, so a restart drops a pending edit until the ticket next changes.
        self._topic_tasks: dict[int, asyncio.Task] = {}
        self._topic_edited: dict[int, float] = {}

    async def load(self) -> None:
        self.open_channels = set(await self.repo.open_ticket_channels())
        self.panel_messages = set(await self.repo.panel_message_ids())

    async def resolve_type(self, guild_id: int, name: str) -> dict:
        ticket_type = await self.repo.get_type_by_name(guild_id, name)
        if ticket_type is None:
            raise TicketError(f"There's no ticket type called **{name}**.")
        return ticket_type

    async def log(
        self,
        ticket: dict,
        ticket_type: dict,
        kind: str,
        actor_id: Optional[int],
        detail: Optional[str] = None,
    ) -> None:
        """Post a ticket event to the ticket log, if the server has one."""
        if self.logging:
            await self.logging.log_ticket_event(
                guild_id=ticket["guild_id"],
                kind=kind,
                number=ticket["number"],
                type_name=ticket_type["name"],
                actor_id=actor_id,
                channel_id=ticket["channel_id"],
                detail=detail,
            )

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
                f"I can't post or edit my panel message in <#{panel['channel_id']}>. Check my View Channel, "
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
                    topic=render_topic(ticket, ticket_type),
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
                except Exception:  # keep the original error as the one that surfaces
                    logger.exception(f"Couldn't remove the channel of failed ticket {ticket['id']}")
                finally:
                    await self.repo.delete_ticket(ticket["id"])
                raise
            self.open_channels.add(channel.id)
            ticket["channel_id"] = channel.id

        try:
            await self.post_welcome(ticket, ticket_type, member.id)
        except Exception:  # Discord or the settings read: the ticket is open and the opener gets its link regardless
            logger.exception(f"Couldn't post the welcome message for ticket {ticket['id']}")
        await self.log(ticket, ticket_type, "opened", member.id)
        return ticket

    async def post_welcome(self, ticket: dict, ticket_type: dict, opener_id: int) -> None:
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
        if ticket_type["stages"]:
            row.add_interactive_button(
                hikari.ButtonStyle.SECONDARY, f"ticket:stagemenu:{ticket['id']}", label="Stage", emoji="🏷️"
            )
        staff = " ".join(f"<@&{r}>" for r in ticket_type["staff_role_ids"])
        await self.rest.create_message(
            ticket["channel_id"],
            f"<@{opener_id}> {staff}".strip(),
            embed=embed,
            components=[row],
            user_mentions=[opener_id],
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
        kind = "assigned" if by else "claimed"
        await self.repo.add_event(ticket["id"], kind, by or user_id, {"assignee": user_id})
        ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
        await self.log(
            updated, ticket_type, kind, by or user_id, f"Assigned to <@{user_id}>" if by else None  # type: ignore[arg-type]
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
        ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
        await self.log(
            ticket, ticket_type, "unclaimed", actor_id,  # type: ignore[arg-type]
            f"<@{ticket['assignee_id']}> " + ("unassigned after no reply" if actor_id is None else "unassigned"),
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
        updated = await self.repo.record_message(ticket["id"], event.author_id, staff=staff)
        if updated["waiting_on"] != ticket["waiting_on"]:
            self.schedule_topic(event.channel_id)
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

    # Closing

    def _role_color(self, guild_id: int, user_id: int) -> Optional[int]:
        member = self.cache.get_member(guild_id, user_id)
        if member is None:
            return None
        colored = [r for r in member.get_roles() if r.color]
        return int(max(colored, key=lambda r: r.position).color) if colored else None

    async def build_transcript(self, ticket: dict, ticket_type: dict) -> dict:
        previous = await self.repo.get_transcript(ticket["id"])  # from before a reopen
        messages = previous["document"]["messages"] if previous else []
        seen = {m["id"] for m in messages}
        colors: dict[int, Optional[int]] = {}
        if ticket["channel_id"]:
            try:
                async for message in self.rest.fetch_messages(
                    ticket["channel_id"], after=hikari.Snowflake.min()
                ).limit(TRANSCRIPT_MESSAGE_LIMIT):
                    if message.id in seen:
                        continue
                    if message.author.id not in colors:
                        colors[message.author.id] = self._role_color(ticket["guild_id"], message.author.id)
                    messages.append(message_entry(message, colors[message.author.id]))
            except (hikari.NotFoundError, hikari.ForbiddenError) as exc:
                # The channel was deleted by hand, or the bot lost access: still close the ticket,
                # with whatever was read, rather than leave it open forever.
                logger.warning(f"Transcript of ticket {ticket['id']} is incomplete: {exc}")
        labels = {f["key"]: f["label"] for f in ticket_type["fields"]}
        return {
            "ticket": {
                "number": ticket["number"],
                "type": ticket_type["name"],
                "opener_id": ticket["opener_id"],
                "opened_at": ticket["opened_at"],
                "closed_at": ticket.get("closed_at"),
                "closed_by": ticket.get("closed_by"),
                "reason": ticket.get("close_reason"),
            },
            "fields": {labels.get(k, k): v for k, v in ticket["fields"].items()},
            "messages": messages,
            "events": [
                {"kind": e["kind"], "actor_id": e["actor_id"], "data": e["data"], "created_at": e["created_at"]}
                for e in await self.repo.list_events(ticket["id"])
            ],
            "notes": [  # staff only
                {"author_id": n["author_id"], "body": n["body"], "created_at": n["created_at"]}
                for n in await self.repo.list_notes(ticket["id"])
            ],
        }

    def can_close(self, member: Optional[hikari.Member], ticket: dict, ticket_type: dict) -> bool:
        return member is not None and (
            member.id == ticket["opener_id"] or self.is_staff(member, ticket_type)
        )

    async def close(
        self, ticket: dict, ticket_type: dict, closed_by: int, reason: Optional[str]
    ) -> dict:
        """Close a ticket: transcript first, then the record, then the channel, then a DM."""
        document: Optional[dict] = None
        if ticket_type["transcripts"]:
            document = await self.build_transcript(ticket, ticket_type)

        closed = await self.repo.close_ticket(ticket["id"], closed_by, reason)
        if closed is None:
            raise TicketError("This ticket is already closed.")

        if document is not None:
            document["ticket"].update(
                closed_at=closed["closed_at"], closed_by=closed_by, reason=reason
            )
            days = await self.retention_days(ticket["guild_id"], ticket_type)
            expires = closed["closed_at"] + days * 86400 if days else None
            try:
                await self.repo.save_transcript(ticket["id"], ticket["guild_id"], document, expires)
            except Exception:  # the channel is kept, and it's the only copy: say where it is
                logger.error(
                    f"Transcript of ticket {ticket['id']} wasn't saved; its messages are still in "
                    f"channel {ticket['channel_id']}"
                )
                raise

        if ticket["channel_id"]:
            self.open_channels.discard(ticket["channel_id"])
            self._topic_edited.pop(ticket["channel_id"], None)
            try:
                await self.rest.delete_channel(ticket["channel_id"], reason=f"Ticket #{ticket['number']} closed")
            except hikari.NotFoundError:
                pass
            except hikari.HTTPError as exc:  # the ticket is closed either way; staff can delete it
                logger.warning(
                    f"Couldn't delete channel {ticket['channel_id']} of closed ticket {ticket['id']}: {exc}"
                )

        await self._dm_closed(closed, ticket_type, closed_by, reason, document)
        # closed has no channel any more; log the one it had, so channel ignores still apply.
        await self.log(
            {**closed, "channel_id": ticket["channel_id"]}, ticket_type, "closed", closed_by,
            reason or "No reason given",
        )
        return closed

    async def _dm_closed(
        self, ticket: dict, ticket_type: dict, closed_by: int, reason: Optional[str], document: Optional[dict]
    ) -> None:
        guild = self.cache.get_guild(ticket["guild_id"])
        embed = hikari.Embed(
            title=f"Your ticket #{ticket['number']} was closed",
            description=f"**{ticket_type['name']}** in **{guild.name if guild else 'the server'}**",
            color=PANEL_COLOR,
        )
        embed.add_field("Closed by", f"<@{closed_by}>", inline=True)
        embed.add_field("Reason", (reason or "No reason given")[:1024], inline=True)
        kwargs: dict = {}
        if document is not None:
            embed.set_footer("The transcript is attached. Staff can reopen this ticket for 7 days.")
            kwargs["attachment"] = hikari.Bytes(
                render_transcript(document, staff=False).encode(), f"ticket-{ticket['number']}.txt"
            )
            kwargs["component"] = self.rest.build_message_action_row().add_interactive_button(
                hikari.ButtonStyle.SECONDARY, f"ticket:delreq:{ticket['id']}",
                label="Ask to delete this transcript",
            )
        try:
            dm = await self.rest.create_dm_channel(ticket["opener_id"])
            await self.rest.create_message(dm, embed=embed, **kwargs)
        except (hikari.ForbiddenError, hikari.NotFoundError, hikari.BadRequestError):
            pass  # DMs closed, or the opener left: they still have nothing to lose
        except hikari.HTTPError as exc:  # the close already happened; don't report it as failed
            logger.warning(f"Couldn't DM the opener of closed ticket {ticket['id']}: {exc}")

    async def request_close(self, ticket: dict, staff_id: int) -> None:
        row = (
            self.rest.build_message_action_row()
            .add_interactive_button(hikari.ButtonStyle.SUCCESS, f"ticket:closeyes:{ticket['id']}", label="Yes, close it")
            .add_interactive_button(hikari.ButtonStyle.SECONDARY, f"ticket:closeno:{ticket['id']}", label="No, keep it open")
        )
        await self.rest.create_message(
            ticket["channel_id"],
            f"<@{ticket['opener_id']}>, <@{staff_id}> thinks this is sorted. Can we close this ticket?",
            component=row,
            user_mentions=[ticket["opener_id"]],
        )
        await self.repo.add_event(ticket["id"], "close_requested", staff_id)

    async def reopen(self, guild_id: int, number: int, actor: hikari.Member) -> dict:
        # Checked inside the guild's queue, so two reopens at once can't both create a channel.
        async with self._open_locks[guild_id]:
            ticket = await self.repo.get_ticket(guild_id, number)
            if ticket is None:
                raise TicketError(f"There's no ticket #{number}.")
            if ticket["status"] == "open":
                raise TicketError(f"Ticket #{number} is still open: <#{ticket['channel_id']}>.")
            if ticket["closed_at"] < time.time() - REOPEN_WINDOW:
                raise TicketError(f"Ticket #{number} closed more than 7 days ago, so it can't be reopened.")
            ticket_type = await self.repo.get_type(guild_id, ticket["type_id"])
            self.require_staff(actor, ticket_type)  # type: ignore[arg-type]

            channel = await self.rest.create_guild_text_channel(
                guild_id,
                self.channel_name(ticket, ticket_type, self._opener_name(ticket)),  # type: ignore[arg-type]
                category=await self._category(guild_id, ticket_type["category_id"]) or hikari.UNDEFINED,  # type: ignore[index]
                permission_overwrites=self.overwrites(guild_id, ticket_type, [ticket["opener_id"]]),  # type: ignore[arg-type]
                # reopen_ticket sets waiting_on back to staff
                topic=render_topic({**ticket, "waiting_on": "staff"}, ticket_type),  # type: ignore[arg-type]
                reason=f"Ticket #{number} reopened by {actor.username}",
            )
            reopened = await self.repo.reopen_ticket(ticket["id"], channel.id, actor.id)
            if reopened is None:  # closed→open by something outside this queue
                await self.rest.delete_channel(channel.id)
                raise TicketError(f"Ticket #{number} was reopened a moment ago.")
            ticket = reopened
            self.open_channels.add(channel.id)

        await self.post_welcome(ticket, ticket_type, ticket["opener_id"])  # type: ignore[arg-type]
        transcript = await self.repo.get_transcript(ticket["id"])
        recent = transcript["document"]["messages"][-10:] if transcript else []
        context = "\n".join(f"**{m['author']['name']}:** {m['content'][:200]}" for m in recent if m["content"])
        embed = hikari.Embed(
            title=f"↩️ Reopened by {actor.display_name or actor.username}",
            description=(f"Last messages before it closed:\n{context}" if context else "No earlier messages.")[:4096],
            color=PANEL_COLOR,
        )
        await self.rest.create_message(channel.id, embed=embed)
        await self.log(ticket, ticket_type, "reopened", actor.id)  # type: ignore[arg-type]
        return ticket

    async def purge_transcripts(self, now: int) -> None:
        purged = await self.repo.purge_expired_transcripts(now)
        if purged:
            logger.info(f"Deleted {purged} expired ticket transcript(s).")

    async def request_transcript_deletion(self, ticket_id: int, user_id: int) -> bool:
        """The opener asks for their transcript to be deleted; admins see it in the ticket log."""
        ticket = await self.repo.get_ticket_by_id(ticket_id)
        if ticket is None or ticket["opener_id"] != user_id:
            raise TicketError("That transcript isn't yours.")
        if not await self.repo.request_transcript_deletion(ticket_id):
            return False
        await self.repo.add_event(ticket_id, "deletion_requested", user_id)
        ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
        await self.log(
            ticket, ticket_type, "deletion_requested", user_id,  # type: ignore[arg-type]
            f"Delete it with `/ticket transcript delete {ticket['number']}`.",
        )
        return True

    # Staff controls

    def channel_name(self, ticket: dict, ticket_type: dict, opener_name: str) -> str:
        """The ticket's channel name, prefixed with its priority unless it's normal."""
        name = render_channel_name(
            ticket_type["name_template"],
            type_name=ticket_type["name"],
            number=ticket["number"],
            username=opener_name,
            fields=ticket["fields"],
        )
        return name if ticket["priority"] == "normal" else f"{ticket['priority']}-{name}"[:100]

    def _opener_name(self, ticket: dict) -> str:
        opener = self.cache.get_member(ticket["guild_id"], ticket["opener_id"])
        return opener.username if opener else str(ticket["opener_id"])

    async def add_member(self, ticket: dict, ticket_type: dict, user_id: int, actor_id: int) -> None:
        await self.rest.edit_permission_overwrite(
            ticket["channel_id"], user_id, target_type=hikari.PermissionOverwriteType.MEMBER,
            allow=TICKET_MEMBER_ALLOW, reason=f"Added to ticket #{ticket['number']}",
        )
        await self.repo.add_event(ticket["id"], "member_added", actor_id, {"user": user_id})

    async def remove_member(self, ticket: dict, user_id: int, actor_id: int) -> None:
        if user_id == ticket["opener_id"]:
            raise TicketError("The opener can't be removed from their own ticket. Close it instead.")
        try:
            await self.rest.delete_permission_overwrite(ticket["channel_id"], user_id)
        except hikari.NotFoundError:
            raise TicketError(f"<@{user_id}> wasn't added to this ticket.") from None
        await self.repo.add_event(ticket["id"], "member_removed", actor_id, {"user": user_id})

    async def set_priority(self, ticket: dict, ticket_type: dict, priority: str, actor_id: int) -> dict:
        updated = await self.repo.update_ticket(ticket["id"], priority=priority)
        # Discord allows 2 renames per 10 minutes; past that hikari waits for the window, so
        # /ticket priority answers before calling this.
        try:
            await self.rest.edit_channel(
                ticket["channel_id"], name=self.channel_name(updated, ticket_type, self._opener_name(ticket))  # type: ignore[arg-type]
            )
        except hikari.ForbiddenError:
            await self.repo.update_ticket(ticket["id"], priority=ticket["priority"])
            raise TicketError(
                "I couldn't rename this channel, so the priority wasn't changed. "
                "I need Manage Channels here."
            ) from None
        await self.repo.add_event(
            ticket["id"], "priority", actor_id, {"from": ticket["priority"], "to": priority}
        )
        return updated  # type: ignore[return-value]

    async def transfer(self, ticket: dict, old_type: dict, new_type: dict, actor_id: int) -> dict:
        """Move a ticket to another type: its category, staff roles and channel name follow."""
        if new_type["id"] == old_type["id"]:
            raise TicketError(f"This is already a **{new_type['name']}** ticket.")
        # Keep everyone added to the ticket; replace only the role overwrites. The overwrite list
        # replaces the channel's whole set, so read it from Discord if the cache doesn't have it.
        try:
            channel = self.cache.get_guild_channel(ticket["channel_id"]) or await self.rest.fetch_channel(
                ticket["channel_id"]
            )
        except (hikari.NotFoundError, hikari.ForbiddenError):
            raise TicketError("I can't read this channel's permissions, so I can't move it.") from None
        overwrites = channel.permission_overwrites.values()  # type: ignore[union-attr]
        me = self.cache.get_me()
        members = [
            ow.id
            for ow in overwrites
            if ow.type == hikari.PermissionOverwriteType.MEMBER
            and ow.allow & hikari.Permissions.VIEW_CHANNEL
            and not (me and ow.id == me.id)
        ] or [ticket["opener_id"]]

        async with self._open_locks[ticket["guild_id"]]:  # _category may create an overflow
            category = await self._category(ticket["guild_id"], new_type["category_id"])
            # Stages are per type, so the ticket starts the new type's workflow.
            first = new_type["stages"][0]["name"] if new_type["stages"] else None
            updated = await self.repo.update_ticket(ticket["id"], type_id=new_type["id"], stage=first)
            await self.rest.edit_channel(
                ticket["channel_id"],
                parent_category=category if category is not None else hikari.UNDEFINED,
                permission_overwrites=self.overwrites(ticket["guild_id"], new_type, members),
                reason=f"Ticket #{ticket['number']} moved to {new_type['name']}",
            )
        # Renamed outside the queue: a rename can wait 10 minutes on Discord's limit, and every
        # open in the guild waits on the queue.
        await self.rest.edit_channel(
            ticket["channel_id"],
            name=self.channel_name(updated, new_type, self._opener_name(ticket)),  # type: ignore[arg-type]
        )
        if updated["assignee_id"]:  # type: ignore[index]  # the old type's staff may not see it any more
            updated = await self.repo.unclaim(ticket["id"])
        await self.repo.add_event(
            ticket["id"], "transferred", actor_id, {"from": old_type["id"], "to": new_type["id"]}
        )
        await self.log(updated, new_type, "transferred", actor_id, f"From **{old_type['name']}**")  # type: ignore[arg-type]
        roles = " ".join(f"<@&{r}>" for r in new_type["staff_role_ids"])
        await self.rest.create_message(
            ticket["channel_id"],
            f"🔀 Moved to **{self.type_label(new_type)}** by <@{actor_id}>. {roles}".strip(),
            role_mentions=new_type["staff_role_ids"],
        )
        self.schedule_topic(ticket["channel_id"])
        return updated  # type: ignore[return-value]

    # Stages

    async def set_stage(self, ticket: dict, ticket_type: dict, name: str, actor_id: int) -> dict:
        """Move a ticket to one of its type's stages; a terminal stage offers to close."""
        stage = find_stage(ticket_type, name)
        if stage is None:
            raise TicketError(f"**{ticket_type['name']}** tickets have no stage called **{name}**.")
        if ticket["stage"] == stage["name"]:
            raise TicketError(f"This ticket is already at **{stage['name']}**.")
        updated = await self.repo.update_ticket(ticket["id"], stage=stage["name"])
        await self.repo.add_event(
            ticket["id"], "stage", actor_id, {"from": ticket["stage"], "to": stage["name"]}
        )
        # In the channel, so the transcript shows it too.
        embed = hikari.Embed(
            description=f"🏷️ <@{actor_id}> moved this ticket to **{stage['name']}**.",
            color=hikari.Color(stage["color"]) if stage["color"] is not None else PANEL_COLOR,
        )
        rows = []
        if stage["terminal"]:
            rows.append(
                self.rest.build_message_action_row().add_interactive_button(
                    hikari.ButtonStyle.DANGER, f"ticket:closeas:{ticket['id']}",
                    label=f"Close as {stage['name']}"[:80], emoji="🔒",
                )
            )
        await self.rest.create_message(ticket["channel_id"], embed=embed, components=rows)
        await self.log(
            updated, ticket_type, "stage", actor_id, f"{ticket['stage'] or 'No stage'} → {stage['name']}"  # type: ignore[arg-type]
        )
        self.schedule_topic(ticket["channel_id"])
        return updated  # type: ignore[return-value]

    def schedule_topic(self, channel_id: int) -> None:
        """Bring a ticket channel's topic up to date, at most once per TOPIC_INTERVAL.

        A burst of changes becomes one edit: the pending one reads the ticket when it runs.
        """
        if channel_id in self._topic_tasks:
            return
        delay = max(0.0, self._topic_edited.get(channel_id, -TOPIC_INTERVAL) + TOPIC_INTERVAL - time.monotonic())
        self._topic_tasks[channel_id] = asyncio.create_task(self._edit_topic(channel_id, delay))

    async def _edit_topic(self, channel_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        finally:
            self._topic_tasks.pop(channel_id, None)  # changes from here on schedule a new edit
        try:
            ticket = await self.repo.get_ticket_by_channel(channel_id)
            if ticket is None or ticket["status"] != "open":
                return
            ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
            topic = render_topic(ticket, ticket_type)  # type: ignore[arg-type]
            channel = self.cache.get_guild_channel(channel_id)
            if channel is not None and getattr(channel, "topic", None) == topic:
                return
            self._topic_edited[channel_id] = time.monotonic()
            await self.rest.edit_channel(channel_id, topic=topic)
        except Exception as exc:  # a topic is cosmetic; never let it break the ticket
            logger.warning(f"Couldn't update the topic of ticket channel {channel_id}: {exc}")
