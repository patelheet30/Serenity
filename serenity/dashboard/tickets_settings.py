"""Dashboard routes for server-wide ticket settings and the blacklist (#75).

The same settings and limits as `/ticket settings ...` and `/ticket blacklist ...`, stored where
TicketService reads them on every open, close and rating, so they apply to the next ticket.
The ticket log channel is set with the Logging routes (PUT /logging/ticket).
"""

from typing import Any, Optional

from aiohttp import web

from serenity.dashboard.common import (
    BOT,
    CHANGES,
    LOG_REPO,
    SESSION,
    TICKET_SVC,
    ApiError,
    guild_id,
    int_field,
    read_json,
)
from serenity.dashboard.tickets import TICKETS, routes
from serenity.services.ticket_service import DEFAULT_RATING_WINDOW_DAYS, DEFAULT_RETENTION_DAYS

# Setting: (change-log label, min, max, default), as the slash commands allow.
SETTINGS = {
    "retention_days": ("Ticket transcript retention (days)", 7, 365, DEFAULT_RETENTION_DAYS),
    "rating_window_days": ("Ticket rating window (days)", 1, 30, DEFAULT_RATING_WINDOW_DAYS),
    "open_cooldown_minutes": ("Ticket cooldown (minutes)", 0, 1440, 0),
    "raid_limit": ("Ticket raid brake (tickets)", 0, 100, 0),
    "raid_window_minutes": ("Ticket raid brake (minutes)", 1, 60, 5),
    "min_account_age_days": ("Ticket minimum account age (days)", 0, 365, 0),
}


def record(request: web.Request, setting: str, old: Any, new: Any) -> None:
    request.app[CHANGES].record(guild_id(request), request[SESSION].user_id, setting, old, new)


def guild_role(request: web.Request, role_id: Any) -> int:
    """A role id of the request's guild, or a 422."""
    roles = request.app[BOT].cache.get_roles_view_for_guild(guild_id(request))
    if not (isinstance(role_id, (str, int)) and str(role_id).isdigit() and int(role_id) in roles):
        raise ApiError(422, "invalid_role", "That role isn't in this server.")
    return int(role_id)


def _user_name(request: web.Request, user_id: int) -> Optional[str]:
    cache = request.app[BOT].cache
    user = cache.get_member(guild_id(request), user_id) or cache.get_user(user_id)
    return user.username if user else None


BLACKLIST_SHOWN = 200  # ponytail: newest only; a paged list if servers block more than this


async def _settings_json(request: web.Request, *, blacklist: bool = True) -> dict[str, Any]:
    gid = guild_id(request)
    svc = request.app[TICKET_SVC]
    settings = await svc.settings(gid)
    log = await request.app[LOG_REPO].get_log_channel(gid, "ticket")
    role = settings.get("required_role_id")
    blocked = await svc.repo.list_blacklist(gid, BLACKLIST_SHOWN + 1) if blacklist else []  # one extra: are there more?
    return {
        **{key: int(settings.get(key, default)) for key, (_, _, _, default) in SETTINGS.items()},
        "limits": {key: [lo, hi] for key, (_, lo, hi, _) in SETTINGS.items()},  # the SPA's input bounds
        "raid_paused": bool(settings.get("raid_paused")),
        "required_role_id": str(role) if role else None,
        "log_channel_id": str(log["channel_id"]) if log and log["channel_id"] else None,
        "log_enabled": bool(log and log["is_enabled"] and log["channel_id"]),
        "blacklist": [
            {
                "user_id": str(b["user_id"]),
                "name": _user_name(request, b["user_id"]),
                "moderator_id": str(b["moderator_id"]),
                "reason": b["reason"],
                "created_at": b["created_at"],
            }
            for b in blocked[:BLACKLIST_SHOWN]
        ],
        "blacklist_more": len(blocked) > BLACKLIST_SHOWN,
    }


@routes.get(f"{TICKETS}/settings")
async def get_settings(request: web.Request) -> web.Response:
    return web.json_response(await _settings_json(request))


@routes.patch(f"{TICKETS}/settings")
async def patch_settings(request: web.Request) -> web.Response:
    """Any of SETTINGS, required_role_id (null for none), and raid_paused: false to resume opening."""
    data = await read_json(request)
    gid = guild_id(request)
    svc = request.app[TICKET_SVC]
    old = await _settings_json(request, blacklist=False)  # only for the change log
    changes: dict[str, Any] = {
        key: int_field(data, key, lo, hi) for key, (_, lo, hi, _) in SETTINGS.items() if key in data
    }
    if "required_role_id" in data:
        role_id = data["required_role_id"]
        changes["required_role_id"] = None if role_id is None else guild_role(request, role_id)
    resume = "raid_limit" in changes or "raid_window_minutes" in changes  # as /ticket settings raid-brake
    if "raid_paused" in data:
        if data["raid_paused"] is not False:
            raise ApiError(422, "invalid", "raid_paused can only be set to false (resume opening).")
        resume = True
    if not changes and not resume:
        raise ApiError(400, "bad_request", "Nothing to change.")

    if changes:
        await svc.update_settings(gid, **changes)
    if resume:
        await svc.resume_opening(gid)
    for key, value in changes.items():
        label = SETTINGS[key][0] if key in SETTINGS else "Ticket required role"
        new = value if key in SETTINGS else (f"<@&{value}>" if value else "none")
        was = old[key] if key in SETTINGS else (f"<@&{old[key]}>" if old[key] else "none")
        record(request, label, was, new)
    if resume and old["raid_paused"]:
        record(request, "Ticket opening", "paused", "resumed")
    return web.json_response(await _settings_json(request))


@routes.post(f"{TICKETS}/blacklist")
async def add_blacklist(request: web.Request) -> web.Response:
    """Block a user from opening tickets: user_id, reason (optional, staff only)."""
    data = await read_json(request)
    user_id, reason = data.get("user_id"), data.get("reason") or None
    if not (isinstance(user_id, str) and user_id.isdigit()):
        raise ApiError(422, "invalid", "Pick a member to block.")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
        raise ApiError(422, "invalid", "The reason can be up to 500 characters.")
    repo = request.app[TICKET_SVC].repo
    if not await repo.blacklist_add(guild_id(request), int(user_id), request[SESSION].user_id, reason):
        raise ApiError(409, "exists", "That member is already blocked from opening tickets.")
    record(request, f"Ticket blacklist: {_user_name(request, int(user_id)) or user_id}", "allowed", "blocked")
    return web.json_response(await _settings_json(request))


@routes.delete(TICKETS + r"/blacklist/{user_id:\d+}")
async def remove_blacklist(request: web.Request) -> web.Response:
    user_id = int(request.match_info["user_id"])
    if not await request.app[TICKET_SVC].repo.blacklist_remove(guild_id(request), user_id):
        raise ApiError(404, "not_found", "That member isn't blocked.")
    record(request, f"Ticket blacklist: {_user_name(request, user_id) or user_id}", "blocked", "allowed")
    return web.json_response(await _settings_json(request))

