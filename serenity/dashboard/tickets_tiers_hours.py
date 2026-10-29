"""Dashboard routes for the escalation tier ladders and support hours (#76).

Tiers take the same values as `/ticket type tier`; hours and away mode the same as
`/ticket settings hours` and `/ticket settings away`. The off-hours preview is rendered by the
bot's own off_hours_text, so it's the message a ticket opened then would get.
"""

import time
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, available_timezones

import asyncpg
from aiohttp import web

from serenity.dashboard.common import (
    SESSION,
    TICKET_SVC,
    ApiError,
    bool_field,
    guild_id,
    int_field,
    read_json,
)
from serenity.dashboard.tickets import TICKETS, _type_json, routes
from serenity.dashboard.tickets_settings import guild_role, record
from serenity.services.ticket_hours import (
    format_schedule,
    next_closed,
    offline_status,
    parse_schedule,
)
from serenity.services.ticket_service import DEFAULT_OFF_HOURS, off_hours_text

MAX_TIERS = 25  # /ticket type tier's position limit


# --- Tiers ---------------------------------------------------------------------------------


def _tier_json(tier: dict) -> dict[str, Any]:
    return {
        "id": tier["id"],
        "name": tier["name"],
        "position": tier["position"],
        "role_ids": [str(r) for r in tier["role_ids"]],
        "can_summon": tier["can_summon"],
        "requires_reason": tier["requires_reason"],
        "reping_minutes": tier["reping_minutes"],
    }


async def _ladders(request: web.Request) -> list[tuple[dict, list[dict]]]:
    """Each type in this guild with its ladder, lowest tier first: two queries in all."""
    repo, gid = request.app[TICKET_SVC].repo, guild_id(request)
    tiers = await repo.guild_tiers(gid)
    return [(t, [x for x in tiers if x["type_id"] == t["id"]]) for t in await repo.list_types(gid)]


async def _ladders_json(request: web.Request) -> list[dict[str, Any]]:
    return [{**_type_json(t), "tiers": [_tier_json(x) for x in tiers]} for t, tiers in await _ladders(request)]


async def _type_or_404(request: web.Request, type_id: Any = None) -> dict:
    """The type {type_id} in the path (or `type_id`), if it's in this guild."""
    type_id = request.match_info["type_id"] if type_id is None else type_id
    found = None
    if str(type_id).isdigit():
        found = await request.app[TICKET_SVC].repo.get_type(guild_id(request), int(type_id))
    if found is None:
        raise ApiError(404, "not_found", "No such ticket type in this server.")
    return found


async def _tier_or_404(request: web.Request) -> tuple[dict, list[dict]]:
    """The tier {tier_id} and its type's whole ladder, if it's in this guild."""
    tier_id = int(request.match_info["tier_id"])
    for ticket_type, tiers in await _ladders(request):
        if tier := next((t for t in tiers if t["id"] == tier_id), None):
            return {**tier, "type_name": ticket_type["name"]}, tiers
    raise ApiError(404, "not_found", "No such tier in this server.")


def _tier_values(request: web.Request, data: dict, tiers: list[dict], tier: Optional[dict]) -> dict:
    """The tier columns in `data`, checked as /ticket type tier checks them."""
    values: dict[str, Any] = {}
    if "name" in data:
        name = data["name"].strip() if isinstance(data["name"], str) else ""
        if not 1 <= len(name) <= 50:
            raise ApiError(422, "invalid", "A tier name is 1 to 50 characters.")
        if any(t["name"].lower() == name.lower() and t is not tier for t in tiers):
            raise ApiError(409, "exists", f"There's already a tier called **{name}**.")
        values["name"] = name
    if "role_ids" in data:
        if not isinstance(data["role_ids"], list):
            raise ApiError(422, "invalid", "role_ids must be a list of roles.")
        values["role_ids"] = list(dict.fromkeys(guild_role(request, r) for r in data["role_ids"]))
    if "can_summon" in data:
        ids = {t["id"] for t in tiers if t is not tier}
        summon = data["can_summon"]
        if not isinstance(summon, list) or not all(type(i) is int and i in ids for i in summon):
            raise ApiError(422, "invalid", "can_summon must list other tiers of this type.")
        values["can_summon"] = list(dict.fromkeys(summon))
    if "requires_reason" in data:
        values["requires_reason"] = bool_field(data, "requires_reason")
    if "reping_minutes" in data:
        values["reping_minutes"] = int_field(data, "reping_minutes", 0, 1440) or None
    return values


@routes.get(f"{TICKETS}/tiers")
async def get_tiers(request: web.Request) -> web.Response:
    """Every type's ladder, lowest tier first."""
    return web.json_response(await _ladders_json(request))


@routes.post(TICKETS + r"/types/{type_id:\d+}/tiers")
async def add_tier(request: web.Request) -> web.Response:
    """A new top rung: name and role_ids, optionally the other tier columns."""
    ticket_type = await _type_or_404(request)
    data = await read_json(request)
    repo = request.app[TICKET_SVC].repo
    tiers = await repo.list_tiers(ticket_type["id"])
    values = _tier_values(request, data, tiers, None)
    if "name" not in values:
        raise ApiError(422, "invalid", "A tier name is 1 to 50 characters.")
    if not values.get("role_ids"):
        raise ApiError(422, "invalid", "A new tier needs a role.")
    if len(tiers) >= MAX_TIERS:
        raise ApiError(422, "invalid", f"A type can have up to {MAX_TIERS} tiers.")
    try:
        await repo.create_tier(ticket_type["id"], **values)  # the next rung, taken in the INSERT
    except asyncpg.UniqueViolationError:
        raise ApiError(409, "exists", "Another change took that tier name or position a moment ago. Try again.") from None
    record(request, f"{ticket_type['name']} tiers", "", f"added {values['name']}")
    return web.json_response(await _ladders_json(request))


@routes.patch(TICKETS + r"/tiers/{tier_id:\d+}")
async def patch_tier(request: web.Request) -> web.Response:
    tier, tiers = await _tier_or_404(request)
    values = _tier_values(request, await read_json(request), tiers, next(t for t in tiers if t["id"] == tier["id"]))
    if not values:
        raise ApiError(400, "bad_request", "Nothing to change.")
    await request.app[TICKET_SVC].repo.update_tier(tier["id"], **values)
    for key, value in values.items():
        record(request, f"{tier['type_name']} tier {tier['name']}: {key}", tier[key], value)
    return web.json_response(await _ladders_json(request))


@routes.delete(TICKETS + r"/tiers/{tier_id:\d+}")
async def delete_tier(request: web.Request) -> web.Response:
    """Remove a tier; tickets with it go back to the lowest one, as with /ticket type tier delete."""
    tier, _ = await _tier_or_404(request)
    await request.app[TICKET_SVC].repo.delete_tier(tier["id"])
    record(request, f"{tier['type_name']} tiers", f"had {tier['name']}", f"removed {tier['name']}")
    return web.json_response(await _ladders_json(request))


@routes.put(TICKETS + r"/types/{type_id:\d+}/tiers/order")
async def order_tiers(request: web.Request) -> web.Response:
    """tier_ids: every tier of the type, lowest rung first."""
    ticket_type = await _type_or_404(request)
    data = await read_json(request)
    repo = request.app[TICKET_SVC].repo
    tiers = await repo.list_tiers(ticket_type["id"])
    ids = data.get("tier_ids")
    if (
        not isinstance(ids, list)
        or not all(type(i) is int for i in ids)
        or sorted(ids) != sorted(t["id"] for t in tiers)
    ):
        raise ApiError(422, "invalid", "tier_ids must list each of the type's tiers once.")
    await repo.reorder_tiers(ticket_type["id"], ids)
    names = {t["id"]: t["name"] for t in tiers}
    record(
        request,
        f"{ticket_type['name']} tier order",
        " → ".join(t["name"] for t in tiers),
        " → ".join(names[i] for i in ids),
    )
    return web.json_response(await _ladders_json(request))


# --- Support hours -------------------------------------------------------------------------


def _schedule(text: Any) -> Optional[list]:
    """A schedule from its text form ("mon-fri 09:00-17:00"); null, "" or "off" is none."""
    if text is None or (isinstance(text, str) and text.strip().lower() in ("", "off")):
        return None
    if not isinstance(text, str) or len(text) > 200:
        raise ApiError(422, "invalid", "Write the hours like `mon-fri 09:00-17:00; sat 10:00-14:00`.")
    try:
        return parse_schedule(text)
    except ValueError as exc:
        raise ApiError(422, "invalid_schedule", str(exc)) from None


def _hours_changes(data: dict, settings: dict) -> dict[str, Any]:
    """Module settings from schedule, timezone, off_hours_message and away, as the commands check them."""
    changes: dict[str, Any] = {}
    if "schedule" in data:
        changes["schedule"] = _schedule(data["schedule"])
    if "timezone" in data:
        if not isinstance(data["timezone"], str) or data["timezone"] not in available_timezones():
            raise ApiError(422, "invalid", f"“{data['timezone']}” isn't a timezone I know. Pick one from the list.")
        changes["timezone"] = data["timezone"]
    if "off_hours_message" in data:
        message = data["off_hours_message"] or None
        if message is not None and (not isinstance(message, str) or len(message) > 1000):
            raise ApiError(422, "invalid", "The auto-reply can be up to 1000 characters.")
        changes["off_hours_message"] = message
    if "away" in data:
        away = data["away"]
        if away is not None and not isinstance(away, dict):
            raise ApiError(422, "invalid", "away must be an object or null.")
        if away is None:
            changes["away"] = None
        else:
            note = away.get("note") or None
            if note is not None and (not isinstance(note, str) or len(note) > 500):
                raise ApiError(422, "invalid", "The away note can be up to 500 characters.")
            until_ts = None
            if until := away.get("until"):
                try:
                    parsed = datetime.fromisoformat(str(until).strip())
                except ValueError:
                    raise ApiError(422, "invalid", "Write the date like `2026-12-27 09:00`.") from None
                tz = changes.get("timezone") or settings.get("timezone", "UTC")
                until_ts = int(parsed.replace(tzinfo=ZoneInfo(tz)).timestamp())
                if until_ts <= time.time():
                    raise ApiError(422, "invalid", "That's in the past.")
            changes["away"] = {"until": until_ts, "note": note}
    return changes


def _hours_json(settings: dict, types: list[dict]) -> dict[str, Any]:
    tz = settings.get("timezone", "UTC")
    away = settings.get("away")
    if away and away.get("until") is not None and away["until"] <= time.time():
        away = None  # ended by itself
    until = away and away.get("until")
    return {
        "schedule": settings.get("schedule"),
        "timezone": tz,
        "off_hours_message": settings.get("off_hours_message"),
        "default_message": DEFAULT_OFF_HOURS,
        "away": away and {
            **away,
            "until_local": datetime.fromtimestamp(until, ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M") if until else None,
        },
        "online": offline_status(settings, None, datetime.now(timezone.utc)) is None,
        "types": [
            {**_type_json(t), "schedule": t["schedule"], "open_off_hours": t["open_off_hours"]}
            for t in types
        ],
    }


async def _respond_hours(request: web.Request) -> web.Response:
    svc = request.app[TICKET_SVC]
    gid = guild_id(request)
    return web.json_response(_hours_json(await svc.settings(gid), await svc.repo.list_types(gid)))


def _hours_text(schedule: Optional[list]) -> str:
    return format_schedule(schedule) if schedule else "off"


@routes.get(f"{TICKETS}/hours")
async def get_hours(request: web.Request) -> web.Response:
    return await _respond_hours(request)


@routes.patch(f"{TICKETS}/hours")
async def patch_hours(request: web.Request) -> web.Response:
    """Any of schedule (text, or null for none), timezone, off_hours_message (null: the default)
    and away (null: off; {until: "2026-12-27T09:00" in the hours' timezone or null, note})."""
    gid = guild_id(request)
    svc = request.app[TICKET_SVC]
    settings = await svc.settings(gid)
    changes = _hours_changes(await read_json(request), settings)
    if not changes:
        raise ApiError(400, "bad_request", "Nothing to change.")
    await svc.update_settings(gid, **changes)
    await svc.refresh_panels(gid)  # their online/offline line
    labels = {"schedule": "Support hours", "timezone": "Support timezone", "off_hours_message": "Off-hours message"}
    for key, value in changes.items():
        if key == "away":
            record(request, "Away mode", "on" if settings.get("away") else "off", "on" if value else "off")
        elif key == "schedule":
            record(request, labels[key], _hours_text(settings.get(key)), _hours_text(value))
        else:
            record(request, labels[key], settings.get(key) or "default", value or "default")
    return await _respond_hours(request)


@routes.post(f"{TICKETS}/hours/preview")
async def preview_hours(request: web.Request) -> web.Response:
    """The auto-reply the viewer would get opening a ticket, with the saved settings and any
    unsaved hours fields (as PATCH /hours takes them) applied. type_id picks a type's own hours.

    While support is open, it's the reply from the moment it next closes; never closing, it's null.
    """
    gid = guild_id(request)
    svc = request.app[TICKET_SVC]
    data = await read_json(request)
    saved = await svc.settings(gid)
    draft = {**saved, **_hours_changes(data, saved)}
    type_schedule = None
    if data.get("type_id") is not None:  # its own hours, set on the Types page
        type_schedule = (await _type_or_404(request, data["type_id"]))["schedule"]
    now = datetime.now(timezone.utc)
    schedule = type_schedule or draft.get("schedule")
    at = now if offline_status(draft, type_schedule, now) else next_closed(schedule, draft.get("timezone", "UTC"), now)
    return web.json_response(
        {
            "online": at is not now,
            "at": int(at.timestamp()) if at else None,
            "content": off_hours_text(draft, type_schedule, request[SESSION].user_id, at) if at else None,
        }
    )
