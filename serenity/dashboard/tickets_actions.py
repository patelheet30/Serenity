"""Ticket detail and staff actions (#71), all through the bot's TicketService so they reach Discord.

Every action answers with the ticket's detail (GET {TICKETS}/{number}), refreshed:
    POST  {TICKETS}/{number}/claim
    POST  {TICKETS}/{number}/unclaim
    POST  {TICKETS}/{number}/assign      {"user_id": "<id>"}
    POST  {TICKETS}/{number}/escalate    {"tier": "<name>" (default: the next up), "reason": "..."}
    POST  {TICKETS}/{number}/deescalate  {"reason": "..."}
    POST  {TICKETS}/{number}/stage       {"stage": "<name>"}
    POST  {TICKETS}/{number}/priority    {"priority": "low|normal|high|urgent"}
    POST  {TICKETS}/{number}/notes       {"text": "..."}
    PATCH {TICKETS}/{number}/fields      {"fields": {"<key>": "<answer>", ...}} (every field)
    POST  {TICKETS}/{number}/close       {"reason": "..."}
    POST  {TICKETS}/{number}/mod         {"user_id": "<id>", "action": "warn|timeout|kick|ban",
                                          "reason": "...", "duration": "30m" (timeouts)}
GET {TICKETS}/{number}/messages is the channel's latest messages, read from Discord, not stored;
GET {TICKETS}/{number}/assignees?query= the staff it could be assigned to.
Refusals are 422s with the slash commands' wording; a closed ticket is a 409.
"""

import asyncio
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Optional

import hikari
from aiohttp import web

from serenity.core.modules import ModuleType
from serenity.dashboard.common import (
    BOT,
    CHANGES,
    MOD_REPO,
    MODULES,
    PERMISSIONS,
    SESSION,
    TICKET_SVC,
    ApiError,
    guild_id,
    read_json,
)
from serenity.dashboard.moderation import _case_json, _user
from serenity.dashboard.tickets import TICKET_ACCESS, TICKETS, staff_routes, ticket_or_404
from serenity.dashboard.tickets_queue import full_types, ticket_json, ticket_settings, type_json
from serenity.services.moderation_service import ModerationService
from serenity.services.ticket_service import (
    MOD_ACTIONS,
    PRIORITIES,
    TicketError,
    field_type,
    message_entry,
    parse_duration,
)
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

TICKET = TICKETS + r"/{number:\d+}"
MESSAGES = 50  # the conversation preview
ASSIGNEES = 25
REASON_MAX = 500  # as the slash commands and modals allow
NOTE_MAX = 2000
RENAME_WAIT = 5.0  # seconds a priority change waits on Discord's channel-rename limit
_renames: set[asyncio.Task] = set()  # priority changes still waiting on Discord
_MENTION = re.compile(r"<@!?(\d+)>")
MANAGE = hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR


@contextmanager
def bot_errors(request: web.Request) -> Iterator[None]:
    """The bot's refusals as 422s, its mentions and markdown made plain for the page."""
    try:
        yield
    except (TicketError, SerenityPermissionError) as exc:
        text = _MENTION.sub(lambda m: "@" + (_user(request, int(m[1]))["name"] or m[1]), str(exc))
        raise ApiError(422, "refused", text.replace("**", "").replace("`", "")) from None
    except hikari.ClientHTTPResponseError as exc:
        raise ApiError(502, "discord", f"Discord refused that: {exc.message or exc.status}.") from None


async def member(request: web.Request, user_id: int) -> Optional[hikari.Member]:
    bot = request.app[BOT]
    found = bot.cache.get_member(guild_id(request), user_id)
    if found is None:
        try:
            found = await bot.rest.fetch_member(guild_id(request), user_id)
        except hikari.NotFoundError:
            return None
    return found


async def actor(request: web.Request) -> hikari.Member:
    """The user as a Discord member, for the bot-side checks the slash commands make."""
    found = await member(request, request[SESSION].user_id)
    if found is None:
        raise ApiError(403, "forbidden", "You're not in this server.")
    return found


async def open_ticket(request: web.Request) -> tuple[dict, dict]:
    ticket, ticket_type = await ticket_or_404(request)
    if ticket["status"] != "open" or not ticket["channel_id"]:
        raise ApiError(409, "closed", "This ticket is closed.")
    return ticket, ticket_type


def text_field(data: dict, key: str, limit: int, *, required: bool) -> Optional[str]:
    """A trimmed string of at most `limit` characters; None for an optional one left blank."""
    value = data.get(key)
    if not required and (value is None or (isinstance(value, str) and not value.strip())):
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ApiError(422, "invalid", f"{key} must be 1 to {limit} characters.")
    return value.strip()


def user_id_field(data: dict) -> int:
    value = data.get("user_id")
    if not isinstance(value, str) or not value.isdecimal() or len(value) > 20:
        raise ApiError(422, "invalid", "user_id must be a member's id.")
    return int(value)


async def moderation_on(request: web.Request) -> bool:
    return await request.app[MODULES].is_enabled(guild_id(request), ModuleType.MODERATION)


def _brief(request: web.Request, t: dict) -> dict[str, Any]:
    return {
        "number": t["number"],
        "type_id": t["type_id"],
        "status": t["status"],
        "opener": _user(request, t["opener_id"]),
        "opened_at": t["opened_at"],
        "closed_at": t["closed_at"],
        **({"linked_by": t["linked_by"]} if "linked_by" in t else {}),
    }


async def detail(request: web.Request, ticket: dict, ticket_type: dict) -> dict[str, Any]:
    """Everything the split view shows besides the conversation."""
    svc, access, gid = request.app[TICKET_SVC], request[TICKET_ACCESS], guild_id(request)
    types = await full_types(request)

    def visible(t: dict) -> bool:  # other tickets show only if the user could open them too
        return t["type_id"] in types and access.can_see(t, types[t["type_id"]])

    previous, _ = await svc.repo.opener_tickets(gid, ticket["opener_id"], ticket["id"], limit=10)
    linked = await svc.repo.linked_tickets(ticket["id"])
    moderation = None
    if await moderation_on(request):
        mod_repo, now = request.app[MOD_REPO], int(time.time())
        involved = [ticket["opener_id"]] + [
            int(ticket["fields"][f["key"]])
            for f in ticket_type["fields"]
            if field_type(f) == "user" and ticket["fields"].get(f["key"], "").isdigit()
        ]
        moderation = [
            {
                "user": _user(request, user_id),
                "warnings": await mod_repo.get_warning_count(gid, user_id),
                "cases": [_case_json(request, c, now) for c in await mod_repo.get_user_cases(gid, user_id, limit=3)],
            }
            for user_id in list(dict.fromkeys(involved))[:4]
        ]
    me = await member(request, request[SESSION].user_id)
    perms = request[PERMISSIONS]
    return {
        **ticket_json(request, ticket, ticket_type, await ticket_settings(request)),
        "closed_at": ticket["closed_at"],
        "close_reason": ticket["close_reason"],
        "type": type_json(ticket_type),
        "transcripts": ticket_type["transcripts"],
        "handlers": [
            {"user": _user(request, h["user_id"]), "role": h["role"], "messages": h["message_count"]}
            for h in await svc.repo.list_handlers(ticket["id"])
        ],
        "notes": [
            {"author": _user(request, n["author_id"]), "body": n["body"], "created_at": n["created_at"]}
            for n in await svc.repo.list_notes(ticket["id"])
        ],
        "events": [
            {
                "kind": e["kind"],
                "actor": _user(request, e["actor_id"]) if e["actor_id"] else None,
                "data": e["data"],
                "created_at": e["created_at"],
            }
            for e in await svc.repo.list_events(ticket["id"])
        ],
        "previous": [_brief(request, t) for t in previous if visible(t)],
        "linked": [_brief(request, t) for t in linked if visible(t)],
        "moderation": moderation,
        "can": {
            "unclaim": ticket["assignee_id"] == request[SESSION].user_id or bool(perms & MANAGE),
            "reassign": me is not None and svc.can_reassign(me, ticket, ticket_type),
            "mod": [a for a, p in MOD_ACTIONS.items() if perms & (p | hikari.Permissions.ADMINISTRATOR)]
            if moderation is not None
            else [],
        },
    }


async def respond(request: web.Request, ticket: dict, **extra: Any) -> web.Response:
    """The ticket's detail, read again (the service may have changed more than it returned)."""
    svc = request.app[TICKET_SVC]
    fresh = await svc.repo.get_ticket_by_id(ticket["id"])
    return web.json_response({**await detail(request, fresh, await svc.full_type(fresh)), **extra})  # type: ignore[arg-type]


@staff_routes.get(TICKET)
async def get_ticket(request: web.Request) -> web.Response:
    return web.json_response(await detail(request, *await ticket_or_404(request)))


@staff_routes.get(TICKET + "/messages")
async def messages(request: web.Request) -> web.Response:
    """The channel's latest MESSAGES, oldest first; none once it's closed (see its transcript)."""
    ticket, _ = await ticket_or_404(request)
    if ticket["status"] != "open" or not ticket["channel_id"]:
        return web.json_response([])
    found = []
    with bot_errors(request):
        async for m in request.app[BOT].rest.fetch_messages(ticket["channel_id"]).limit(MESSAGES):
            entry = message_entry(m, None)
            found.append({**entry, "id": str(m.id), "author": {**entry["author"], "id": str(m.author.id)}})
    return web.json_response(found[::-1])


@staff_routes.get(TICKET + "/assignees")
async def assignees(request: web.Request) -> web.Response:
    """Members who could take the ticket (its type's staff), for the reassign picker; ?query=."""
    _, ticket_type = await ticket_or_404(request)
    text = request.query.get("query", "").strip().lower()[:100]
    svc, found = request.app[TICKET_SVC], []
    # ponytail: cached members only, like the members picker's first pass.
    for m in request.app[BOT].cache.get_members_view_for_guild(guild_id(request)).values():
        if (text in m.username.lower() or text in m.display_name.lower()) and svc.is_staff(m, ticket_type):
            found.append(_user(request, m.id))
            if len(found) == ASSIGNEES:
                break
    return web.json_response(found)


@staff_routes.post(TICKET + "/claim")
async def claim(request: web.Request) -> web.Response:
    ticket, _ = await open_ticket(request)
    with bot_errors(request):
        await request.app[TICKET_SVC].claim(ticket, request[SESSION].user_id)
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/unclaim")
async def unclaim(request: web.Request) -> web.Response:
    """As /ticket unclaim: the assignee, or an admin."""
    ticket, _ = await open_ticket(request)
    assignee, me = ticket["assignee_id"], request[SESSION].user_id
    if assignee is None:
        raise ApiError(422, "refused", "Nobody has claimed this ticket.")
    if assignee != me and not request[PERMISSIONS] & MANAGE:
        name = _user(request, assignee)["name"] or assignee
        raise ApiError(422, "refused", f"Only @{name} or an admin can unclaim this ticket.")
    note = f"↩️ <@{assignee}> is no longer handling this ticket."
    with bot_errors(request):
        unclaimed = await request.app[TICKET_SVC].unclaim(ticket, me, note)
    if not unclaimed:
        raise ApiError(422, "refused", "Someone else claimed this ticket a moment ago.")
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/assign")
async def assign(request: web.Request) -> web.Response:
    """As /ticket assign: admins, and the tier the ticket was escalated to, give it to staff."""
    ticket, ticket_type = await open_ticket(request)
    target_id = user_id_field(await read_json(request))
    svc = request.app[TICKET_SVC]
    if not svc.can_reassign(await actor(request), ticket, ticket_type):
        raise ApiError(403, "forbidden", "Only admins, or the tier this ticket was escalated to, can reassign it.")
    with bot_errors(request):
        svc.require_staff(await member(request, target_id), ticket_type)
        await svc.claim(ticket, target_id, by=request[SESSION].user_id)
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/escalate")
async def escalate(request: web.Request) -> web.Response:
    ticket, ticket_type = await open_ticket(request)
    data = await read_json(request)
    tier = data.get("tier")
    if tier is not None and not isinstance(tier, str):
        raise ApiError(422, "invalid", "tier must be a tier's name.")
    reason = text_field(data, "reason", REASON_MAX, required=False)
    with bot_errors(request):
        await request.app[TICKET_SVC].escalate(ticket, ticket_type, request[SESSION].user_id, tier, reason)
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/deescalate")
async def deescalate(request: web.Request) -> web.Response:
    ticket, ticket_type = await open_ticket(request)
    reason = text_field(await read_json(request), "reason", REASON_MAX, required=False)
    with bot_errors(request):
        await request.app[TICKET_SVC].deescalate(ticket, ticket_type, request[SESSION].user_id, reason)
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/stage")
async def stage(request: web.Request) -> web.Response:
    ticket, ticket_type = await open_ticket(request)
    name = text_field(await read_json(request), "stage", 100, required=True)
    with bot_errors(request):
        await request.app[TICKET_SVC].set_stage(ticket, ticket_type, name, request[SESSION].user_id)  # type: ignore[arg-type]
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/priority")
async def priority(request: web.Request) -> web.Response:
    """The priority is saved at once; the channel rename can wait up to 10 minutes on Discord, so
    past RENAME_WAIT it finishes in the background (rolled back there if Discord refuses)."""
    ticket, ticket_type = await open_ticket(request)
    level = (await read_json(request)).get("priority")
    if level not in PRIORITIES:
        raise ApiError(422, "invalid", f"priority must be one of {', '.join(PRIORITIES)}.")
    if ticket["priority"] == level:
        raise ApiError(422, "refused", f"This ticket is already {level} priority.")
    task = asyncio.create_task(
        request.app[TICKET_SVC].set_priority(ticket, ticket_type, level, request[SESSION].user_id)
    )
    done, _ = await asyncio.wait({task}, timeout=RENAME_WAIT)
    if done:
        with bot_errors(request):
            task.result()
        return await respond(request, ticket)
    _renames.add(task)
    task.add_done_callback(_renamed)
    return await respond(request, ticket, renaming=True)


def _renamed(task: asyncio.Task) -> None:
    _renames.discard(task)
    if not task.cancelled() and task.exception():
        logger.warning(f"A dashboard priority change was rolled back: {task.exception()}")


@staff_routes.post(TICKET + "/notes")
async def add_note(request: web.Request) -> web.Response:
    """As /ticket note: kept in the transcript, never shown to the opener."""
    ticket, ticket_type = await open_ticket(request)  # a closed ticket's transcript is saved
    text = text_field(await read_json(request), "text", NOTE_MAX, required=True)
    if not ticket_type["transcripts"]:
        raise ApiError(
            422, "refused", f"Notes are kept in the transcript, and {ticket_type['name']} tickets don't keep one."
        )
    await request.app[TICKET_SVC].repo.add_note(ticket["id"], request[SESSION].user_id, text)  # type: ignore[arg-type]
    return await respond(request, ticket)


@staff_routes.patch(TICKET + "/fields")
async def edit_fields(request: web.Request) -> web.Response:
    """Every answer, staff-only ones included, checked as the Edit fields form checks them."""
    ticket, ticket_type = await open_ticket(request)
    answers = (await read_json(request)).get("fields")
    if not isinstance(answers, dict) or not all(isinstance(v, str) for v in answers.values()):
        raise ApiError(422, "invalid", "fields must map each field's key to its answer.")
    svc = request.app[TICKET_SVC]
    with bot_errors(request):
        updated = await svc.edit_fields(ticket, ticket_type, answers, request[SESSION].user_id)
    await svc.refresh_welcome(updated, ticket_type)
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/close")
async def close(request: web.Request) -> web.Response:
    ticket, ticket_type = await open_ticket(request)
    reason = text_field(await read_json(request), "reason", REASON_MAX, required=False)
    with bot_errors(request):
        await request.app[TICKET_SVC].close(ticket, ticket_type, request[SESSION].user_id, reason)
    return await respond(request, ticket)


@staff_routes.post(TICKET + "/mod")
async def mod_action(request: web.Request) -> web.Response:
    """As the Ticket mod action menu: the bot checks the moderator's permissions and roles."""
    ticket, ticket_type = await open_ticket(request)
    data = await read_json(request)
    target_id, action = user_id_field(data), data.get("action")
    if action not in MOD_ACTIONS:
        raise ApiError(422, "invalid", f"action must be one of {', '.join(MOD_ACTIONS)}.")
    reason = text_field(data, "reason", REASON_MAX, required=False)
    if not await moderation_on(request):
        raise ApiError(422, "refused", "Turn on the Moderation module to take mod actions from tickets.")
    app = request.app
    with bot_errors(request):
        duration = parse_duration(str(data.get("duration") or "")) if action == "timeout" else None
        mod = ModerationService(app[MOD_REPO], app[CHANGES].logging_svc, app[BOT].rest)
        case = await app[TICKET_SVC].mod_action(
            ticket, ticket_type, await actor(request), target_id, action, reason, duration, mod, app[MOD_REPO]
        )
    return await respond(request, ticket, case=case)
