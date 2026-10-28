from __future__ import annotations

import asyncio
import math
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

import hikari
import toolbox

from serenity.core.modules import ModuleManager, ModuleType, module_available
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.logging_service import LoggingService
from serenity.services.moderation_service import ModerationService
from serenity.services.ticket_hours import (
    business_seconds_between,
    effective_schedule,
    format_schedule,
    offline_status,
    within_hours,
)
from serenity.services.ticket_webhooks import TicketWebhooks
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
DEFAULT_RATING_WINDOW_DAYS = 7
LOW_RATING = 2  # ratings at or below this alert the ticket log
RAID_PAUSED = "Opening tickets is paused because a lot were opened at once. Please try again later."
TRANSCRIPT_MESSAGE_LIMIT = 10_000  # ponytail: a cap, not paging; no real ticket gets close
AUTO_CLOSE_GRACE = 12 * 3600  # from the inactivity warning to the close, in support time
AUTO_CLOSES_PER_SWEEP = 25
DEFAULT_OFF_HOURS = (
    "Hi {user}, our support team is away right now, so a reply may take a while. "
    "We're back {next_open}. Our hours: {hours}."
)
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


MAX_OPEN_REPEATS = 3  # wide repeats (`*`, `+`, `{0,100}`): on 100 characters, ~100^3 steps
MAX_VARIABLE_REPEATS = 6  # `?` and `{0,n}` too: each one can double the paths


def _slow_pattern(parsed: Any) -> bool:
    """Whether a parsed regex can backtrack badly. Patterns run on the event loop, and Python's
    re has no timeout, so the shapes behind catastrophic backtracking are refused up front:
    a repeat or `|` inside any repeat (bounded ones too: `(a+){6}` explodes as well),
    backreferences, and many open-ended repeats.

    ponytail: reads re's private parse tree and is deliberately strict; a `regex`-module
    timeout would allow more if admins need richer patterns.
    """
    from re import _constants as c  # type: ignore[attr-defined]

    open_repeats = variable_repeats = 0

    def walk(items: Any, in_repeat: bool) -> bool:
        nonlocal open_repeats, variable_repeats
        for op, av in items:
            if op in (c.MAX_REPEAT, c.MIN_REPEAT, c.POSSESSIVE_REPEAT):
                low, high, sub = av
                if in_repeat and low != high:  # `{3}` can only match one way; `?` can't
                    return True
                open_repeats += high - low > 1  # `{0,100}` splits an answer as many ways as `*`
                variable_repeats += low != high
                if walk(sub, in_repeat or high > 1):
                    return True
            elif op == c.BRANCH:
                if in_repeat or any(walk(branch, in_repeat) for branch in av[1]):
                    return True
            elif op in (c.GROUPREF, c.GROUPREF_EXISTS):
                return True
            elif op in (c.SUBPATTERN, c.ASSERT, c.ASSERT_NOT):
                if walk(av[-1], in_repeat):
                    return True
            elif op == c.ATOMIC_GROUP:  # its argument is the subpattern itself
                if walk(av, in_repeat):
                    return True
        return False

    return (
        walk(parsed, False)
        or open_repeats > MAX_OPEN_REPEATS
        or variable_repeats > MAX_VARIABLE_REPEATS
    )


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
                f"{MAX_OPEN_REPEATS} wide repeats (`*`, `+`, `{{0,100}}`) and {MAX_VARIABLE_REPEATS} "
                "repeats in all (counting `?`)."
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
    """Staff of a type: one of its staff or tier roles (tiers come from full_type), or Manage Server."""
    if permissions & (hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR):
        return True
    staff = {*ticket_type["staff_role_ids"], *(r for t in ticket_type.get("tiers", ()) for r in t["role_ids"])}
    return any(role in staff for role in role_ids)


def current_tier(ticket: dict, ticket_type: dict) -> Optional[dict]:
    """The tier a ticket is with: the one it was escalated to, else its type's lowest."""
    tiers = ticket_type["tiers"]
    return next((t for t in tiers if t["id"] == ticket["tier_id"]), tiers[0] if tiers else None)


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
        self.webhooks = TicketWebhooks(repo)
        # Channels of open tickets, so the message listener can skip every other message
        # without a query. ponytail: one bot process; shard across processes and this moves to the DB.
        self.open_channels: set[int] = set()
        # Posted panel messages, so a deleted message only costs a query if it was a panel.
        self.panel_messages: set[int] = set()
        # The creation queue: one ticket channel at a time per guild, so limits are checked
        # against settled counts and overflow categories aren't created twice.
        self._open_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        # The support status each guild's panels last showed, so they're edited only when it changes.
        # A guild missing here hasn't been rendered since startup, so the first sweep reconciles it.
        self._panel_status: dict[int, Optional[str]] = {}
        # Pending topic edits and when each channel's topic was last edited (monotonic).
        # ponytail: in memory, so a restart drops a pending edit until the ticket next changes.
        self._topic_tasks: dict[int, asyncio.Task] = {}
        self._topic_edited: dict[int, float] = {}
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
        """Post a ticket event to the ticket log, if the server has one, and to its webhooks."""
        self.webhooks.dispatch(ticket, ticket_type, kind, actor_id, detail)
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
        self, panel: dict, types: list[dict], status: Optional[str] = None
    ) -> tuple[hikari.Embed, list[hikari.api.MessageActionRowBuilder]]:
        """The panel message: one button per type (5 per row), or a dropdown of them."""
        embed = hikari.Embed(
            title=panel["title"], description=panel["description"], color=PANEL_COLOR
        )
        if status:
            embed.add_field("Support", status)
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
        status = await self.panel_status(panel["guild_id"])
        embed, rows = self.build_panel(panel, types, status)
        try:
            if panel["message_id"]:
                try:
                    await self.rest.edit_message(
                        panel["channel_id"], panel["message_id"], embed=embed, components=rows
                    )
                    self._panel_status[panel["guild_id"]] = status  # only once it's shown
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
        self._panel_status[panel["guild_id"]] = status
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

    # Support hours

    async def panel_status(self, guild_id: int) -> Optional[str]:
        """The panels' status line, if the server has support hours or away mode set.

        ponytail: the server's schedule only; a type with its own hours isn't shown separately.
        """
        settings = await self.settings(guild_id)
        status = offline_status(settings, None, datetime.now(timezone.utc))
        if status is None:  # no line without a schedule, e.g. once away mode has ended
            return "🟢 Online" if settings.get("schedule") else None
        return "🔴 Offline" + (f" · back <t:{int(status['back'].timestamp())}:R>" if status["back"] else "")

    async def refresh_panel_statuses(self) -> None:
        """Re-render panels whose support status changed: opening, closing, away mode ending."""
        for guild_id in await self.repo.panel_guilds():
            try:
                status = await self.panel_status(guild_id)
                if guild_id not in self._panel_status or status != self._panel_status[guild_id]:
                    await self.refresh_panels(guild_id)  # records the new status
            except Exception:  # one broken guild can't stop the rest
                logger.exception(f"Couldn't refresh the ticket panel status of guild {guild_id}")

    async def off_hours_notice(self, guild_id: int, ticket_type: dict, user_id: int) -> Optional[str]:
        """The auto-reply for a ticket opened now, or None while support is open."""
        settings = await self.settings(guild_id)
        status = offline_status(settings, ticket_type["schedule"], datetime.now(timezone.utc))
        if status is None:
            return None
        schedule = effective_schedule(ticket_type["schedule"], settings)
        hours = f"{format_schedule(schedule)} ({settings.get('timezone', 'UTC')})" if schedule else "not set"
        back = int(status["back"].timestamp()) if status["back"] else None
        text = (
            (settings.get("off_hours_message") or DEFAULT_OFF_HOURS)
            .replace("{user}", f"<@{user_id}>")
            .replace("{hours}", hours)
            .replace("{next_open}", f"<t:{back}:F> (<t:{back}:R>)" if back else "soon")
        )
        return f"{text}\n> {status['note']}" if status["note"] else text

    # Opening

    async def retention_days(self, guild_id: int, ticket_type: dict) -> int:
        """Days a transcript of this type is kept; 0 means until deleted."""
        if ticket_type["retention_days"] is not None:
            return ticket_type["retention_days"]
        config = await self.modules.get_module_config(guild_id, ModuleType.TICKETS)
        return int(config.settings.get("retention_days", DEFAULT_RETENTION_DAYS))

    async def settings(self, guild_id: int) -> dict:
        return (await self.modules.get_module_config(guild_id, ModuleType.TICKETS)).settings

    async def update_settings(self, guild_id: int, **changes) -> None:
        await self.modules.repo.merge_module_settings(guild_id, ModuleType.TICKETS, changes)

    async def check_can_open(self, guild_id: int, member: hikari.Member, ticket_type: dict) -> None:
        """Raise if this member may not open a ticket of this type right now."""
        if await self.repo.get_blacklist_entry(guild_id, member.id):
            raise TicketError("You can't open tickets in this server.")
        settings = await self.settings(guild_id)
        if settings.get("raid_paused"):
            raise TicketError(RAID_PAUSED)
        role = settings.get("required_role_id")
        if role and role not in member.role_ids:
            raise TicketError(f"You need the <@&{role}> role to open tickets.")
        age = settings.get("min_account_age_days", 0)
        if age and member.created_at > datetime.now(timezone.utc) - timedelta(days=age):
            raise TicketError(f"Your account needs to be at least {age} days old to open tickets.")
        limit = ticket_type["open_limit"]
        if await self.repo.count_open(guild_id, member.id, ticket_type["id"]) >= limit:
            raise TicketError(
                f"You already have {'an open' if limit == 1 else f'{limit} open'} "
                f"**{ticket_type['name']}** ticket{'' if limit == 1 else 's'}."
            )
        if not ticket_type["open_off_hours"]:
            # The schedule decides; away mode only adds its auto-reply.
            schedule = effective_schedule(ticket_type["schedule"], settings)
            if not within_hours(schedule, settings.get("timezone", "UTC"), datetime.now(timezone.utc)):
                notice = await self.off_hours_notice(guild_id, ticket_type, member.id)
                raise TicketError(
                    f"**{ticket_type['name']}** tickets can only be opened during support hours.\n{notice}"
                )
        now = int(time.time())
        if cooldown := settings.get("open_cooldown_minutes", 0):
            last = await self.repo.last_opened_at(guild_id, member.id)
            if last and last + cooldown * 60 > now:
                raise TicketError(f"You can open another ticket <t:{last + cooldown * 60}:R>.")
        if raid_limit := settings.get("raid_limit", 0):
            minutes = settings.get("raid_window_minutes", 5)
            # A resume starts the count afresh (opened_at is whole seconds, so from the next one).
            since = max(now - minutes * 60, settings.get("raid_resumed_at", -1) + 1)
            opened = await self.repo.count_opened_since(guild_id, since)
            if opened >= raid_limit:
                # Latched only once staff have the alert and its Resume button; with no ticket log
                # the brake still refuses opens, and lifts by itself as the burst leaves the window.
                # ponytail: the pre-form check runs outside the open lock, so two members tripping
                # it at the same instant can post two alerts; harmless.
                if await self.raid_alert(guild_id, opened, minutes, ticket_type):
                    await self.update_settings(guild_id, raid_paused=True)
                raise TicketError(RAID_PAUSED)

    async def raid_alert(self, guild_id: int, opened: int, minutes: int, ticket_type: dict) -> bool:
        """Tell staff in the ticket log that the raid brake tripped, with a button to resume."""
        if not self.logging:
            return False
        row = self.rest.build_message_action_row().add_interactive_button(
            hikari.ButtonStyle.SUCCESS, "ticket:resume:0", label="Resume opening tickets"
        )
        return await self.logging.log_ticket_raid_brake(
            guild_id=guild_id, opened=opened, minutes=minutes,
            role_ids=ticket_type["staff_role_ids"], component=row,
        )

    async def resume_opening(self, guild_id: int) -> None:
        """Lift the raid brake; only tickets opened from now on count towards the next one."""
        await self.update_settings(guild_id, raid_paused=False, raid_resumed_at=int(time.time()))

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
            await self.check_can_open(guild_id, member, ticket_type)
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
        try:
            notice = await self.off_hours_notice(guild_id, ticket_type, member.id)
            if notice:
                await self.rest.create_message(ticket["channel_id"], notice, user_mentions=[member.id])
                ticket["off_hours_notice"] = notice  # for the opener's confirmation too
        except Exception:  # the ticket is open either way
            logger.exception(f"Couldn't post the off-hours reply for ticket {ticket['id']}")
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
        return ticket, await self.full_type(ticket)

    async def full_type(self, ticket: dict) -> dict:
        """A ticket's type with its tier ladder, whose roles count as the type's staff."""
        ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
        return {**ticket_type, "tiers": await self.repo.list_tiers(ticket["type_id"])}  # type: ignore[dict-item]

    def is_staff(self, member: hikari.Member, ticket_type: dict) -> bool:
        return is_staff(ticket_type, member.role_ids, member_permissions(member))

    def require_staff(self, member: Optional[hikari.Member], ticket_type: dict) -> hikari.Member:
        if member is None or not self.is_staff(member, ticket_type):
            raise TicketError(f"Only **{ticket_type['name']}** staff can do that.")
        return member

    async def claim(
        self,
        ticket: dict,
        user_id: int,
        *,
        by: Optional[int] = None,
        announce: bool = True,
        force: bool = False,
    ) -> dict:
        """Give the ticket to `user_id`. `by` is whoever reassigns it, if it isn't them; `force`
        takes it from the current assignee (an escalated tier picking it up)."""
        updated = await self.repo.claim(ticket["id"], user_id, force=force or by is not None)
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
        # Tier roles only get into the channel by escalating, so a never-escalated ticket skips the ladder.
        if ticket["tier_id"] is not None:
            ticket_type = await self.full_type(ticket)
        else:
            ticket_type = {**await self.repo.get_type(ticket["guild_id"], ticket["type_id"]), "tiers": []}  # type: ignore[dict-item]
        staff = event.author_id != ticket["opener_id"] and self.is_staff(event.member, ticket_type)
        updated = await self.repo.record_message(ticket["id"], event.author_id, staff=staff)
        if updated is None:  # the ticket was rolled back (failed open) a moment ago
            return
        if updated["waiting_on"] != ticket["waiting_on"]:
            self.schedule_topic(event.channel_id)
        tier = current_tier(ticket, ticket_type)
        if (
            staff
            and ticket["tier_pinged_at"]
            and tier
            and ticket_type["claim_mode"] in ("reply", "both")
            and set(event.member.role_ids) & set(tier["role_ids"])
        ):
            # The first of an escalated tier to reply takes the ticket (claiming clears the re-ping);
            # earlier handlers keep their place and credit in the chain. In button mode the tier
            # takes it with /ticket assign. ponytail: two replies at once both claim, the later wins.
            await self.claim(ticket, event.author_id, announce=False, force=True)
        elif staff and ticket["assignee_id"] is None and ticket_type["claim_mode"] in ("reply", "both"):
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

    # Escalation

    async def _ping_tier(self, ticket: dict, tier: dict, headline: str, reason: Optional[str] = None) -> None:
        roles = " ".join(f"<@&{r}>" for r in tier["role_ids"])
        lines = [
            f"{headline} {roles}".strip(),
            f"Ticket #{ticket['number']} · opened by <@{ticket['opener_id']}> <t:{ticket['opened_at']}:R>",
        ]
        if reason:
            lines.append(f"> {reason}")
        await self.rest.create_message(
            ticket["channel_id"], "\n".join(lines), role_mentions=tier["role_ids"], user_mentions=False
        )

    async def escalate(
        self, ticket: dict, ticket_type: dict, actor_id: int, target_name: Optional[str], reason: Optional[str]
    ) -> dict:
        """Bring a higher tier into the ticket: the next rung up, or a tier the current one may summon."""
        tiers = ticket_type["tiers"]
        current = current_tier(ticket, ticket_type)
        if current is None:
            raise TicketError(
                f"**{ticket_type['name']}** tickets have no tiers. Add them with `/ticket type tier`."
            )
        above = next((t for t in tiers if t["position"] > current["position"]), None)
        if target_name is None:
            target = above
            if target is None:
                raise TicketError(f"**{current['name']}** is already the top tier.")
        else:
            target = next((t for t in tiers if t["name"].lower() == target_name.lower()), None)
            if target is None:
                raise TicketError(f"**{ticket_type['name']}** has no tier called **{target_name}**.")
            if target is current:
                raise TicketError(f"This ticket is already with **{current['name']}**.")
            if target is not above and target["id"] not in current["can_summon"]:
                raise TicketError(f"**{current['name']}** can't summon **{target['name']}**.")
        if target["requires_reason"] and not reason:
            raise TicketError(f"Escalating to **{target['name']}** needs a reason.")

        for role in target["role_ids"]:
            await self.rest.edit_permission_overwrite(
                ticket["channel_id"], role, target_type=hikari.PermissionOverwriteType.ROLE,
                allow=TICKET_MEMBER_ALLOW, reason=f"Ticket #{ticket['number']} escalated to {target['name']}",
            )
        updated = await self.repo.update_ticket(
            ticket["id"], tier_id=target["id"], tier_pinged_at=int(time.time())
        )
        await self.repo.add_event(
            ticket["id"], "escalated", actor_id, {"from": current["id"], "to": target["id"], "reason": reason}
        )
        await self.log(
            updated, ticket_type, "escalated", actor_id,  # type: ignore[arg-type]
            f"{current['name']} → {target['name']}" + (f": {reason}" if reason else ""),
        )
        await self._ping_tier(
            updated, target, f"⬆️ <@{actor_id}> escalated this ticket to **{target['name']}**.", reason  # type: ignore[arg-type]
        )
        return updated  # type: ignore[return-value]

    async def deescalate(self, ticket: dict, ticket_type: dict, actor_id: int, note: Optional[str]) -> dict:
        """Hand the ticket back to the tier that escalated it, unassigned so that tier picks it up.

        ponytail: the higher tier keeps channel access (and its credit); nothing removes its role.
        """
        events = await self.repo.list_events(ticket["id"])
        last = next(
            (e for e in reversed(events) if e["kind"] == "escalated" and e["data"]["to"] == ticket["tier_id"]),
            None,
        )
        if last is None:
            raise TicketError("This ticket hasn't been escalated.")
        if not note:
            raise TicketError("Add a reason: it's posted for the tier taking the ticket back.")
        tiers = {t["id"]: t for t in ticket_type["tiers"]}
        back = tiers.get(last["data"]["from"]) or ticket_type["tiers"][0]
        updated = await self.repo.update_ticket(
            ticket["id"], tier_id=back["id"], tier_pinged_at=None, assignee_id=None
        )
        await self.repo.add_event(
            ticket["id"], "deescalated", actor_id, {"from": ticket["tier_id"], "to": back["id"], "reason": note}
        )
        await self.log(updated, ticket_type, "deescalated", actor_id, f"Back to {back['name']}: {note}")  # type: ignore[arg-type]
        await self._ping_tier(
            updated, back, f"⬇️ <@{actor_id}> handed this ticket back to **{back['name']}**.", note  # type: ignore[arg-type]
        )
        return updated  # type: ignore[return-value]

    def can_reassign(self, member: hikari.Member, ticket: dict, ticket_type: dict) -> bool:
        """Admins, and the tier a ticket was escalated to (higher tiers may reassign)."""
        if member_permissions(member) & (hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR):
            return True
        tier = current_tier(ticket, ticket_type)
        return (
            tier is not None
            and tier is not ticket_type["tiers"][0]
            and any(r in tier["role_ids"] for r in member.role_ids)
        )

    async def reping_tiers(self, now: int) -> None:
        """Ping escalated tiers again when nobody from them has replied in their re-ping time."""
        for ticket in await self.repo.due_for_reping(now):
            try:
                await self.repo.update_ticket(ticket["id"], tier_pinged_at=now)
                await self._ping_tier(
                    ticket,
                    {"role_ids": ticket["tier_role_ids"]},
                    f"⏰ Nobody from **{ticket['tier_name']}** has picked this ticket up yet.",
                )
            except Exception:  # one deleted or locked channel can't hold up the rest
                logger.exception(f"Tier re-ping failed for ticket {ticket['id']}")

    async def auto_close(self, now: int) -> None:
        """Warn openers who went quiet past their type's timeout; close AUTO_CLOSE_GRACE later.

        Only tickets waiting on the opener, never at a stage with auto-close off, and only
        support hours count. ponytail: away mode doesn't pause the clock, only the schedule does.
        """
        at = datetime.fromtimestamp(now, timezone.utc)
        settings: dict[int, dict] = {}
        closes = 0
        for ticket in await self.repo.due_for_auto_close(now):
            try:
                ticket_type = await self.repo.get_type(ticket["guild_id"], ticket["type_id"])
                stage = find_stage(ticket_type, ticket["stage"])  # type: ignore[arg-type]
                if stage is not None and not stage.get("auto_close", True):
                    continue
                if ticket["guild_id"] not in settings:
                    settings[ticket["guild_id"]] = await self.settings(ticket["guild_id"])
                guild = settings[ticket["guild_id"]]
                schedule = effective_schedule(ticket_type["schedule"], guild)  # type: ignore[index]

                def quiet_since(ts: int) -> int:
                    return business_seconds_between(
                        schedule, guild.get("timezone", "UTC"), datetime.fromtimestamp(ts, timezone.utc), at
                    )

                warned = ticket["close_warned_at"]
                if warned is None or warned < ticket["last_activity_at"]:  # no warning since the last reply
                    if quiet_since(ticket["last_activity_at"]) >= ticket["auto_close_hours"] * 3600:
                        await self.rest.create_message(
                            ticket["channel_id"],
                            f"⏰ <@{ticket['opener_id']}>, this ticket has been quiet for a while. It closes "
                            f"automatically after {AUTO_CLOSE_GRACE // 3600} more "
                            f"{'support ' if schedule else ''}hours unless someone replies.",
                            user_mentions=[ticket["opener_id"]],
                        )
                        await self.repo.update_ticket(ticket["id"], close_warned_at=now)
                elif quiet_since(warned) >= AUTO_CLOSE_GRACE and closes < AUTO_CLOSES_PER_SWEEP:
                    # Tickets warned together come due together; a close builds a transcript, so
                    # the rest wait for the next sweep. Capped here, not in the query, so tickets
                    # that are skipped (stage, off-hours, grace) can't crowd out due ones.
                    closes += 1
                    await self._auto_close(ticket, ticket_type)  # type: ignore[arg-type]
            except Exception:  # one deleted or locked channel can't hold up the rest
                logger.exception(f"Auto-close failed for ticket {ticket['id']}")

    async def _auto_close(self, ticket: dict, ticket_type: dict) -> None:
        # Re-read just before closing: a reply since the sweep's query keeps it open.
        # ponytail: a reply in the seconds close() takes still loses; make close_ticket conditional if it matters.
        fresh = await self.repo.get_ticket_by_id(ticket["id"])
        if (
            fresh is None
            or fresh["status"] != "open"
            or fresh["waiting_on"] != "opener"
            or fresh["last_activity_at"] != ticket["last_activity_at"]
        ):
            return
        me = self.cache.get_me()
        await self.close(
            fresh, ticket_type, me.id if me else 0,
            f"Auto-closed: no reply for {ticket['auto_close_hours']}h and the reminder after it",
        )
        await self.repo.add_event(ticket["id"], "auto_closed", None)  # what stats count

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
        rows: list[hikari.api.MessageActionRowBuilder] = []
        # Not asked again after a reopen if they already rated it.
        try:
            ask = ticket_type["ratings"] and await self.repo.get_rating(ticket["id"]) is None
        except Exception:  # the close already happened; send the DM without the buttons
            logger.exception(f"Couldn't check the rating of closed ticket {ticket['id']}")
            ask = False
        if ask:
            embed.add_field("How did we do?", "Rate your experience from 1 (poor) to 5 (great).")
            rows.append(self.rest.build_message_action_row())
            for n in range(1, 6):
                rows[-1].add_interactive_button(
                    hikari.ButtonStyle.SECONDARY, f"ticket:rate:{ticket['id']}:{n}", label=str(n), emoji="⭐"
                )
        if document is not None:
            embed.set_footer("The transcript is attached. Staff can reopen this ticket for 7 days.")
            kwargs["attachment"] = hikari.Bytes(
                render_transcript(document, staff=False).encode(), f"ticket-{ticket['number']}.txt"
            )
            rows.append(self.rest.build_message_action_row().add_interactive_button(
                hikari.ButtonStyle.SECONDARY, f"ticket:delreq:{ticket['id']}",
                label="Ask to delete this transcript",
            ))
            if dashboard := os.getenv("DASHBOARD_URL"):  # the viewer asks them to log in first
                rows[-1].add_link_button(
                    f"{dashboard.rstrip('/')}/transcripts/{ticket['id']}", label="View transcript"
                )
        try:
            dm = await self.rest.create_dm_channel(ticket["opener_id"])
            # ponytail: DMs closed means no rating; the dashboard's transcript page can offer it later.
            await self.rest.create_message(dm, embed=embed, components=rows, **kwargs)
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

    # Ratings

    async def check_can_rate(self, ticket_id: int, user_id: int) -> tuple[dict, dict]:
        """The closed ticket `user_id` may rate now, and its type; raises if they can't."""
        ticket = await self.repo.get_ticket_by_id(ticket_id)
        if ticket is None or ticket["opener_id"] != user_id:
            raise TicketError("Only the person who opened this ticket can rate it.")
        guild_id = ticket["guild_id"]
        # The buttons are in a DM, so the guild checks other ticket buttons get happen here.
        if not module_available(guild_id, ModuleType.TICKETS) or not await self.modules.is_enabled(
            guild_id, ModuleType.TICKETS
        ):
            raise TicketError("Tickets are turned off in that server.")
        ticket_type: dict = await self.repo.get_type(guild_id, ticket["type_id"])  # type: ignore[assignment]
        if ticket["status"] != "closed":
            raise TicketError("This ticket was reopened. You can rate it once it closes again.")
        if not ticket_type["ratings"]:
            raise TicketError(f"**{ticket_type['name']}** tickets aren't rated.")
        if await self.repo.get_rating(ticket_id):
            raise TicketError("You've already rated this ticket. Thanks!")
        config = await self.modules.get_module_config(guild_id, ModuleType.TICKETS)
        days = int(config.settings.get("rating_window_days", DEFAULT_RATING_WINDOW_DAYS))
        if ticket["closed_at"] < time.time() - days * 86400:
            raise TicketError(f"Tickets can only be rated for {days} days after they close.")
        return ticket, ticket_type

    async def rate(self, ticket_id: int, user_id: int, rating: int, comment: Optional[str]) -> dict:
        """Store the opener's rating, credited to the ticket's handlers; low ones alert staff."""
        if not 1 <= rating <= 5:
            raise TicketError("Ratings are from 1 to 5.")
        ticket, ticket_type = await self.check_can_rate(ticket_id, user_id)
        stored = await self.repo.add_rating(ticket_id, user_id, rating, comment)
        if stored is None:  # a second submit that raced the first
            raise TicketError("You've already rated this ticket. Thanks!")
        await self.repo.add_event(ticket_id, "rated", user_id, {"rating": rating})
        if rating <= LOW_RATING:
            handlers = " ".join(f"<@{h}>" for h in stored["handler_ids"]) or "nobody"
            detail = f"{'⭐' * rating} {rating}/5 · Handled by {handlers}"
            await self.log(
                ticket, ticket_type, "low_rating", user_id,
                f"{detail}\n> {comment}" if comment else detail,
            )
        return stored

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
            # Tiers and stages belong to a type: the ticket starts the new type's ladder at the
            # bottom and its workflow at the first stage.
            first = new_type["stages"][0]["name"] if new_type["stages"] else None
            updated = await self.repo.update_ticket(
                ticket["id"], type_id=new_type["id"], tier_id=None, tier_pinged_at=None, stage=first
            )
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
