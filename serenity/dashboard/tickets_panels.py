"""Dashboard panel editor and template packs (#73).

Panels are published, updated and deleted through TicketService, as /ticket panel does, so the
message in Discord always matches. A template pack creates its ticket types (with the same
validators as /ticket type field and stage) and returns a panel draft for the editor; nothing is
posted until the draft is published.
"""

from typing import Any

import asyncpg
import hikari
from aiohttp import web

from serenity.dashboard.common import BOT, TICKET_SVC, ApiError, guild_channel, guild_id, read_json
from serenity.dashboard.tickets import TICKETS, routes
from serenity.services.ticket_service import TicketError, set_field, with_stage

STYLES = ("buttons", "menu")  # /ticket panel's style choices
MAX_PANEL_TYPES = 25  # and its type limit

# Template packs, as plain data. A type's fields are (label, kind, options, required, identity); its stages
# are (name, terminal). Types the server already has (by name) are reused as they are.
PACKS: dict[str, dict[str, Any]] = {
    "game": {
        "name": "Game server",
        "blurb": "Player reports, appeals, bugs and store help.",
        "panel": {
            "title": "Server support",
            "description": "Pick what you need help with and we'll open a private channel with our staff.",
            "style": "buttons",
        },
        "types": [
            {
                "name": "Player report", "emoji": "🚩",
                "description": "Report a player breaking the rules.",
                "fields": [
                    ("Your in-game name", "short", None, True, True),
                    ("Player you're reporting", "short", None, True, False),
                    ("What happened?", "paragraph", None, True, False),
                    ("Evidence link", "url", None, False, False),
                ],
                "stages": [("Reviewing", False), ("Action taken", True), ("No action", True)],
            },
            {
                "name": "Ban appeal", "emoji": "⚖️",
                "description": "Appeal a ban or another punishment.",
                "fields": [
                    ("Your in-game name", "short", None, True, True),
                    ("Why should we lift it?", "paragraph", None, True, False),
                ],
                "stages": [("Under review", False), ("Accepted", True), ("Denied", True)],
                "ratings": False,
            },
            {
                "name": "Bug report", "emoji": "🐛",
                "description": "Something in the game isn't working.",
                "fields": [
                    ("Where is it?", "select", "In game,Website,Store,Discord", True, False),
                    ("What happened?", "paragraph", None, True, False),
                ],
                "stages": [("Confirmed", False), ("Fixed", True), ("Can't reproduce", True)],
            },
            {
                "name": "Store", "emoji": "💳",
                "description": "Help with a purchase or a rank.",
                "fields": [
                    ("Your in-game name", "short", None, True, True),
                    ("Order ID", "short", None, False, True),
                    ("What's wrong?", "paragraph", None, True, False),
                ],
            },
        ],
    },
    "freelance": {
        "name": "Freelance / studio",
        "blurb": "Project enquiries, revisions and billing.",
        "panel": {
            "title": "Work with us",
            "description": "Tell us what you need and we'll get back to you in a private channel.",
            "style": "menu",
        },
        "types": [
            {
                "name": "New project", "emoji": "💼",
                "description": "Start a project or ask for a quote.",
                "fields": [
                    ("Budget", "select", "Under $500,$500–2000,$2000–10000,Over $10000", True, False),
                    ("Deadline", "date", None, False, False),
                    ("Project details", "paragraph", None, True, False),
                ],
                "stages": [
                    ("Enquiry", False), ("Quoted", False), ("In progress", False),
                    ("Delivered", True), ("Declined", True),
                ],
            },
            {
                "name": "Revision", "emoji": "✏️",
                "description": "Changes to work we've delivered.",
                "fields": [
                    ("Order ID", "short", None, True, True),
                    ("What should change?", "paragraph", None, True, False),
                ],
                "stages": [("Queued", False), ("In progress", False), ("Done", True)],
            },
            {
                "name": "Billing", "emoji": "🧾",
                "description": "Invoices, payments and refunds.",
                "fields": [
                    ("Invoice number", "short", None, False, True),
                    ("Your question", "paragraph", None, True, False),
                ],
            },
            {
                "name": "Question", "emoji": "❓",
                "description": "Anything else.",
                "fields": [("Your question", "paragraph", None, True, False)],
            },
        ],
    },
    "community": {
        "name": "Community",
        "blurb": "Support, member reports, partnerships and staff applications.",
        "panel": {
            "title": "Contact the staff team",
            "description": "Choose a topic below to open a private ticket.",
            "style": "buttons",
        },
        "types": [
            {
                "name": "Support", "emoji": "💬",
                "description": "Ask the staff team for help.",
                "fields": [("How can we help?", "paragraph", None, True, False)],
            },
            {
                "name": "Report a member", "emoji": "🚩",
                "description": "Someone is breaking the rules.",
                "fields": [
                    ("Member", "user", None, True, False),
                    ("What happened?", "paragraph", None, True, False),
                    ("Evidence link", "url", None, False, False),
                ],
                "stages": [("Reviewing", False), ("Resolved", True)],
            },
            {
                "name": "Partnership", "emoji": "🤝",
                "description": "Partner your server with ours.",
                "fields": [
                    ("Server invite", "url", None, True, False),
                    ("Member count", "number", None, True, False),
                    ("About your server", "paragraph", None, True, False),
                ],
                "stages": [("Reviewing", False), ("Accepted", True), ("Declined", True)],
            },
            {
                "name": "Staff application", "emoji": "📝",
                "description": "Apply to join the staff team.",
                "fields": [
                    ("Timezone", "short", None, True, False),
                    ("Why do you want to join?", "paragraph", None, True, False),
                ],
                "stages": [
                    ("Reviewing", False), ("Interview", False), ("Accepted", True), ("Declined", True),
                ],
                "ratings": False,
            },
        ],
    },
    "blank": {
        "name": "Blank",
        "blurb": "An empty panel for the types you already have.",
        "panel": {"title": "Open a ticket", "description": "", "style": "buttons"},
        "types": [],
    },
}


def _pack_values(spec: dict) -> dict[str, Any]:
    """A pack type as create_type values, checked by the same validators as the slash commands."""
    fields: list[dict] = []
    for label, kind, options, required, identity in spec["fields"]:
        fields = set_field(fields, label, kind, required=required, options=options, identity=identity)
    stages: list[dict] = []
    for name, terminal in spec.get("stages", []):
        stages = with_stage(stages, {"name": name, "color": None, "terminal": terminal, "auto_close": True})
    values = {"emoji": spec["emoji"], "description": spec["description"], "fields": fields, "stages": stages}
    if "ratings" in spec:
        values["ratings"] = spec["ratings"]
    return values


def _type_json(t: dict) -> dict[str, Any]:
    return {"id": t["id"], "name": t["name"], "emoji": t["emoji"], "description": t["description"]}


def _panel_json(p: dict) -> dict[str, Any]:
    return {
        "id": p["id"],
        "channel_id": str(p["channel_id"]),
        "message_id": str(p["message_id"]) if p["message_id"] else None,
        "url": f"https://discord.com/channels/{p['guild_id']}/{p['channel_id']}/{p['message_id']}"
        if p["message_id"]
        else None,
        "title": p["title"],
        "description": p["description"],
        "style": p["style"],
        "type_ids": p["type_ids"],
    }


async def _panel_values(request: web.Request, data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """The panel columns in `data`, checked with /ticket panel's limits."""
    values: dict[str, Any] = {}
    if not partial or "title" in data:
        title = data.get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 256:
            raise ApiError(422, "invalid", "A panel needs a title of at most 256 characters.")
        values["title"] = title.strip()
    if not partial or "description" in data:
        description = data.get("description") or None
        if description is not None and (not isinstance(description, str) or len(description) > 2000):
            raise ApiError(422, "invalid", "The description can be at most 2000 characters.")
        values["description"] = description
    if not partial or "style" in data:
        if data.get("style") not in STYLES:
            raise ApiError(422, "invalid", "style must be buttons or menu.")
        values["style"] = data["style"]
    if not partial or "type_ids" in data:
        ids = data.get("type_ids")
        if not isinstance(ids, list) or not ids:
            raise ApiError(422, "invalid", "A panel needs at least one ticket type.")
        ids = list(dict.fromkeys(ids))  # a type listed twice gets one button
        if len(ids) > MAX_PANEL_TYPES:
            raise ApiError(422, "invalid", f"A panel can show at most {MAX_PANEL_TYPES} ticket types.")
        known = {t["id"] for t in await request.app[TICKET_SVC].repo.list_types(guild_id(request))}
        if not all(type(i) is int and i in known for i in ids):
            raise ApiError(422, "invalid", "Panels can only show this server's ticket types.")
        values["type_ids"] = ids
    return values


async def _post(request: web.Request, panel: dict) -> dict:
    try:
        return await request.app[TICKET_SVC].post_panel(panel)
    except TicketError as e:  # missing permissions in the channel
        raise ApiError(422, "cant_post", str(e)) from None
    except hikari.HTTPError as e:
        raise ApiError(502, "discord", f"Discord refused the panel: {e.message or e}") from None


async def _panel_or_404(request: web.Request) -> dict:
    panel = await request.app[TICKET_SVC].repo.get_panel(guild_id(request), int(request.match_info["panel_id"]))
    if panel is None:
        raise ApiError(404, "not_found", "No such panel in this server.")
    return panel


@routes.get(f"{TICKETS}/panels")
async def list_panels(request: web.Request) -> web.Response:
    """The panels, the types they can show (for the preview), and the template packs."""
    repo = request.app[TICKET_SVC].repo
    return web.json_response(
        {
            "panels": [_panel_json(p) for p in await repo.list_panels(guild_id(request))],
            "types": [_type_json(t) for t in await repo.list_types(guild_id(request))],
            "packs": [
                {
                    "key": key,
                    "name": pack["name"],
                    "blurb": pack["blurb"],
                    "types": [{k: t[k] for k in ("name", "emoji", "description")} for t in pack["types"]],
                }
                for key, pack in PACKS.items()
            ],
        }
    )


@routes.post(f"{TICKETS}/panels")
async def publish_panel(request: web.Request) -> web.Response:
    """Create a panel and post it, as /ticket panel post does."""
    data = await read_json(request)
    channel = guild_channel(request, data.get("channel_id"))
    values = await _panel_values(request, data, partial=False)
    repo = request.app[TICKET_SVC].repo
    panel = await repo.create_panel(
        guild_id(request), channel.id, values["title"], values["type_ids"], values["description"], values["style"]
    )
    try:
        panel = await _post(request, panel)
    except BaseException:  # cancelled too: don't leave an unposted panel behind
        await repo.delete_panel(guild_id(request), panel["id"])
        raise
    return web.json_response(_panel_json(panel), status=201)


@routes.patch(TICKETS + r"/panels/{panel_id:\d+}")
async def update_panel(request: web.Request) -> web.Response:
    """Change a panel and edit its message in place (or repost it if it was deleted)."""
    data = await read_json(request)
    panel = await _panel_or_404(request)
    if "channel_id" in data and str(data["channel_id"]) != str(panel["channel_id"]):
        raise ApiError(422, "invalid", "A panel stays in its channel. Delete it and publish a new one to move it.")
    values = await _panel_values(request, data, partial=True)
    if values:
        panel = await request.app[TICKET_SVC].repo.update_panel(guild_id(request), panel["id"], **values)
        if panel is None:
            raise ApiError(404, "not_found", "No such panel in this server.")
    return web.json_response(_panel_json(await _post(request, panel)))


@routes.delete(TICKETS + r"/panels/{panel_id:\d+}")
async def delete_panel(request: web.Request) -> web.Response:
    """Delete a panel and its message."""
    await request.app[TICKET_SVC].delete_panel(await _panel_or_404(request))
    return web.json_response({"ok": True})


@routes.post(TICKETS + r"/packs/{pack}")
async def apply_pack(request: web.Request) -> web.Response:
    """Create a pack's ticket types (reusing ones the server already has by name) and return the
    pack's panel draft showing them. `staff_role_id` (optional) handles the new types."""
    data = await read_json(request)
    pack = PACKS.get(request.match_info["pack"])
    if pack is None:
        raise ApiError(404, "not_found", "No such template pack.")
    gid = guild_id(request)
    role = data.get("staff_role_id")
    if role is not None:
        roles = request.app[BOT].cache.get_roles_view_for_guild(gid)
        if not str(role).isdigit() or int(role) not in roles:
            raise ApiError(422, "invalid_role", "That role isn't in this server.")
    repo = request.app[TICKET_SVC].repo
    types = []
    for spec in pack["types"]:
        existing = await repo.get_type_by_name(gid, spec["name"])
        if existing is None:
            try:
                existing = await repo.create_type(
                    gid, spec["name"], staff_role_ids=[int(role)] if role else [], **_pack_values(spec)
                )
            except asyncpg.UniqueViolationError:  # created by someone else a moment ago
                existing = await repo.get_type_by_name(gid, spec["name"])
        types.append(existing)
    return web.json_response(
        {
            "types": [_type_json(t) for t in types],
            "panel": {**pack["panel"], "type_ids": [t["id"] for t in types]},
        }
    )
