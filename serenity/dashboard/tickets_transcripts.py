"""Transcripts in the dashboard (#78): staff browse a guild's, openers read their own.

The viewer lives outside /guilds, so an opener who left the server can still read theirs; it
needs a session (never a public link) and transcript_viewer decides staff, opener or 404.
"""

from typing import Any, Optional

from aiohttp import web

from serenity.dashboard.auth import NotInstalledError, access_level
from serenity.dashboard.common import (
    ACCESS,
    BOT,
    SESSION,
    TICKET_SVC,
    ApiError,
    guild_id,
    int_field,
    read_json,
)
from serenity.dashboard.moderation import BIGINT_MAX, INT_MAX, parse_int
from serenity.dashboard.tickets import (
    TICKET_ACCESS,
    TICKETS,
    _type_json,
    staff_routes,
    transcript_viewer,
    visible_types,
)
from serenity.services.ticket_service import TicketError

PAGE_SIZE = 50
OUTCOMES = ("staff", "opener", "auto")
COMMENT_MAX = 500  # the close DM's rating form
# Events an opener sees inline; escalations, notes and mod actions are staff business.
OPENER_EVENTS = {
    "opened", "claimed", "assigned", "unclaimed", "member_added", "member_removed", "stage",
    "transferred", "close_requested", "close_declined", "closed", "auto_closed", "reopened",
}


def _user(app: web.Application, guild: int, user_id: Optional[int]) -> Optional[dict[str, Any]]:
    """Name and avatar from the gateway cache (None for both when unknown), or None for no user."""
    if user_id is None:
        return None
    cache = app[BOT].cache
    user = cache.get_member(guild, user_id) or cache.get_user(user_id)
    return {
        "id": str(user_id),
        "name": user.username if user else None,
        "avatar_url": str(user.display_avatar_url) if user else None,
    }


def _retention(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "expires_at": row["expires_at"],
        "held_by_case": row["held_by_case_number"],
        "deletion_requested_at": row["deletion_requested_at"],
    }


@staff_routes.get(f"{TICKETS}/transcripts")
async def list_transcripts(request: web.Request) -> web.Response:
    """Query: type (comma list), opener, staff (a handler), outcome, since/until (closed_at,
    unix seconds), before (ticket number, for paging)."""
    q = request.query
    access = request[TICKET_ACCESS]
    visible = {t["id"] for t in await visible_types(request)}
    asked = {i for v in q.get("type", "").split(",") if (i := parse_int(v, BIGINT_MAX))}
    type_ids = sorted(asked & visible) if asked else (None if access.manager else sorted(visible))
    outcome = q.get("outcome") if q.get("outcome") in OUTCOMES else None
    svc = request.app[TICKET_SVC]
    gid = guild_id(request)
    opener_id = parse_int(q.get("opener", ""), BIGINT_MAX)
    handler_id = parse_int(q.get("staff", ""), BIGINT_MAX)
    since = parse_int(q.get("since", ""), BIGINT_MAX)
    until = parse_int(q.get("until", ""), BIGINT_MAX)
    # Tier-only staff see the tickets that are with their tier. ponytail: filtered after the
    # query, so their pages can come back short; a page they can't see any of is skipped, so
    # an empty list means there's nothing left. Move can_see into SQL if that bothers anyone.
    types: dict[int, dict] = {}
    shown: list[dict] = []
    cursor = parse_int(q.get("before", ""), INT_MAX)
    while True:
        rows = await svc.repo.list_transcripts(
            gid,
            type_ids=type_ids,
            opener_id=opener_id,
            handler_id=handler_id,
            outcome=outcome,
            since=since,
            until=until,
            before=cursor,
            limit=PAGE_SIZE,
        )
        for row in rows:
            if row["type_id"] not in types:
                types[row["type_id"]] = await svc.full_type(row)
            if access.can_see(row, types[row["type_id"]]):
                shown.append(row)
        if shown or len(rows) < PAGE_SIZE:
            break
        cursor = rows[-1]["number"]
    return web.json_response(
        {
            "transcripts": [
                {
                    "id": str(t["id"]),
                    "number": t["number"],
                    "type": {"id": t["type_id"], "name": t["type_name"], "emoji": t["type_emoji"]},
                    "opener": _user(request.app, gid, t["opener_id"]),
                    "staff": [_user(request.app, gid, h) for h in t["handler_ids"]],
                    "outcome": t["outcome"],
                    "closed_at": t["closed_at"],
                    "reason": t["close_reason"],
                    "rating": t["rating"],
                    **_retention(t),
                }
                for t in shown
            ],
            "next": rows[-1]["number"] if len(rows) == PAGE_SIZE else None,
        }
    )


# --- The viewer: a session is enough, transcript_viewer decides the rest -----------------------

TRANSCRIPT = r"/transcripts/{ticket_id:\d+}"
viewer_routes = web.RouteTableDef()  # outside /guilds: auth_middleware only asks for a session


async def _viewable(request: web.Request) -> tuple[dict, dict, str]:
    """The ticket, its transcript and how the user may see it; 404 alike for missing and hidden."""
    svc = request.app[TICKET_SVC]
    ticket_id = parse_int(request.match_info["ticket_id"], BIGINT_MAX)
    ticket = await svc.repo.get_ticket_by_id(ticket_id) if ticket_id else None
    if ticket:
        transcript = await svc.repo.get_transcript(ticket["id"])
        viewer = await transcript_viewer(request, ticket) if transcript else None
        if viewer:
            return ticket, transcript, viewer  # type: ignore[return-value]
    raise ApiError(404, "not_found", "There's no such transcript, or it isn't yours to see.")


async def _manager(request: web.Request, guild: int) -> bool:
    """Manage Server there: what /ticket transcript delete asks for."""
    try:
        member = await request.app[ACCESS].member(request[SESSION].user_id, guild)
    except NotInstalledError:
        return False
    return member is not None and access_level(member.permissions) is not None


def _stringify(data: dict[str, Any]) -> dict[str, Any]:
    """Snowflakes don't survive JSON numbers in the browser."""
    return {k: str(v) if type(v) is int else v for k, v in data.items()}


@viewer_routes.get(TRANSCRIPT)
async def get_transcript(request: web.Request) -> web.Response:
    ticket, transcript, viewer = await _viewable(request)
    svc = request.app[TICKET_SVC]
    app, gid, user_id = request.app, ticket["guild_id"], request[SESSION].user_id
    staff = viewer == "staff"
    doc = transcript["document"]
    ticket_type = await svc.repo.get_type(gid, ticket["type_id"])
    guild = app[BOT].cache.get_guild(gid)
    events = [e for e in doc.get("events", []) if staff or e["kind"] in OPENER_EVENTS]
    notes = doc.get("notes", []) if staff else []
    # Everyone named outside the messages, which carry their own snapshot.
    ids = {ticket["opener_id"], doc["ticket"].get("closed_by")}
    ids |= {e["actor_id"] for e in events} | {n["author_id"] for n in notes}
    ids |= {e["data"].get(k) for e in events for k in ("assignee", "user")}
    can_rate = False
    if user_id == ticket["opener_id"]:
        try:
            await svc.check_can_rate(ticket["id"], user_id)
            can_rate = True
        except TicketError:
            pass
    rating = await svc.repo.get_rating(ticket["id"])
    return web.json_response(
        {
            "id": str(ticket["id"]),
            "viewer": viewer,
            "guild": {
                "id": str(gid),
                "name": guild.name if guild else None,
                "icon_url": str(guild.make_icon_url()) if guild and guild.icon_hash else None,
            },
            "ticket": {
                "number": ticket["number"],
                "type": _type_json(ticket_type) if ticket_type else {"name": doc["ticket"]["type"]},
                "status": ticket["status"],
                "opener_id": str(ticket["opener_id"]),
                "opened_at": doc["ticket"]["opened_at"],
                "closed_at": doc["ticket"].get("closed_at"),
                "closed_by": str(c) if (c := doc["ticket"].get("closed_by")) else None,
                "reason": doc["ticket"].get("reason"),
            },
            "fields": doc["fields"],
            "staff_fields": doc.get("staff_fields", {}) if staff else {},
            "messages": [
                {**m, "id": str(m["id"]), "author": {**m["author"], "id": str(m["author"]["id"])}}
                for m in doc["messages"]
            ],
            "events": [
                {
                    "kind": e["kind"],
                    "actor_id": str(e["actor_id"]) if e["actor_id"] else None,
                    "data": _stringify(e["data"]) if staff else {
                        k: v for k, v in _stringify(e["data"]).items() if k in ("to", "user", "assignee", "reason")
                    },
                    "created_at": e["created_at"],
                }
                for e in events
            ],
            "notes": [
                {"author_id": str(n["author_id"]), "body": n["body"], "created_at": n["created_at"]}
                for n in notes
            ],
            "users": {str(i): _user(app, gid, i) for i in ids if i},
            **_retention(transcript),
            "rating": {"rating": rating["rating"], "comment": rating["comment"]} if rating else None,
            "can_rate": can_rate,
            "can_delete": staff and await _manager(request, gid),
            "is_opener": user_id == ticket["opener_id"],
        }
    )


@viewer_routes.delete(TRANSCRIPT)
async def delete_transcript(request: web.Request) -> web.Response:
    """What /ticket transcript delete does, for managers; a transcript held as evidence stays."""
    ticket, transcript, viewer = await _viewable(request)
    if viewer != "staff" or not await _manager(request, ticket["guild_id"]):
        raise ApiError(403, "forbidden", "Deleting a transcript needs Manage Server in its server.")
    if case := transcript["held_by_case_number"]:
        raise ApiError(
            409,
            "held",
            f"This transcript is held as evidence for case #{case}. Release the hold with "
            f"/ticket transcript hold {ticket['number']} first.",
        )
    svc = request.app[TICKET_SVC]
    if not await svc.repo.delete_transcript(ticket["id"]):
        raise ApiError(404, "not_found", f"Ticket #{ticket['number']} has no transcript.")
    await svc.repo.add_event(ticket["id"], "transcript_deleted", request[SESSION].user_id)
    return web.Response(status=204)


@viewer_routes.post(f"{TRANSCRIPT}/deletion-request")
async def request_deletion(request: web.Request) -> web.Response:
    """The close DM's "Ask to delete this transcript" button."""
    ticket, _, _ = await _viewable(request)
    try:
        asked = await request.app[TICKET_SVC].request_transcript_deletion(
            ticket["id"], request[SESSION].user_id
        )
    except TicketError as e:
        raise ApiError(403, "forbidden", str(e)) from None
    if not asked:
        raise ApiError(409, "already_asked", "You've already asked, or the transcript is already gone.")
    return web.Response(status=204)


@viewer_routes.post(f"{TRANSCRIPT}/rating")
async def rate(request: web.Request) -> web.Response:
    """The close DM's star buttons and comment form. Body: {rating: 1-5, comment?}."""
    ticket, _, _ = await _viewable(request)
    data = await read_json(request)
    rating = int_field(data, "rating", 1, 5)
    comment = data.get("comment") or None
    if comment is not None and (not isinstance(comment, str) or len(comment) > COMMENT_MAX):
        raise ApiError(422, "invalid", f"A comment is at most {COMMENT_MAX} characters.")
    try:
        stored = await request.app[TICKET_SVC].rate(
            ticket["id"], request[SESSION].user_id, rating, comment and comment.strip() or None
        )
    except TicketError as e:
        raise ApiError(422, "invalid", str(e)) from None
    return web.json_response({"rating": stored["rating"], "comment": stored["comment"]})

