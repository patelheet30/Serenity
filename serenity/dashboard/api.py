"""The dashboard's JSON API, mounted at /api on the bot's web server.

Every /api/guilds/{guild_id}/... route goes through auth_middleware's guild check; handlers
never check access themselves. Ticket staff who can't manage the guild only get STAFF_HANDLERS.
"""

import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable, Optional

import aiohttp
import hikari
from aiohttp import web
from yarl import URL

from serenity.core.modules import ModuleManager
from serenity.dashboard import moderation, settings, tickets, tickets_transcripts
from serenity.dashboard.auth import (
    DiscordOAuth,
    GuildAccess,
    NotInstalledError,
    Session,
    Sessions,
)
from serenity.dashboard.common import (
    ACCESS,
    ACCESS_LEVEL,
    BASE_URL,
    BOT,
    CHANGES,
    LOG_REPO,
    MOD_REPO,
    MODULES,
    OAUTH,
    ORIGIN,
    PERMISSIONS,
    REPO,
    SECURE,
    SESSION,
    SESSIONS,
    TICKET_SVC,
    ApiError,
    ChangeLog,
    guild_id,
)
from serenity.dashboard.tickets import TICKET_ACCESS, ticket_access
from serenity.database.logging_repository import LoggingRepository
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.repository import Repository
from serenity.services.logging_service import LoggingService
from serenity.services.ticket_service import TicketService
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

SESSION_COOKIE = "serenity_session"
STATE_COOKIE = "serenity_oauth_state"
CONSENT_COOKIE = "serenity_oauth_consent"  # set while retrying with Discord's consent screen
NEXT_COOKIE = "serenity_oauth_next"  # the page to come back to after logging in
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/callback", "/api/auth/invite"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MEMBER_SEARCH_LIMIT = 25

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """JSON errors for everything under /api, plus a one-line request log."""
    start = time.perf_counter()
    try:
        response = await handler(request)
    except ApiError as e:
        response = web.json_response(e.body, status=e.status)
    except web.HTTPException as e:
        if e.status < 400:  # redirects
            raise
        response = web.json_response(
            {"error": {"code": e.reason.lower().replace(" ", "_"), "message": e.reason}},
            status=e.status,
        )
    except Exception:
        logger.exception(f"Unhandled error in {request.method} {request.path}")
        response = web.json_response(
            {"error": {"code": "internal", "message": "Something went wrong."}}, status=500
        )
    logger.info(
        f"{request.method} {request.path} -> {response.status} "
        f"({(time.perf_counter() - start) * 1000:.0f} ms)"
    )
    return response


@web.middleware
async def csrf_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Reject state-changing requests from other origins (on top of SameSite=Lax cookies)."""
    if request.method not in SAFE_METHODS and request.headers.get("Origin") != request.app[ORIGIN]:
        raise ApiError(403, "bad_origin", "Cross-origin request rejected.")
    return await handler(request)


@web.middleware
async def auth_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Require a session outside PUBLIC_PATHS, and guild access on any route with {guild_id}:
    managers get every route, ticket staff only STAFF_HANDLERS."""
    if request.path in PUBLIC_PATHS:
        return await handler(request)

    token = request.cookies.get(SESSION_COOKIE)
    session = await request.app[SESSIONS].get(token) if token else None
    if session is None:
        raise ApiError(401, "unauthenticated", "Log in with Discord first.")
    request[SESSION] = session

    if "guild_id" in request.match_info:
        gid = guild_id(request)
        try:
            member = await request.app[ACCESS].member(session.user_id, gid)
        except NotInstalledError:
            raise ApiError(
                404,
                "bot_not_installed",
                "Serenity isn't in this server.",
                invite=f"/api/auth/invite?guild_id={gid}",
            ) from None
        access = await ticket_access(request.app, gid, member)
        handler = request.match_info.handler
        if not (access.manager or access.staff):
            raise ApiError(403, "forbidden", "You need Manage Server in this server.")
        if not access.manager and handler not in STAFF_HANDLERS:
            raise ApiError(403, "forbidden", "Ticket staff can only use Tickets in this server.")
        request[ACCESS_LEVEL] = "manage" if access.manager else "staff"
        request[PERMISSIONS] = member.permissions  # type: ignore[union-attr]  # set when access is
        request[TICKET_ACCESS] = access

    return await handler(request)


def _oauth(request: web.Request) -> DiscordOAuth:
    oauth = request.app.get(OAUTH)
    if oauth is None:
        raise ApiError(503, "not_configured", "Dashboard login isn't configured on this server.")
    return oauth


def _cookie_args(request: web.Request) -> dict[str, Any]:
    return {"httponly": True, "secure": request.app[SECURE], "samesite": "Lax", "path": "/"}


def _cdn(kind: str, owner_id: Any, image: Optional[str]) -> Optional[str]:
    return f"https://cdn.discordapp.com/{kind}/{owner_id}/{image}.png" if image else None


def _session_json(session: Session) -> dict[str, Any]:
    return {
        "id": str(session.user_id),
        "username": session.username,
        "avatar_url": _cdn("avatars", session.user_id, session.avatar),
    }


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def login(request: web.Request) -> web.Response:
    state = secrets.token_urlsafe(24)
    prompt = "consent" if request.query.get("prompt") == "consent" else "none"
    response = web.HTTPFound(_oauth(request).authorize_url(state, prompt))
    response.set_cookie(STATE_COOKIE, state, max_age=600, **_cookie_args(request))
    if prompt == "consent":
        response.set_cookie(CONSENT_COOKIE, "1", max_age=600, **_cookie_args(request))
    else:  # a fresh login the user started gets its own consent retry, and its own next page
        response.del_cookie(CONSENT_COOKIE, path="/")
        if _local_path(next_path := request.query.get("next", "")):
            response.set_cookie(NEXT_COOKIE, next_path, max_age=600, **_cookie_args(request))
        else:
            response.del_cookie(NEXT_COOKIE, path="/")
    raise response


def _local_path(path: str) -> bool:
    """A page of the dashboard itself, like /transcripts/12, so ?next= can't lead off-site."""
    return re.fullmatch(r"/(?!/)[\w/-]*", path) is not None


async def callback(request: web.Request) -> web.Response:
    oauth = _oauth(request)
    base = request.app[BASE_URL]
    expected = request.cookies.get(STATE_COOKIE, "")
    state = request.query.get("state", "")
    code = request.query.get("code")

    # Discord echoes state on error redirects too, so check it before acting on anything.
    if not expected or not secrets.compare_digest(state, expected):
        raise web.HTTPFound(f"{base}/?login=failed")  # forged or stale callback
    if request.query.get("error") == "consent_required" and CONSENT_COOKIE not in request.cookies:
        # prompt=none and the user hasn't authorised the app (yet): show Discord's screen, once.
        raise web.HTTPFound(f"{base}/api/auth/login?prompt=consent")
    if not code:
        raise web.HTTPFound(f"{base}/?login=failed")  # cancelled on Discord's screen

    try:
        tokens = await oauth.exchange(code)
        user = await oauth.get("/users/@me", tokens["access_token"])
    except aiohttp.ClientResponseError as e:
        logger.warning(f"OAuth code exchange failed: {e.status}")
        raise web.HTTPFound(f"{base}/?login=failed") from None

    token = await request.app[SESSIONS].create(user, tokens)
    next_path = request.cookies.get(NEXT_COOKIE, "")
    response = web.HTTPFound(f"{base}{next_path if _local_path(next_path) else '/servers'}")
    response.set_cookie(SESSION_COOKIE, token, max_age=7 * 24 * 3600, **_cookie_args(request))
    response.del_cookie(STATE_COOKIE, path="/")
    response.del_cookie(CONSENT_COOKIE, path="/")
    response.del_cookie(NEXT_COOKIE, path="/")
    raise response


async def invite(request: web.Request) -> web.Response:
    guild_id = request.query.get("guild_id")
    raise web.HTTPFound(
        _oauth(request).invite_url(guild_id if guild_id and guild_id.isdigit() else None)
    )


async def logout(request: web.Request) -> web.Response:
    await request.app[SESSIONS].delete(request[SESSION])
    response = web.Response(status=204)
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


async def me(request: web.Request) -> web.Response:
    return web.json_response(_session_json(request[SESSION]))


async def guilds(request: web.Request) -> web.Response:
    """The user's guilds for the server picker.

    `manageable` comes from the OAuth list and, with `staff`, only drives the picker's grouping;
    opening a guild is authorised separately by auth_middleware.
    """
    oauth = _oauth(request)
    session: Session = request[SESSION]
    try:
        access_token = await request.app[SESSIONS].access_token(session, oauth)
        user_guilds = await oauth.get("/users/@me/guilds", access_token) if access_token else None
    except aiohttp.ClientResponseError as e:
        if e.status != 401:  # rate limit or outage: keep the session
            raise ApiError(502, "discord_unavailable", f"Discord returned {e.status}.") from None
        user_guilds = None  # Discord rejected a token we thought was valid
    if user_guilds is None:
        await request.app[SESSIONS].delete(session)
        raise ApiError(401, "unauthenticated", "Your Discord login expired. Log in again.")

    manage = hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR
    cache = request.app[BOT].cache
    result = [
        {
            "id": g["id"],
            "name": g["name"],
            "icon_url": _cdn("icons", g["id"], g.get("icon")),
            "manageable": g.get("owner", False) or bool(int(g.get("permissions", 0)) & manage),
            "installed": cache.get_guild(int(g["id"])) is not None,
            "staff": False,
        }
        for g in user_guilds
    ]
    # Ticket staff: only servers with ticket types can make someone staff, so only those cost
    # a member lookup.
    others = [int(g["id"]) for g in result if g["installed"] and not g["manageable"]]
    with_types = set(await request.app[TICKET_SVC].repo.guilds_with_types(others)) if others else set()
    for g in result:
        gid = int(g["id"])
        if gid in with_types:
            member = await request.app[ACCESS].member(session.user_id, gid)
            g["staff"] = (await ticket_access(request.app, gid, member)).staff
    return web.json_response(sorted(result, key=lambda g: g["name"].lower()))


async def guild_access(request: web.Request) -> web.Response:
    """What the user can open here: "manage" (everything) or "staff" (Tickets only)."""
    return web.json_response({"level": request[ACCESS_LEVEL]})


CHANNEL_TYPES = {
    hikari.ChannelType.GUILD_TEXT: "text",
    hikari.ChannelType.GUILD_NEWS: "announcement",
    hikari.ChannelType.GUILD_CATEGORY: "category",
    hikari.ChannelType.GUILD_VOICE: "voice",
    hikari.ChannelType.GUILD_STAGE: "stage",
    hikari.ChannelType.GUILD_FORUM: "forum",
}


async def channels(request: web.Request) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    view = request.app[BOT].cache.get_guild_channels_view_for_guild(guild_id)
    result = [
        {
            "id": str(c.id),
            "name": c.name,
            "type": CHANNEL_TYPES[c.type],
            "parent_id": str(c.parent_id) if c.parent_id else None,
            "position": c.position,
        }
        for c in view.values()
        if c.type in CHANNEL_TYPES
    ]
    return web.json_response(sorted(result, key=lambda c: c["position"]))


async def roles(request: web.Request) -> web.Response:
    guild_id = int(request.match_info["guild_id"])
    view = request.app[BOT].cache.get_roles_view_for_guild(guild_id)
    result = [
        {
            "id": str(r.id),
            "name": r.name,
            "color": str(r.color) if r.color else None,
            "position": r.position,
            "managed": r.is_managed,
        }
        for r in view.values()
    ]
    return web.json_response(sorted(result, key=lambda r: -r["position"]))


def _member_json(m: hikari.Member) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "username": m.username,
        "display_name": m.display_name,
        "avatar_url": str(m.display_avatar_url),
    }


async def members(request: web.Request) -> web.Response:
    """Member search for user pickers: the gateway cache first, Discord's search as fallback."""
    guild_id = int(request.match_info["guild_id"])
    query = request.query.get("query", "").strip().lower()[:100]
    bot: hikari.GatewayBot = request.app[BOT]

    found: list[hikari.Member] = []
    for m in bot.cache.get_members_view_for_guild(guild_id).values():
        if query in m.username.lower() or query in m.display_name.lower():
            found.append(m)
            if len(found) == MEMBER_SEARCH_LIMIT:
                break

    if not found and len(query) >= 2:  # keeps one-letter typos off Discord's rate limit
        found = list(await bot.rest.search_members(guild_id, query))[:MEMBER_SEARCH_LIMIT]

    return web.json_response([_member_json(m) for m in found])


def _handlers(*tables: web.RouteTableDef) -> frozenset[Any]:
    return frozenset(r.handler for t in tables for r in t if isinstance(r, web.RouteDef))


# Routes ticket staff may use.
STAFF_HANDLERS = _handlers(tickets.staff_routes) | {guild_access}


def build_api(
    bot: hikari.GatewayBot, repo: Repository, ticket_svc: TicketService
) -> web.Application:
    """The /api sub-app. Login needs DISCORD_CLIENT_ID/SECRET, DASHBOARD_URL, DASHBOARD_SECRET."""
    url = os.getenv("DASHBOARD_URL", "http://localhost:5173").rstrip("/")
    secret = os.getenv("DASHBOARD_SECRET", "")
    client_id = os.getenv("DISCORD_CLIENT_ID", "")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")

    app = web.Application(middlewares=[error_middleware, csrf_middleware, auth_middleware])
    app[BOT] = bot
    app[BASE_URL] = url
    app[ORIGIN] = str(URL(url).origin())
    app[SECURE] = url.startswith("https://")
    app[ACCESS] = GuildAccess(bot)
    app[REPO] = repo
    app[LOG_REPO] = LoggingRepository(repo.pool)  # type: ignore[arg-type]
    app[MOD_REPO] = ModerationRepository(repo.pool)  # type: ignore[arg-type]
    app[MODULES] = ModuleManager(repo)
    app[TICKET_SVC] = ticket_svc
    app[CHANGES] = ChangeLog(LoggingService(app[LOG_REPO], bot.rest))
    # Without a valid secret there are no sessions, so every protected route answers 401.
    try:
        app[SESSIONS] = Sessions(repo.pool, secret)  # type: ignore[arg-type]
    except ValueError:
        app[SESSIONS] = _NoSessions()

    configured = isinstance(app[SESSIONS], Sessions) and bool(client_id and client_secret)
    if not configured:
        logger.warning(
            "Dashboard login disabled: set DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET and a "
            "valid DASHBOARD_SECRET (Fernet key)."
        )

    async def http_client(app: web.Application) -> AsyncIterator[None]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            if configured:
                app[OAUTH] = DiscordOAuth(
                    http, client_id, client_secret, f"{url}/api/auth/callback"
                )
            yield

    app.cleanup_ctx.append(http_client)

    app.router.add_get("/health", health)
    app.router.add_get("/auth/login", login)
    app.router.add_get("/auth/callback", callback)
    app.router.add_get("/auth/invite", invite)
    app.router.add_post("/auth/logout", logout)
    app.router.add_get("/me", me)
    app.router.add_get("/guilds", guilds)
    app.router.add_get(r"/guilds/{guild_id:\d+}/access", guild_access)
    app.router.add_get(r"/guilds/{guild_id:\d+}/channels", channels)
    app.router.add_get(r"/guilds/{guild_id:\d+}/roles", roles)
    app.router.add_get(r"/guilds/{guild_id:\d+}/members", members)
    app.add_routes(settings.routes)
    app.add_routes(moderation.routes)
    app.add_routes(tickets.routes)
    app.add_routes(tickets.staff_routes)
    app.add_routes(tickets_transcripts.viewer_routes)
    return app


class _NoSessions:
    async def get(self, token: str) -> None:
        return None
