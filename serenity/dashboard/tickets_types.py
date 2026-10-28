"""Dashboard routes for the ticket type editor (#74), managers only.

Each write has the same rules as `/ticket type create|edit|field|stage-add|delete`, reusing their
validators (set_field, with_stage, parse_color, parse_schedule), so the dashboard can't save a
type the bot couldn't. Forms and workflows are sent whole and rebuilt field by field.
"""

import re
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import asyncpg
import hikari
from aiohttp import web

from serenity.dashboard.common import (
    BOT,
    CHANGES,
    SESSION,
    TICKET_SVC,
    ApiError,
    bool_field,
    guild_id,
    int_field,
    read_json,
)
from serenity.dashboard.tickets import TICKETS, routes
from serenity.services.ticket_hours import format_schedule, parse_schedule
from serenity.services.ticket_service import (
    TicketError,
    find_stage,
    parse_color,
    render_channel_name,
    set_field,
    with_stage,
)

CLAIM_MODES = ("reply", "button", "both")
PANEL_KEYS = {"name", "emoji", "description"}  # what a posted panel shows of a type
EDITABLE = {
    "name", "emoji", "description", "category_id", "staff_role_ids", "fields", "stages",
    "claim_mode", "open_limit", "name_template", "transcripts", "retention_days",
    "auto_unclaim_hours", "ratings", "auto_close_hours", "hours", "open_off_hours",
}  # fmt: skip


def _id(value: Optional[int]) -> Optional[str]:
    return str(value) if value else None


def hours_text(schedule: Optional[list]) -> Optional[str]:
    """A schedule as text parse_schedule reads back: "mon-fri 09:00-17:00; sat 10:00-14:00"."""
    if schedule is None:
        return None
    text = re.sub(r", (?=[A-Z])", "; ", format_schedule(schedule))  # between days
    return re.sub(r", (?=\d)", " ", text).replace("–", "-").lower()  # between one day's ranges


def type_json(ticket_type: dict) -> dict[str, Any]:
    out = {k: ticket_type[k] for k in EDITABLE - {"category_id", "staff_role_ids", "hours"}}
    return {
        **out,
        "id": ticket_type["id"],
        "category_id": _id(ticket_type["category_id"]),
        "staff_role_ids": [str(r) for r in ticket_type["staff_role_ids"]],
        "hours": hours_text(ticket_type["schedule"]),
    }


def _text(data: dict, key: str, hi: int, what: str, lo: int = 1) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not lo <= len(value.strip()) <= hi:
        raise ApiError(422, "invalid", f"{what} is {lo}–{hi} characters.")
    return value.strip()


def _optional_text(data: dict, key: str, hi: int, what: str) -> Optional[str]:
    return _text(data, key, hi, what, 0) or None if data.get(key) is not None else None


def _hours_field(data: dict, key: str, hi: int) -> Optional[int]:
    """0 or null is "never"."""
    return int_field(data, key, 0, hi) or None if data.get(key) is not None else None


def _flag(item: dict, key: str) -> Optional[bool]:
    value = item.get(key)
    if value is not None and not isinstance(value, bool):
        raise ApiError(422, "invalid", f"{key} must be true or false.")
    return value


def fields_from(items: Any, current: list[dict]) -> list[dict]:
    """A form from the editor's list, through set_field's checks. A field sent with the `key`
    of a current one keeps it, so answers already given still line up after a rename."""
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ApiError(422, "invalid", "fields must be a list of fields.")
    fields: list[dict] = []
    for item in items:
        label, kind = item.get("label"), item.get("type") or "short"
        if not isinstance(label, str) or not isinstance(kind, str):
            raise ApiError(422, "invalid", "Every field needs a label and a type.")
        if any(f["label"].lower() == label.strip().lower() for f in fields):
            raise ApiError(422, "invalid", f"Two fields are called “{label.strip()}”.")
        options = item.get("options") if kind == "select" else item.get("pattern")
        if kind == "select" and isinstance(options, list):
            options = ",".join(str(o) for o in options)
        if kind == "pattern" and not options:
            raise ApiError(422, "invalid", f"“{label.strip()}” needs its regular expression.")
        if options is not None and not isinstance(options, str):
            raise ApiError(422, "invalid", f"“{label.strip()}” has options that aren't text.")
        fields = set_field(
            fields, label, kind, required=_flag(item, "required"), options=options,
            identity=_flag(item, "identity"), staff_only=_flag(item, "staff_only"),
        )  # fmt: skip
    # set_field appends new labels, so fields[i] is items[i].
    current_keys = {f["key"] for f in current}
    kept: dict[int, str] = {}
    for i, item in enumerate(items):
        key = item.get("key")
        if isinstance(key, str) and key in current_keys and key not in kept.values():
            kept[i] = key
    taken = set(kept.values())
    for i, field in enumerate(fields):
        if i in kept:
            field["key"] = kept[i]
            continue
        while field["key"] in taken:
            field["key"] += "_"
        taken.add(field["key"])
    return fields


def stages_from(items: Any) -> list[dict]:
    """A workflow from the editor's list, in order, through `/ticket type stage-add`'s checks."""
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ApiError(422, "invalid", "stages must be a list of stages.")
    stages: list[dict] = []
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise TicketError("A stage needs a name.")
        name = name.strip()
        if len(name) > 50:
            raise TicketError(f"Stage names are at most 50 characters: “{name}”.")
        if find_stage({"stages": stages}, name):
            raise TicketError(f"Two stages are called “{name}”.")
        color = item.get("color")
        if isinstance(color, str):
            color = parse_color(color)
        elif color is not None and (type(color) is not int or not 0 <= color <= 0xFFFFFF):
            raise TicketError(f"“{color}” isn't a hex colour like #5865F2.")
        stage = {
            "name": name,
            "color": color,
            "terminal": bool(_flag(item, "terminal")),
            "auto_close": _flag(item, "auto_close") is not False,  # new stages may auto-close
        }
        stages = with_stage(stages, stage)
    return stages


def _category(request: web.Request, value: Any) -> Optional[int]:
    if value is None:
        return None
    channel = None
    if isinstance(value, str) and value.isdigit():
        channel = request.app[BOT].cache.get_guild_channel(int(value))
    if (
        channel is None
        or channel.guild_id != guild_id(request)
        or channel.type != hikari.ChannelType.GUILD_CATEGORY
    ):
        raise ApiError(422, "invalid_channel", "That isn't a category in this server.")
    return channel.id


def _roles(request: web.Request, value: Any) -> list[int]:
    roles = request.app[BOT].cache.get_roles_view_for_guild(guild_id(request))
    if not isinstance(value, list):
        raise ApiError(422, "invalid", "staff_role_ids must be a list of roles.")
    ids = [int(r) for r in value if isinstance(r, str) and r.isdigit()]
    if len(ids) != len(value) or any(r not in roles for r in ids):
        raise ApiError(422, "invalid_role", "That role isn't in this server.")
    return list(dict.fromkeys(ids))


def values_from(request: web.Request, data: dict, ticket_type: Optional[dict]) -> dict[str, Any]:
    """The columns to save, checked as `/ticket type` checks them."""
    unknown = sorted(data.keys() - EDITABLE)
    if unknown:
        raise ApiError(422, "invalid", f"Unknown setting {unknown[0]}.")
    values: dict[str, Any] = {}
    for key in data:
        if key == "name":
            values[key] = _text(data, key, 80, "A type's name")
        elif key == "emoji":
            values[key] = _optional_text(data, key, 64, "The emoji")
        elif key == "description":
            values[key] = _optional_text(data, key, 100, "The description")
        elif key == "category_id":
            values[key] = _category(request, data[key])
        elif key == "staff_role_ids":
            values[key] = _roles(request, data[key])
        elif key == "fields":
            values[key] = fields_from(data[key], ticket_type["fields"] if ticket_type else [])
        elif key == "stages":
            values[key] = stages_from(data[key])
        elif key == "claim_mode":
            if data[key] not in CLAIM_MODES:
                raise ApiError(422, "invalid", "claim_mode is reply, button or both.")
            values[key] = data[key]
        elif key == "open_limit":
            values[key] = int_field(data, key, 1, 10)
        elif key == "name_template":
            values[key] = _text(data, key, 90, "The channel name template")
        elif key in ("transcripts", "ratings", "open_off_hours"):
            values[key] = bool_field(data, key)
        elif key == "retention_days":
            days = None if data[key] is None else int_field(data, key, 0, 365)
            if days is not None and 0 < days < 7:
                raise TicketError("Retention is 0 (until deleted) or 7–365 days.")
            values[key] = days
        elif key == "auto_unclaim_hours":
            values[key] = _hours_field(data, key, 168)
        elif key == "auto_close_hours":
            values[key] = _hours_field(data, key, 720)
        elif key == "hours":
            text = data[key]
            if text is not None and not isinstance(text, str):
                raise ApiError(422, "invalid", "hours must be text, or null for the server's.")
            try:
                values["schedule"] = parse_schedule(text) if text and text.strip() else None
            except ValueError as exc:
                raise TicketError(str(exc)) from None
    return values


def _shown(key: str, value: Any) -> str:
    """A value as the Server log shows it."""
    if key == "fields":
        return ", ".join(f["label"] for f in value) or "none"
    if key == "stages":
        return " → ".join(s["name"] for s in value) or "none"
    if key == "schedule":
        return format_schedule(value) if value else "server default"
    return "none" if value is None or value == [] else str(value)


def _record(request: web.Request, ticket_type: dict, key: str, old: Any, new: Any) -> None:
    request.app[CHANGES].record(
        guild_id(request), request[SESSION].user_id,
        f"Ticket type {ticket_type['name']}: {key.replace('_', ' ')}", _shown(key, old), _shown(key, new),
    )  # fmt: skip


async def _type_or_404(request: web.Request) -> dict:
    ticket_type = await request.app[TICKET_SVC].repo.get_type(
        guild_id(request), int(request.match_info["type_id"])
    )
    if ticket_type is None:
        raise ApiError(404, "not_found", "No such ticket type in this server.")
    return ticket_type


@contextmanager
def _checked() -> Iterator[None]:
    """The bot's TicketErrors and name clashes, as API errors."""
    try:
        yield
    except TicketError as exc:
        raise ApiError(422, "invalid", str(exc).replace("**", "")) from None
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "name_taken", "A ticket type with that name already exists.") from None


@routes.get(f"{TICKETS}/types/config")
async def list_type_configs(request: web.Request) -> web.Response:
    """Every ticket type with all its settings, for the editor."""
    types = await request.app[TICKET_SVC].repo.list_types(guild_id(request))
    return web.json_response([type_json(t) for t in types])


@routes.post(f"{TICKETS}/types")
async def create_type(request: web.Request) -> web.Response:
    """A new type, like `/ticket type create`: a name and a staff role, and anything else."""
    data = await read_json(request)
    with _checked():
        values = values_from(request, data, None)
        if "name" not in values or not values.get("staff_role_ids"):
            raise ApiError(422, "invalid", "A new ticket type needs a name and a staff role.")
        created = await request.app[TICKET_SVC].repo.create_type(
            guild_id(request), values.pop("name"), **values
        )
    request.app[CHANGES].record(
        guild_id(request), request[SESSION].user_id, "Ticket types", "", f"created {created['name']}"
    )
    return web.json_response(type_json(created), status=201)


@routes.patch(TICKETS + r"/types/{type_id:\d+}")
async def update_type(request: web.Request) -> web.Response:
    """Change any of a type's settings, like `/ticket type edit|staff|field|stage-add`."""
    data = await read_json(request)
    ticket_type = await _type_or_404(request)
    svc = request.app[TICKET_SVC]
    with _checked():
        values = values_from(request, data, ticket_type)
        updated = await svc.repo.update_type(guild_id(request), ticket_type["id"], **values)
    if updated is None:
        raise ApiError(404, "not_found", "No such ticket type in this server.")
    for key, value in values.items():
        if ticket_type[key] != value:
            _record(request, ticket_type, key, ticket_type[key], value)
    if PANEL_KEYS & values.keys():
        await svc.refresh_panels(guild_id(request), ticket_type["id"])
    return web.json_response(type_json(updated))


@routes.delete(TICKETS + r"/types/{type_id:\d+}")
async def delete_type(request: web.Request) -> web.Response:
    """Delete a type no panel shows and that has never had a ticket, like `/ticket type delete`."""
    ticket_type = await _type_or_404(request)
    if not await request.app[TICKET_SVC].repo.delete_type(guild_id(request), ticket_type["id"]):
        raise ApiError(
            409,
            "in_use",
            f"{ticket_type['name']} is still on a panel or has tickets (kept for their history). "
            "Take it off your panels; types with tickets can't be deleted.",
        )
    request.app[CHANGES].record(
        guild_id(request), request[SESSION].user_id, "Ticket types", "", f"deleted {ticket_type['name']}"
    )
    return web.Response(status=204)


@routes.post(f"{TICKETS}/types/channel-name")
async def channel_name_example(request: web.Request) -> web.Response:
    """What a channel name template gives, with the bot's renderer: ticket #42 opened by the
    dashboard user, each field answered with its label."""
    data = await read_json(request)
    template = _text(data, "template", 90, "The channel name template")
    name = data.get("type") if isinstance(data.get("type"), str) else "support"
    fields = data.get("fields") if isinstance(data.get("fields"), list) else []
    answers = {
        f["key"]: f["label"]
        for f in fields
        if isinstance(f, dict) and isinstance(f.get("key"), str) and isinstance(f.get("label"), str)
    }
    example = render_channel_name(
        template, type_name=name, number=42, username=request[SESSION].username, fields=answers
    )
    return web.json_response({"name": example})
