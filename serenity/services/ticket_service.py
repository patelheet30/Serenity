from __future__ import annotations

import asyncio
import math
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

import hikari
import toolbox

from serenity.core.modules import ModuleManager, ModuleType
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.logging_service import LoggingService
from serenity.services.moderation_service import ModerationService
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

# What the opener and staff can do in a ticket channel.
TICKET_MEMBER_ALLOW = (
    hikari.Permissions.VIEW_CHANNEL
    | hikari.Permissions.SEND_MESSAGES
    | hikari.Permissions.READ_MESSAGE_HISTORY
    | hikari.Permissions.ATTACH_FILES
    | hikari.Permissions.EMBED_LINKS
)
BOT_ALLOW = TICKET_MEMBER_ALLOW | hikari.Permissions.MANAGE_CHANNELS | hikari.Permissions.MANAGE_MESSAGES
# A ticket's Mod action, and the permission the moderator needs for it (as for the slash commands).
MOD_ACTIONS = {
    "warn": hikari.Permissions.MODERATE_MEMBERS,
    "timeout": hikari.Permissions.MODERATE_MEMBERS,
    "kick": hikari.Permissions.KICK_MEMBERS,
    "ban": hikari.Permissions.BAN_MEMBERS,
}
MAX_TIMEOUT = 28 * 86400  # Discord's limit


def parse_duration(text: str) -> int:
    """"30m", "2h" or "7d" in seconds, up to Discord's 28-day timeout limit."""
    match = re.fullmatch(r"\s*(\d{1,4})\s*([mhd])\s*", text.lower())
    if not match:
        raise TicketError("Give the duration as minutes, hours or days, like 30m, 2h or 7d.")
    seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400}[match.group(2)]
    if not 60 <= seconds <= MAX_TIMEOUT:
        raise TicketError("A timeout is between 1 minute and 28 days.")
    return seconds


class TicketError(SerenityError):
    """A ticket action the member can't do; the message is shown to them as is."""


FIELD_TYPES = ("short", "paragraph", "number", "date", "select", "url", "user", "pattern")
PICKED = ("select", "user")  # asked with select menus after the form, since modals only hold text
MAX_PICKED = 4  # those menus' rows, plus the row with the Open button
MAX_OPTIONS = 25  # options in one select menu
MAX_LENGTH = {"paragraph": 1000}  # answer length; 100 for everything else
DRAFT_TTL = 15 * 60  # seconds a half-filled form waits for its dropdown picks
MAX_DRAFTS = 1000  # half-filled forms kept at once, across guilds


def field_type(field: dict) -> str:
    return field.get("type") or field.get("style", "paragraph")  # fields from before #60 only have a style


def _field_key(label: str, fields: list[dict]) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32] or f"q{len(fields) + 1}"
    while key in {f["key"] for f in fields}:
        key += "_"
    return key


MAX_OPEN_REPEATS = 3  # on a 100-character answer, at most ~100^3 steps


def _slow_pattern(parsed: Any) -> bool:
    """Whether a parsed regex can backtrack badly. Patterns run on the event loop, and Python's
    re has no timeout, so the shapes behind catastrophic backtracking are refused up front:
    a repeat or `|` inside an open-ended repeat, backreferences, and many open-ended repeats.

    ponytail: reads re's private parse tree and is deliberately strict; a `regex`-module
    timeout would allow more if admins need richer patterns.
    """
    from re import _constants as c  # type: ignore[attr-defined]

    open_repeats = 0

    def walk(items: Any, in_repeat: bool) -> bool:
        nonlocal open_repeats
        for op, av in items:
            if op in (c.MAX_REPEAT, c.MIN_REPEAT, c.POSSESSIVE_REPEAT):
                _, high, sub = av
                if in_repeat and high > 1:
                    return True
                open_ended = high == c.MAXREPEAT
                open_repeats += open_ended
                if walk(sub, in_repeat or open_ended):
                    return True
            elif op == c.BRANCH:
                if in_repeat or any(walk(branch, in_repeat) for branch in av[1]):
                    return True
            elif op in (c.GROUPREF, c.GROUPREF_EXISTS):
                return True
            elif op in (c.SUBPATTERN, c.ATOMIC_GROUP, c.ASSERT, c.ASSERT_NOT):
                if walk(av[-1], in_repeat):
                    return True
        return False

    return walk(parsed, False) or open_repeats > MAX_OPEN_REPEATS


def parse_questions(text: str) -> list[dict]:
    """`/ticket type` questions ("Order number | What went wrong?") as form fields.

    Every question is a required paragraph; `/ticket type field` adds typed and optional ones.
    """
    labels = [q.strip() for q in text.split("|") if q.strip()]
    if len(labels) > MAX_FIELDS:
        raise TicketError(f"A form can have at most {MAX_FIELDS} questions.")
    fields: list[dict] = []
    for label in labels:
        if len(label) > MAX_LABEL:
            raise TicketError(f"Questions can be at most {MAX_LABEL} characters: “{label}”.")
        fields.append(
            {"key": _field_key(label, fields), "label": label, "type": "paragraph",
             "required": True, "max_length": 1000}
        )
    return fields


def set_field(
    fields: list[dict],
    label: str,
    kind: Optional[str] = None,
    *,
    required: Optional[bool] = None,
    options: Optional[str] = None,
    identity: Optional[bool] = None,
    staff_only: Optional[bool] = None,
) -> list[dict]:
    """`fields` with the field called `label` added, or changed in place (keeping its key, so
    answers already given still line up). On a change, anything left as None keeps its current
    value; for a new field it's a required short-text question."""
    label = label.strip()
    if not label or len(label) > MAX_LABEL:
        raise TicketError(f"A field's label is 1–{MAX_LABEL} characters.")
    existing = next((f for f in fields if f["label"].lower() == label.lower()), None)
    current = existing or {}
    kind = kind or (field_type(existing) if existing else "short")
    if kind not in FIELD_TYPES:
        raise TicketError(f"Unknown field type {kind}.")
    staff_only = bool(current.get("staff_only")) if staff_only is None else staff_only
    identity = bool(current.get("identity")) if identity is None else identity
    if required is None:
        required = current["required"] if existing and not current.get("staff_only") else True
    if options is None and existing and field_type(existing) == kind:  # same kind: keep its choices
        options = ",".join(current["options"]) if kind == "select" else current.get("pattern")
    field: dict = {
        "key": existing["key"] if existing else _field_key(label, fields),
        "label": label,
        "type": kind,
        "required": required and not staff_only,  # staff fill those in once it's open
        "max_length": MAX_LENGTH.get(kind, 100),
    }
    if kind == "select":
        choices = list(dict.fromkeys(o.strip()[:100] for o in (options or "").split(",") if o.strip()))
        if not 1 <= len(choices) <= MAX_OPTIONS:
            raise TicketError(f"A dropdown needs 1–{MAX_OPTIONS} options, separated by commas.")
        field["options"] = choices
    if kind == "pattern":
        if not options:
            raise TicketError("A pattern field needs its regular expression in `options`.")
        try:
            slow = len(options) > 200 or _slow_pattern(re._parser.parse(options))  # type: ignore[attr-defined]
        except re.error as exc:
            raise TicketError(f"That pattern isn't a valid regular expression: {exc}.") from None
        if slow:
            raise TicketError(
                "That pattern could take too long to check. Keep it simple: no repeats inside "
                "repeats (like `(a+)*`), no `|` inside a repeat, no backreferences, and at most "
                f"{MAX_OPEN_REPEATS} open-ended repeats (`*`, `+`, `{{2,}}`)."
            )
        field["pattern"] = options
    if identity:
        field["identity"] = True
    if staff_only:
        field["staff_only"] = True

    result = [field if f is existing else f for f in fields] if existing else [*fields, field]
    if len(result) > MAX_FIELDS:
        raise TicketError(f"A ticket type can have at most {MAX_FIELDS} fields.")
    if sum(1 for f in asked_fields({"fields": result}) if field_type(f) in PICKED) > MAX_PICKED:
        raise TicketError(f"A form can ask at most {MAX_PICKED} dropdown or member questions.")
    return result


def asked_fields(ticket_type: dict) -> list[dict]:
    """The fields the opener fills in (staff-only ones are never asked)."""
    return [f for f in ticket_type["fields"] if not f.get("staff_only")]


def placeholder(field: dict) -> hikari.UndefinedOr[str]:
    kind = field_type(field)
    if kind == "date":
        return "YYYY-MM-DD"
    if kind == "url":
        return "https://…"
    if kind == "user":
        return "Member ID or @mention"
    if kind == "select":
        return f"One of: {', '.join(field['options'])}"[:100]
    return hikari.UNDEFINED


def check_answer(field: dict, value: str) -> str:
    """An answer as stored, or a TicketError saying what's wrong with it."""
    kind, label = field_type(field), field["label"]
    limit = field.get("max_length", 1000)
    if len(value) > limit:
        raise TicketError(f"**{label}** can be at most {limit} characters.")
    if kind == "number":
        try:
            ok = math.isfinite(float(value.replace(",", "")))
        except ValueError:
            ok = False
        if not ok:
            raise TicketError(f"**{label}** must be a number.")
    elif kind == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            raise TicketError(f"**{label}** must be a date like 2026-01-31.") from None
    elif kind == "url":
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise TicketError(f"**{label}** must be a link starting with https://.")
    elif kind == "pattern":
        # set_field refused slow shapes, and the answer is at most 100 characters.
        if not re.fullmatch(field["pattern"], value):
            raise TicketError(f"**{label}** isn't in the expected format.")
    elif kind == "select":
        match = next((o for o in field["options"] if o.lower() == value.lower()), None)
        if match is None:
            raise TicketError(f"**{label}** must be one of: {', '.join(field['options'])}.")
        value = match
    elif kind == "user":
        value = value.strip("<@!>")
        if not value.isdigit():
            raise TicketError(f"**{label}** must be a member: their ID or @mention.")
    return value


def check_answers(fields: list[dict], answers: dict[str, str]) -> dict[str, str]:
    """Answers to `fields`, checked: one per field ("" when skipped), or a TicketError listing
    every problem at once."""
    checked: dict[str, str] = {}
    problems: list[str] = []
    for f in fields:
        value = (answers.get(f["key"]) or "").strip()
        checked[f["key"]] = ""
        if not value:
            if f["required"]:
                problems.append(f"Please answer **{f['label']}**.")
            continue
        try:
            checked[f["key"]] = check_answer(f, value)
        except TicketError as exc:
            problems.append(str(exc))
    if problems:
        raise TicketError("\n".join(problems))
    return checked


def identity_values(ticket_type: dict, fields: dict[str, str]) -> dict[str, str]:
    """Identity answers, normalised, by field key. Tickets sharing one are linked, across types
    too: fields with the same label have the same key."""
    return {
        f["key"]: fields[f["key"]].strip().lower()
        for f in ticket_type["fields"]
        if f.get("identity") and fields.get(f["key"], "").strip()
    }


def answer_text(field: Optional[dict], value: str) -> str:
    if field is not None and field_type(field) == "user" and value:
        return f"<@{value}>"
    return value


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
    if staff:
        lines += [f"{label} (staff): {answer}" for label, answer in document.get("staff_fields", {}).items()]
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
        # Form answers waiting on the opener's dropdown/member picks, by (guild, user, type), with
        # when they were saved. Oldest first, so expiry and the cap only ever drop from the front.
        # ponytail: in memory; a restart drops half-filled forms and the member starts again.
        self.drafts: dict[tuple[int, int, int], tuple[float, dict[str, str]]] = {}

    def save_draft(self, key: tuple[int, int, int], answers: dict[str, str]) -> None:
        """Keep a form's answers for DRAFT_TTL, replacing that member's earlier form of the type.
        Expired drafts are dropped here, and past MAX_DRAFTS the oldest go first."""
        now = time.monotonic()
        self.drafts.pop(key, None)
        for old, (saved, _) in list(self.drafts.items()):
            if now - saved < DRAFT_TTL and len(self.drafts) < MAX_DRAFTS:
                break
            del self.drafts[old]
        self.drafts[key] = (now, answers)

    def get_draft(self, key: tuple[int, int, int]) -> dict[str, str]:
        draft = self.drafts.get(key)
        if draft is None or time.monotonic() - draft[0] >= DRAFT_TTL:
            raise TicketError("This form expired. Press the panel button again.")
        return draft[1]

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
        fields = check_answers(asked_fields(ticket_type), fields)  # before anything is created
        async with self._open_locks[guild_id]:
            await self.check_can_open(guild_id, member.id, ticket_type)
            ticket = await self.repo.create_ticket(
                guild_id, ticket_type["id"], member.id, fields, identity_values(ticket_type, fields)
            )
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
        self.show_answers(embed, ticket, ticket_type)
        embed.set_footer(privacy)

        row = self.rest.build_message_action_row().add_interactive_button(
            hikari.ButtonStyle.DANGER, f"ticket:close:{ticket['id']}", label="Close", emoji="🔒"
        )
        if ticket_type["claim_mode"] in ("button", "both"):
            row.add_interactive_button(
                hikari.ButtonStyle.SUCCESS, f"ticket:claim:{ticket['id']}", label="Claim", emoji="🙋"
            )
        if ticket_type["fields"]:
            row.add_interactive_button(
                hikari.ButtonStyle.SECONDARY, f"ticket:fields:{ticket['id']}", label="Edit fields", emoji="✏️"
            )
        row.add_interactive_button(
            hikari.ButtonStyle.SECONDARY, f"ticket:context:{ticket['id']}", label="Staff info", emoji="🔎"
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

    def show_answers(self, embed: hikari.Embed, ticket: dict, ticket_type: dict) -> hikari.Embed:
        """The welcome embed's fields: the opener's answers, never staff-only ones (the opener
        sees this message). Answers to fields removed since aren't shown either: they may have
        been staff-only."""
        while embed.fields:
            embed.remove_field(0)
        for field in asked_fields(ticket_type):
            if field["key"] in ticket["fields"]:
                embed.add_field(field["label"], answer_text(field, ticket["fields"][field["key"]])[:1024] or "—")
        return embed

    async def edit_fields(
        self, ticket: dict, ticket_type: dict, answers: dict[str, str], actor_id: int
    ) -> dict:
        """Staff change a ticket's answers, staff-only fields included."""
        fields = {**ticket["fields"], **check_answers(ticket_type["fields"], answers)}
        updated = await self.repo.update_fields(
            ticket["id"], ticket["guild_id"], fields, identity_values(ticket_type, fields)
        )
        changed = sorted(k for k in fields if fields[k] != ticket["fields"].get(k, ""))
        await self.repo.add_event(ticket["id"], "fields_edited", actor_id, {"keys": changed})
        return updated

    # Staff context

    async def staff_context(
        self, ticket: dict, ticket_type: dict, mod_repo: Optional[ModerationRepository] = None
    ) -> hikari.Embed:
        """What staff should know about a ticket: who opened it, their other tickets, linked
        tickets, notes, and (with `mod_repo`, when Moderation is on) everyone involved's record.
        Sent ephemerally: the opener can see the channel."""
        guild_id, opener_id = ticket["guild_id"], ticket["opener_id"]
        embed = hikari.Embed(title=f"🔎 Ticket #{ticket['number']} · staff info", color=PANEL_COLOR)

        about = [f"<@{opener_id}> · account made <t:{int(hikari.Snowflake(opener_id).created_at.timestamp())}:R>"]
        opener = self.cache.get_member(guild_id, opener_id)
        if opener is None:
            try:
                opener = await self.rest.fetch_member(guild_id, opener_id)
            except hikari.NotFoundError:
                about.append("Not in the server any more.")
        if opener is not None:
            about.append(f"Joined <t:{int(opener.joined_at.timestamp())}:R>")
            roles = [f"<@&{r}>" for r in opener.role_ids if r != guild_id]
            about.append(f"Roles: {' '.join(roles[:15]) or 'none'}")
        embed.add_field("Opener", "\n".join(about)[:1024])

        def line(t: dict) -> str:
            when = t["closed_at"] or t["opened_at"]
            state = "open" if t["status"] == "open" else "closed"
            return f"#{t['number']} {t['type_name']} · {state} <t:{when}:R>"

        previous, total = await self.repo.opener_tickets(guild_id, opener_id, ticket["id"])
        history = "\n".join(line(t) for t in previous) or "None"
        if total > len(previous):
            history += f"\n…and {total - len(previous)} more"
        embed.add_field("Their other tickets", history[:1024])

        linked = await self.repo.linked_tickets(ticket["id"])
        if linked:
            embed.add_field(
                "Linked tickets (same answers)",
                "\n".join(f"{line(t)} · <@{t['opener_id']}> · {', '.join(t['linked_by'])}" for t in linked)[:1024],
            )

        notes = await self.repo.list_notes(ticket["id"])
        if notes:
            embed.add_field(
                "Notes", "\n".join(f"<@{n['author_id']}>: {n['body'][:200]}" for n in notes[-5:])[:1024]
            )

        if mod_repo is not None:
            involved = [opener_id] + [
                int(ticket["fields"][f["key"]])
                for f in ticket_type["fields"]
                if field_type(f) == "user" and ticket["fields"].get(f["key"], "").isdigit()
            ]
            for user_id in list(dict.fromkeys(involved))[:4]:
                warnings = await mod_repo.get_warning_count(guild_id, user_id)
                cases = await mod_repo.get_user_cases(guild_id, user_id, limit=3)
                record = [f"{warnings} active warning{'' if warnings == 1 else 's'}"] + [
                    f"Case #{c['case_number']} {c['action']} <t:{c['created_at']}:R>: {(c['reason'] or '')[:80]}"
                    for c in cases
                ]
                embed.add_field("Moderation record", f"<@{user_id}>\n" + "\n".join(record)[:1000])
        return embed

    def _top_role(self, member: hikari.Member) -> int:
        roles = self.cache.get_roles_view_for_guild(member.guild_id)
        return max((roles[r].position for r in member.role_ids if r in roles), default=0)

    async def mod_action(
        self,
        ticket: dict,
        ticket_type: dict,
        moderator: hikari.Member,
        target_id: int,
        action: str,
        reason: Optional[str],
        duration: Optional[int],
        mod: ModerationService,
        mod_repo: ModerationRepository,
    ) -> int:
        """Warn, time out, kick or ban from a ticket. The case links to the ticket, and holds its
        transcript as evidence (now if it has one from before a reopen, otherwise when it closes)."""
        self.require_staff(moderator, ticket_type)
        needed = MOD_ACTIONS[action]
        if not member_permissions(moderator) & (needed | hikari.Permissions.ADMINISTRATOR):
            raise TicketError(f"You need **{needed.name.replace('_', ' ').title()}** to {action} members.")  # type: ignore[union-attr]

        guild_id = ticket["guild_id"]
        target: Optional[hikari.Member] = self.cache.get_member(guild_id, target_id)
        if target is None:
            try:
                target = await self.rest.fetch_member(guild_id, target_id)
            except hikari.NotFoundError:
                if action != "ban":
                    raise TicketError(f"<@{target_id}> isn't in the server.") from None
        if target is not None:
            guild = self.cache.get_guild(guild_id)
            me = self.cache.get_me()
            if target.id == moderator.id or (me and target.id == me.id) or (guild and target.id == guild.owner_id):
                raise TicketError("You can't moderate yourself, me or the server owner.")
            if self._top_role(moderator) <= self._top_role(target):
                raise TicketError("You can't moderate someone with an equal or higher role than yours.")

        kwargs = {"guild_id": guild_id, "moderator": moderator, "reason": reason}
        if action == "warn":
            case, _ = await mod.warn(target=target, **kwargs)  # type: ignore[arg-type]
        elif action == "timeout":
            case = await mod.timeout(target=target, duration_seconds=duration or 3600, **kwargs)  # type: ignore[arg-type]
        elif action == "kick":
            case = await mod.kick(target=target, **kwargs)  # type: ignore[arg-type]
        else:
            case = await mod.ban(target=target or await self.rest.fetch_user(target_id), **kwargs)  # type: ignore[arg-type]

        await mod_repo.set_case_source(guild_id, case, ticket["id"])
        await self.repo.hold_transcript(ticket["id"], case)
        await self.repo.add_event(
            ticket["id"], "mod_action", moderator.id, {"case": case, "action": action, "user": target_id}
        )
        return case

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
        # Only answers to fields the opener is asked go in their copy; staff-only ones, and those
        # of fields removed since (which may have been staff-only), are kept for staff.
        opener_keys = {f["key"] for f in asked_fields(ticket_type)}
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
            "fields": {labels[k]: v for k, v in ticket["fields"].items() if k in opener_keys},
            "staff_fields": {labels.get(k, k): v for k, v in ticket["fields"].items() if k not in opener_keys and v},
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
            updated = await self.repo.update_ticket(ticket["id"], type_id=new_type["id"])
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
        return updated  # type: ignore[return-value]
