"""Dashboard routes for outgoing ticket webhooks and their delivery log (#77), as /ticket webhook.

A webhook's signing secret is returned only when it's created or rotated, never listed or logged.
"""

from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from serenity.dashboard.common import CHANGES, SESSION, TICKET_SVC, ApiError, guild_id, read_json
from serenity.dashboard.tickets import TICKETS, routes
from serenity.services.ticket_webhooks import EVENTS, MAX_WEBHOOKS, WebhookError

WEBHOOK = TICKETS + r"/webhooks/{webhook_id:\d+}"
URL_MAX = 500  # as /ticket webhook add


def _webhook_json(hook: dict) -> dict[str, Any]:
    out = {k: hook[k] for k in ("id", "url", "events", "type_id", "created_at")}
    if "last_at" in hook:  # from list_webhooks
        out["last"] = None if hook["last_at"] is None else {
            "event": hook["last_event"],
            "attempts": hook["last_attempts"],
            "status": hook["last_status"],
            "latency_ms": hook["last_latency_ms"],
            "error": hook["last_error"],
            "at": hook["last_at"],
        }
    return out


async def _fields(request: web.Request, data: dict[str, Any]) -> dict[str, Any]:
    """The url, events and type_id present in `data`, checked as the slash command checks them."""
    values: dict[str, Any] = {}
    if "url" in data:
        url = data["url"]
        if not isinstance(url, str) or not url.strip() or len(url.strip()) > URL_MAX:
            raise ApiError(422, "invalid", f"The URL must be 1 to {URL_MAX} characters.")
        values["url"] = url.strip()
    if "events" in data:
        events = data["events"]
        if not isinstance(events, list) or any(e not in EVENTS for e in events):
            raise ApiError(
                422,
                "invalid",
                "Events must be some of: " + ", ".join(e.removeprefix("ticket.") for e in EVENTS),
            )
        values["events"] = list(dict.fromkeys(events))  # [] means every event
    if "type_id" in data:
        type_id = data["type_id"]
        if type_id is not None and (
            type(type_id) is not int
            or await request.app[TICKET_SVC].repo.get_type(guild_id(request), type_id) is None
        ):
            raise ApiError(422, "invalid", "There's no such ticket type.")
        values["type_id"] = type_id
    return values


def _webhook_id(request: web.Request) -> int:
    return int(request.match_info["webhook_id"])


def _missing(request: web.Request) -> ApiError:
    return ApiError(404, "not_found", f"There's no webhook #{_webhook_id(request)}.")


def _record(request: web.Request, hook_id: int, old: str, new: str) -> None:
    request.app[CHANGES].record(
        guild_id(request), request[SESSION].user_id, f"Ticket webhook #{hook_id}", old, new
    )


def _summary(hook: dict) -> str:
    """For the server log: the host only, since webhook URLs often carry a token."""
    events = ", ".join(e.removeprefix("ticket.") for e in hook["events"]) or "all events"
    scope = f", type {hook['type_id']} only" if hook["type_id"] else ""
    return f"{urlsplit(hook['url']).hostname} ({events}{scope})"


@routes.get(f"{TICKETS}/webhooks")
async def list_webhooks(request: web.Request) -> web.Response:
    hooks = await request.app[TICKET_SVC].repo.list_webhooks(guild_id(request))
    return web.json_response(
        {"webhooks": [_webhook_json(h) for h in hooks], "events": EVENTS, "max": MAX_WEBHOOKS}
    )


@routes.post(f"{TICKETS}/webhooks")
async def add_webhook(request: web.Request) -> web.Response:
    """Add a webhook; the response carries its secret, the only time it's shown."""
    values = await _fields(request, await read_json(request))
    if "url" not in values:
        raise ApiError(422, "invalid", f"The URL must be 1 to {URL_MAX} characters.")
    try:
        hook = await request.app[TICKET_SVC].webhooks.add(
            guild_id(request), values["url"], values.get("events", []), values.get("type_id")
        )
    except WebhookError as exc:
        raise ApiError(422, "invalid", str(exc).replace("**", "")) from None
    _record(request, hook["id"], "none", _summary(hook))
    return web.json_response({**_webhook_json(hook), "secret": hook["secret"]}, status=201)


@routes.patch(WEBHOOK)
async def edit_webhook(request: web.Request) -> web.Response:
    values = await _fields(request, await read_json(request))
    svc = request.app[TICKET_SVC]
    old = await svc.repo.get_webhook(guild_id(request), _webhook_id(request))
    if old is None:
        raise _missing(request)
    try:
        hook = await svc.webhooks.edit(guild_id(request), old["id"], **values)
    except WebhookError as exc:
        raise ApiError(422, "invalid", str(exc).replace("**", "")) from None
    if hook is None:
        raise _missing(request)
    _record(request, hook["id"], _summary(old), _summary(hook))
    return web.json_response(_webhook_json(hook))


@routes.delete(WEBHOOK)
async def remove_webhook(request: web.Request) -> web.Response:
    svc = request.app[TICKET_SVC]
    old = await svc.repo.get_webhook(guild_id(request), _webhook_id(request))
    if old is None or not await svc.repo.remove_webhook(guild_id(request), old["id"]):
        raise _missing(request)
    _record(request, old["id"], _summary(old), "removed")
    return web.json_response({"ok": True})


@routes.post(WEBHOOK + "/rotate")
async def rotate_secret(request: web.Request) -> web.Response:
    """A new signing secret, returned this once; the old one stops working at once."""
    hook = await request.app[TICKET_SVC].webhooks.rotate(guild_id(request), _webhook_id(request))
    if hook is None:
        raise _missing(request)
    _record(request, hook["id"], "secret", "secret rotated")
    return web.json_response({"secret": hook["secret"]})


@routes.post(WEBHOOK + "/test")
async def test_webhook(request: web.Request) -> web.Response:
    """Send one `ticket.test` event now (the receiver has up to 10 seconds); logged like any other."""
    svc = request.app[TICKET_SVC]
    hook = await svc.repo.get_webhook(guild_id(request), _webhook_id(request))
    if hook is None:
        raise _missing(request)
    error = await svc.webhooks.send_test(hook)
    return web.json_response({"ok": error is None, "error": error})


@routes.get(WEBHOOK + "/deliveries")
async def list_deliveries(request: web.Request) -> web.Response:
    """The webhook's last 50 deliveries, newest first (kept 7 days)."""
    rows = await request.app[TICKET_SVC].repo.list_deliveries(guild_id(request), _webhook_id(request))
    keys = ("id", "event", "attempts", "status", "latency_ms", "error", "created_at")
    return web.json_response([{k: r[k] for k in keys} for r in rows])
