"""Dashboard API: the login/session boundary and the per-guild authorization check."""

import asyncio
import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import hikari
import pytest
import pytest_asyncio
import toolbox
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from cryptography.fernet import Fernet

from serenity.core.modules import ModuleType
from serenity.dashboard import auth
from serenity.dashboard.api import (
    CHANGES,
    OAUTH,
    SESSION_COOKIE,
    SESSIONS,
    STAFF_HANDLERS,
    STATE_COOKIE,
    build_api,
)
from serenity.dashboard.auth import GuildAccess, Membership, NotInstalledError, Session, Sessions
from serenity.dashboard.common import SESSION, TICKET_SVC, ChangeLog
from serenity.dashboard.tickets import ticket_access, transcript_viewer
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.ticket_service import TicketService

REAL_PERMISSIONS = toolbox.calculate_permissions  # before fake_permissions patches it

GUILD = 111
USER = 222
ORIGIN = "http://localhost:5173"


TEXT_CHANNEL = 333_000
VOICE_CHANNEL = 333_001
OTHER_GUILD_CHANNEL = 333_002


def make_channel(
    channel_id, name, kind=hikari.ChannelType.GUILD_TEXT, guild_id=GUILD, slowmode=0, parent=None
):
    spec = hikari.GuildTextChannel if kind == hikari.ChannelType.GUILD_TEXT else hikari.GuildChannel
    channel = MagicMock(
        spec=spec, id=channel_id, guild_id=guild_id, type=kind, parent_id=parent, position=0
    )
    channel.name = name
    channel.rate_limit_per_user = timedelta(seconds=slowmode)
    return channel


def make_member(perms=hikari.Permissions.MANAGE_GUILD, roles=()):
    return MagicMock(perms=perms, role_ids=list(roles), username="aria")


def make_bot(*, installed=True, member=True, perms=hikari.Permissions.MANAGE_GUILD, roles=()):
    """A bot whose cache knows one guild; calculate_permissions is patched to read .perms."""
    bot = MagicMock()
    channels = {
        c.id: c
        for c in (
            make_channel(TEXT_CHANNEL, "general", slowmode=5),
            make_channel(VOICE_CHANNEL, "lounge", hikari.ChannelType.GUILD_VOICE),
            make_channel(OTHER_GUILD_CHANNEL, "elsewhere", guild_id=GUILD + 1),
        )
    }
    bot.cache.get_guild_channel.side_effect = channels.get
    bot.cache.get_guild_channels_view_for_guild.side_effect = lambda gid: {
        k: c for k, c in channels.items() if c.guild_id == gid
    }
    bot.cache.get_guild.side_effect = lambda gid: object() if installed and gid == GUILD else None
    bot.cache.get_member.return_value = make_member(perms, roles) if member else None
    bot.rest.fetch_member = AsyncMock(side_effect=hikari.NotFoundError("", {}, b""))
    bot.cache.get_roles_view_for_guild.return_value = {}
    bot.cache.get_members_view_for_guild.return_value = {}
    bot.rest.search_members = AsyncMock(return_value=[])
    bot.get_me.return_value = MagicMock(id=999)
    ids = iter(range(334_000, 335_000))

    def create(name, kind, parent=None):  # Discord's CHANNEL_CREATE puts it in the cache too
        channel = make_channel(next(ids), name, kind, parent=parent)
        channels[channel.id] = channel
        return channel

    bot.rest.create_guild_category = AsyncMock(
        side_effect=lambda guild, name: create(name, hikari.ChannelType.GUILD_CATEGORY)
    )
    bot.rest.create_guild_text_channel = AsyncMock(
        side_effect=lambda guild, name, category, **kw: create(
            name, hikari.ChannelType.GUILD_TEXT, category.id
        )
    )
    return bot


@pytest.fixture(autouse=True)
def fake_permissions(monkeypatch):
    monkeypatch.setattr(auth.toolbox, "calculate_permissions", lambda m: m.perms)


# --- GuildAccess -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_allowed():
    assert await GuildAccess(make_bot()).level(USER, GUILD) == "manage"


@pytest.mark.asyncio
async def test_access_missing_permission():
    bot = make_bot(perms=hikari.Permissions.SEND_MESSAGES)
    assert await GuildAccess(bot).level(USER, GUILD) is None


@pytest.mark.asyncio
async def test_access_not_a_member():
    bot = make_bot(member=False)
    assert await GuildAccess(bot).level(USER, GUILD) is None
    bot.rest.fetch_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_access_bot_not_installed():
    with pytest.raises(NotInstalledError):
        await GuildAccess(make_bot(installed=False)).level(USER, GUILD)


@pytest.mark.asyncio
async def test_access_revoked_mid_session(monkeypatch):
    bot = make_bot()
    access = GuildAccess(bot)
    assert await access.level(USER, GUILD) == "manage"

    bot.cache.get_member.return_value = MagicMock(perms=hikari.Permissions.NONE)
    assert await access.level(USER, GUILD) == "manage"  # still cached

    now = time.monotonic()
    monkeypatch.setattr(auth.time, "monotonic", lambda: now + auth.ACCESS_CACHE_SECONDS + 1)
    assert await access.level(USER, GUILD) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "role_perms", "expected"),
    [
        (True, hikari.Permissions.NONE, "manage"),
        (False, hikari.Permissions.ADMINISTRATOR, "manage"),
        (False, hikari.Permissions.MANAGE_GUILD, "manage"),
        (False, hikari.Permissions.SEND_MESSAGES, None),
    ],
)
async def test_access_with_real_permission_helper(monkeypatch, owner, role_perms, expected):
    """Runs hikari-toolbox's calculate_permissions, which GuildAccess relies on to treat the
    owner and ADMINISTRATOR as having Manage Server."""
    monkeypatch.setattr(auth.toolbox, "calculate_permissions", REAL_PERMISSIONS)
    role_id = 333
    guild = MagicMock(id=GUILD, owner_id=USER if owner else 999)
    guild.get_roles.return_value = {
        GUILD: MagicMock(id=GUILD, permissions=hikari.Permissions.NONE),  # @everyone
        role_id: MagicMock(id=role_id, permissions=role_perms),
    }
    member = MagicMock(id=USER, role_ids=[role_id])
    member.get_guild.return_value = guild
    bot = make_bot()
    bot.cache.get_member.return_value = member

    assert await GuildAccess(bot).level(USER, GUILD) == expected


# --- HTTP ----------------------------------------------------------------------------------------


class FakeSessions:
    def __init__(self):
        self.session = Session(user_id=USER, username="aria", avatar=None, id_hash=b"x")
        self.deleted = False

    async def get(self, token):
        return self.session if token == "good" else None

    async def delete(self, session):
        self.deleted = True

    async def access_token(self, session, oauth):
        return "token"


@pytest_asyncio.fixture
async def client_for():
    clients = []

    async def make(bot, repo=None):
        """The API with fake sessions and Discord. Routes that touch Postgres need `repo`."""
        pool = repo.pool if repo else None
        tickets = TicketService(TicketRepository(pool), bot.rest, bot.cache, MagicMock())
        api = build_api(bot, repo or MagicMock(pool=None), tickets)
        api[SESSIONS] = FakeSessions()
        api[CHANGES] = MagicMock()  # ChangeLog has its own tests

        async def fake_oauth(app):
            app[OAUTH] = MagicMock(authorize_url=lambda state, prompt: f"https://discord/{prompt}")
            yield

        api.cleanup_ctx.append(fake_oauth)
        root = web.Application()
        root.add_subapp("/api", api)
        client = TestClient(TestServer(root))
        await client.start_server()
        client.api = api  # type: ignore[attr-defined]
        clients.append(client)
        return client

    yield make
    for c in clients:
        await c.close()


PLACEHOLDERS = {
    "{guild_id}": str(GUILD),
    "{channel_id}": str(TEXT_CHANNEL),
    "{module}": "slowmode",
    "{log_type}": "message",
    "{number}": "1",
    "{user_id}": str(USER),
}


def guild_routes(client, *, staff=None):
    """(method, path) for every /api route that takes a guild id, placeholders filled in;
    with `staff`, only the routes ticket staff may (True) or may not (False) use."""
    found = []
    for resource in client.server.app.router.resources():
        if hasattr(resource, "_app"):  # the /api sub-app
            for route in resource._app.router.routes():
                path = route.resource.canonical
                if staff is not None and (route.handler in STAFF_HANDLERS) != staff:
                    continue
                if "{guild_id}" in path and route.method != "HEAD":
                    for key, value in PLACEHOLDERS.items():
                        path = path.replace(key, value)
                    assert "{" not in path, f"add a placeholder for {path}"
                    found.append((route.method, path))
    assert found, "expected guild routes"
    return found


@pytest.mark.asyncio
async def test_health_is_public(client_for):
    client = await client_for(make_bot())
    resp = await client.get("/api/health")
    assert resp.status == 200
    assert await resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_every_guild_route_needs_a_session_and_permission(client_for, pg_repo, monkeypatch):
    monkeypatch.setenv("TICKETS_GUILDS", str(GUILD))
    origin = {"Origin": ORIGIN}  # so writes reach the auth check instead of the CSRF one
    denied = await client_for(make_bot(perms=hikari.Permissions.NONE), pg_repo)
    for method, path in guild_routes(denied):
        resp = await denied.request(method, path, headers=origin)
        assert resp.status == 401, (method, path)
        assert (await resp.json())["error"]["code"] == "unauthenticated"

        resp = await denied.request(method, path, headers=origin, cookies={SESSION_COOKIE: "good"})
        assert resp.status == 403, (method, path)

    await ModerationRepository(pg_repo.pool).create_case(GUILD, USER, 999, "warn")  # case #1
    tickets = TicketRepository(pg_repo.pool)
    await tickets.create_ticket(GUILD, (await tickets.create_type(GUILD, "Support"))["id"], USER, {})  # #1
    allowed = await client_for(
        make_bot(perms=hikari.Permissions.MANAGE_GUILD | hikari.Permissions.MODERATE_MEMBERS),
        pg_repo,
    )
    for method, path in guild_routes(allowed):
        resp = await allowed.request(method, path, headers=origin, cookies={SESSION_COOKIE: "good"})
        if method == "GET":
            assert resp.status == 200, (method, path)
        else:  # no body, so 400; what matters is that it got past the access check
            assert resp.status not in (401, 403) and resp.status < 500, (method, path)


@pytest.mark.asyncio
async def test_bot_not_installed_returns_invite_hint(client_for):
    client = await client_for(make_bot(installed=False))
    resp = await client.get(f"/api/guilds/{GUILD}/roles", cookies={SESSION_COOKIE: "good"})
    assert resp.status == 404
    body = await resp.json()
    assert body["error"]["code"] == "bot_not_installed"
    assert body["error"]["invite"].endswith(f"guild_id={GUILD}")


@pytest.mark.asyncio
async def test_cross_origin_post_is_rejected(client_for):
    client = await client_for(make_bot())
    cookies = {SESSION_COOKIE: "good"}
    resp = await client.post(
        "/api/auth/logout", cookies=cookies, headers={"Origin": "https://evil.example"}
    )
    assert resp.status == 403
    resp = await client.post("/api/auth/logout", cookies=cookies)  # no Origin at all
    assert resp.status == 403

    resp = await client.post("/api/auth/logout", cookies=cookies, headers={"Origin": ORIGIN})
    assert resp.status == 204


@pytest.mark.asyncio
async def test_me(client_for):
    client = await client_for(make_bot())
    assert (await client.get("/api/me")).status == 401
    resp = await client.get("/api/me", cookies={SESSION_COOKIE: "good"})
    assert (await resp.json())["id"] == str(USER)


@pytest.mark.asyncio
async def test_login_falls_back_to_consent_screen(client_for):
    client = await client_for(make_bot())

    async def callback(state=None):
        """Discord's error redirect, echoing the state cookie unless told otherwise."""
        cookies = client.session.cookie_jar.filter_cookies(client.make_url("/"))
        state = state or cookies[STATE_COOKIE].value
        resp = await client.get(
            f"/api/auth/callback?error=consent_required&state={state}", allow_redirects=False
        )
        return resp.headers["Location"]

    resp = await client.get("/api/auth/login", allow_redirects=False)
    assert resp.headers["Location"] == "https://discord/none"

    # A forged callback is rejected before it can use up the retry.
    assert (await callback(state="forged")).endswith("/?login=failed")
    assert (await callback()).endswith("/api/auth/login?prompt=consent")

    resp = await client.get("/api/auth/login?prompt=consent", allow_redirects=False)
    assert resp.headers["Location"] == "https://discord/consent"

    # Already retried once (consent cookie set by that login): give up instead of looping.
    assert (await callback()).endswith("/?login=failed")

    # Logging in again by hand starts over, so that attempt gets its own retry.
    await client.get("/api/auth/login", allow_redirects=False)
    assert (await callback()).endswith("/api/auth/login?prompt=consent")


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected", "deleted"), [(503, 502, False), (401, 401, True)])
async def test_guild_list_discord_errors(client_for, status, expected, deleted):
    """An outage keeps the session; Discord rejecting the token ends it."""
    client = await client_for(make_bot())
    err = aiohttp.ClientResponseError(MagicMock(), (), status=status)
    client.api[OAUTH].get = AsyncMock(side_effect=err)
    resp = await client.get("/api/guilds", cookies={SESSION_COOKIE: "good"})
    assert resp.status == expected
    assert client.api[SESSIONS].deleted is deleted


# --- Sessions (Postgres) -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_roundtrip_and_idle_expiry(pg_repo):
    sessions = Sessions(pg_repo.pool, Fernet.generate_key().decode())
    user = {"id": str(USER), "username": "aria", "avatar": "abc"}
    tokens = {
        "access_token": "discord-access-token",
        "refresh_token": "rt",
        "expires_at": int(time.time()) + 3600,
    }

    token = await sessions.create(user, tokens)
    session = await sessions.get(token)
    assert session is not None and session.user_id == USER
    assert await sessions.get("not-a-token") is None

    stored = await pg_repo.pool.fetchval("SELECT tokens FROM dashboard_sessions")
    assert b"discord-access-token" not in stored  # encrypted at rest
    assert await sessions.access_token(session, oauth=None) == "discord-access-token"

    await pg_repo.pool.execute("UPDATE dashboard_sessions SET last_seen = 0")
    assert await sessions.get(token) is None

    token = await sessions.create(user, tokens)
    await sessions.delete(await sessions.get(token))
    assert await sessions.get(token) is None


@pytest.mark.asyncio
async def test_refresh_failures(pg_repo):
    """A revoked grant ends the session; a Discord outage must not."""
    sessions = Sessions(pg_repo.pool, Fernet.generate_key().decode())
    user = {"id": str(USER), "username": "aria"}
    token = await sessions.create(
        user, {"access_token": "a", "refresh_token": "r", "expires_at": 0}
    )
    session = await sessions.get(token)

    def failing(status):
        err = aiohttp.ClientResponseError(MagicMock(), (), status=status)
        return MagicMock(refresh=AsyncMock(side_effect=err))

    for status in (429, 500, 503):
        with pytest.raises(aiohttp.ClientResponseError):
            await sessions.access_token(session, failing(status))
    for status in (400, 401):
        assert await sessions.access_token(session, failing(status)) is None


# --- Settings (#42, #43, #44, #46) -----------------------------------------------------------

COOKIES = {SESSION_COOKIE: "good"}
HEADERS = {"Origin": ORIGIN}


@pytest_asyncio.fixture
async def settings(client_for, pg_repo):
    """A logged-in client with Manage Server, its bot, and the recorded server-log changes."""
    bot = make_bot()
    client = await client_for(bot, pg_repo)

    async def call(method, path, body=None, **params):
        resp = await client.request(
            method,
            f"/api/guilds/{GUILD}{path}",
            json=body,
            params=params,
            cookies=COOKIES,
            headers=HEADERS,
        )
        return resp.status, await resp.json()

    def changes():
        return [c.args[2:] for c in client.api[CHANGES].record.call_args_list]

    call.bot, call.changes = bot, changes
    return call


@pytest.mark.asyncio
async def test_modules_follow_the_dependency_rules(settings):
    status, body = await settings("PUT", "/modules/moderation", {"enabled": True})
    assert status == 409 and body["error"]["code"] == "dependency"

    await settings("PUT", "/modules/logging", {"enabled": True})
    status, body = await settings("PUT", "/modules/moderation", {"enabled": True})
    assert status == 200
    assert body["enabled"] == {"slowmode": False, "moderation": True, "logging": True}
    assert body["requires"]["moderation"] == ["logging"]

    status, body = await settings("PUT", "/modules/logging", {"enabled": False})
    assert status == 409 and "moderation" in body["error"]["message"]
    assert (await settings("PUT", "/modules/nope", {"enabled": True}))[0] == 404
    assert settings.changes() == [
        ("Logging module", "off", "on"),
        ("Moderation module", "off", "on"),
    ]


@pytest.mark.asyncio
async def test_tickets_module_only_exists_for_allowlisted_servers(settings, monkeypatch):
    assert (await settings("PUT", "/modules/tickets", {"enabled": True}))[0] == 404
    status, body = await settings("GET", "/logging")
    assert "ticket" not in {t["type"] for t in body["types"]}
    assert (await settings("PUT", "/logging/ticket", {"enabled": False}))[0] == 404

    monkeypatch.setenv("TICKETS_GUILDS", str(GUILD))
    status, body = await settings("PUT", "/modules/tickets", {"enabled": True})
    assert status == 200 and body["enabled"]["tickets"] is True
    status, body = await settings("GET", "/logging")
    assert "ticket" in {t["type"] for t in body["types"]}


@pytest.mark.asyncio
async def test_slowmode_settings_match_the_command_limits(settings):
    status, body = await settings("GET", "/slowmode")
    assert (status, body["enabled"], body["default_threshold"]) == (200, True, 10)

    for bad in (
        {"default_threshold": 0},
        {"default_threshold": 101},
        {"default_threshold": True},
        {"interval": 6},
        {"enabled": "yes"},
        {"enabled": False, "default_threshold": 500},  # nothing applies if any field is bad
    ):
        status, body = await settings("PATCH", "/slowmode", bad)
        assert status == 422, bad
    assert (await settings("GET", "/slowmode"))[1]["enabled"] is True

    status, body = await settings(
        "PATCH", "/slowmode", {"enabled": False, "default_threshold": 25, "interval": 3}
    )
    assert (body["enabled"], body["default_threshold"], body["interval"]) == (False, 25, 3)
    assert settings.changes() == [
        ("Auto slowmode", "on", "off"),
        ("Default threshold", 10, 25),
        ("Update interval", "30 s", "3 min"),  # the table default, unclamped in the log
    ]


@pytest.mark.asyncio
async def test_slowmode_channels(settings, pg_repo):
    assert (await settings("PUT", f"/slowmode/channels/{VOICE_CHANNEL}", {}))[0] == 422
    assert (await settings("PUT", f"/slowmode/channels/{OTHER_GUILD_CHANNEL}", {}))[0] == 422
    assert (await settings("PUT", f"/slowmode/channels/{TEXT_CHANNEL}", {"threshold": 101}))[
        0
    ] == 422

    path = f"/slowmode/channels/{TEXT_CHANNEL}"
    status, body = await settings("PUT", path, {"enabled": False, "threshold": 15})
    assert body["channels"] == [
        {"id": str(TEXT_CHANNEL), "enabled": False, "threshold": 15, "slowmode": 5}
    ]
    status, body = await settings("PUT", path, {"threshold": None})
    assert body["channels"][0]["threshold"] is None
    assert (await pg_repo.get_channel_config(TEXT_CHANNEL, GUILD)).threshold is None

    # Another guild's row can't be removed through this guild's URL.
    await pg_repo.get_channel_config(OTHER_GUILD_CHANNEL, GUILD + 1)
    await settings("DELETE", f"/slowmode/channels/{OTHER_GUILD_CHANNEL}")
    assert await pg_repo.get_channel_configs(GUILD + 1) != []

    status, body = await settings("DELETE", path)
    assert body["channels"] == []
    assert ("Threshold for #general", 15, "server default") in settings.changes()


@pytest.mark.asyncio
async def test_slowmode_live_rates_match_stats(settings, pg_repo):
    await settings("PUT", f"/slowmode/channels/{TEXT_CHANNEL}", {})
    now = int(time.time())
    for ts in (now, now - 30, now - 90):
        await pg_repo.record_message_activity(TEXT_CHANNEL, ts)
    await pg_repo.record_slowmode_change(TEXT_CHANNEL, 0, 5, "busy", 12.5, 0.8)

    status, body = await settings("GET", "/slowmode/live")
    [live] = body["channels"]
    assert live["rate"] == await pg_repo.get_message_rate(TEXT_CHANNEL, 60) == 2.0  # as /stats
    assert live["history"][-2:] == [1, 2] and len(live["history"]) == 24
    assert live["slowmode"] == 5

    status, body = await settings("GET", "/slowmode/history")
    assert body == [
        {
            "channel_id": str(TEXT_CHANNEL),
            "old": 0,
            "new": 5,
            "reason": "busy",
            "rate": 12.5,
            "confidence": 0.8,
            "at": body[0]["at"],
        }
    ]
    status, body = await settings("GET", "/slowmode/analytics")
    assert status == 200 and len(body["hourly"]) == 24 and body["effectiveness"] == []
    assert body["changes"] == {"6h": 1, "24h": 1}


@pytest.mark.asyncio
async def test_log_types(settings):
    status, body = await settings("PUT", "/logging/message", {"enabled": True})
    assert status == 409 and body["error"]["code"] == "no_channel"
    assert (await settings("PUT", "/logging/message", {"channel_id": str(VOICE_CHANNEL)}))[0] == 422

    status, body = await settings("PUT", "/logging/message", {"channel_id": str(TEXT_CHANNEL)})
    message = next(t for t in body["types"] if t["type"] == "message")
    assert message == {"type": "message", "channel_id": str(TEXT_CHANNEL), "enabled": True}

    status, body = await settings("PUT", "/logging/message", {"enabled": False})
    assert not next(t for t in body["types"] if t["type"] == "message")["enabled"]
    assert settings.changes() == [
        ("Message log channel", "none", "#general"),
        ("Message log", "off", "on"),
        ("Message log", "on", "off"),
    ]


@pytest.mark.asyncio
async def test_logging_setup_is_idempotent(settings, pg_repo, monkeypatch):
    rest = settings.bot.rest
    status, body = await settings("POST", "/logging/setup")
    assert status == 200 and body["failed"] == []
    # Everything but the ticket log, which only servers using tickets get.
    assert {t["type"] for t in body["types"] if t["enabled"] and t["channel_id"]} == {
        "member", "message", "voice", "server", "mod"
    }
    assert rest.create_guild_category.await_count == 1
    assert rest.create_guild_text_channel.await_count == 5

    await settings("POST", "/logging/setup")
    assert rest.create_guild_text_channel.await_count == 5  # nothing new

    # A log type that lost its config gets its old channel back by name, not a new one.
    await pg_repo.pool.execute("DELETE FROM log_channels WHERE log_type = 'voice'")
    status, body = await settings("POST", "/logging/setup")
    assert next(t for t in body["types"] if t["type"] == "voice")["channel_id"]
    assert rest.create_guild_category.await_count == 1
    assert rest.create_guild_text_channel.await_count == 5

    monkeypatch.setenv("TICKETS_GUILDS", str(GUILD))
    await pg_repo.set_module_enabled(GUILD, ModuleType.TICKETS, True)
    status, body = await settings("POST", "/logging/setup")
    assert next(t for t in body["types"] if t["type"] == "ticket")["channel_id"]
    assert rest.create_guild_text_channel.await_count == 6

    rest.create_guild_category.side_effect = hikari.ForbiddenError("", {}, b"")
    await pg_repo.pool.execute("DELETE FROM log_channels")
    settings.bot.cache.get_guild_channels_view_for_guild.side_effect = lambda gid: {}
    status, body = await settings("POST", "/logging/setup")
    assert status == 409 and body["error"]["code"] == "missing_permissions"


@pytest.mark.asyncio
async def test_log_ignores(settings):
    user = {"target_type": "user", "target_id": "4242"}
    await settings("POST", "/logging/ignores", {**user, "log_type": "message"})
    status, body = await settings("POST", "/logging/ignores", {**user, "log_type": "*"})
    # "*" replaces the per-type row, as /logging ignore-user does
    assert [i["log_type"] for i in body["ignores"]] == ["*"]

    assert (await settings("POST", "/logging/ignores", {**user, "log_type": "all"}))[0] == 422
    bad_channel = {"target_type": "channel", "target_id": str(OTHER_GUILD_CHANNEL)}
    assert (await settings("POST", "/logging/ignores", bad_channel))[0] == 422

    voice = {"target_type": "channel", "target_id": str(VOICE_CHANNEL), "log_type": "voice"}
    status, body = await settings("POST", "/logging/ignores", voice)
    assert {
        "target_type": "channel",
        "target_id": str(VOICE_CHANNEL),
        "log_type": "voice",
        "name": "lounge",
    } in body["ignores"]

    # One scope, then everything; a deleted channel's ignore can still be removed.
    status, body = await settings("DELETE", "/logging/ignores", **user, log_type="*")
    assert [i["target_type"] for i in body["ignores"]] == ["channel"]
    status, body = await settings(
        "DELETE", "/logging/ignores", target_type="channel", target_id=str(VOICE_CHANNEL)
    )
    assert body["ignores"] == []
    status, body = await settings(
        "DELETE", "/logging/ignores", target_type="channel", target_id="123456"
    )
    assert status == 200


@pytest.mark.asyncio
async def test_change_log_batches_rapid_edits(monkeypatch):
    monkeypatch.setattr(ChangeLog, "DEBOUNCE", 0.01)
    svc = MagicMock(log_dashboard_change=AsyncMock())
    changes = ChangeLog(svc)

    changes.record(GUILD, USER, "Default threshold", 10, 11)
    changes.record(GUILD, USER, "Default threshold", 11, 12)
    assert len(changes._timers) == 1  # a repeat edit updates the entry, it doesn't add a timer
    changes.record(GUILD, USER, "Update interval", "1 min", "2 min")
    changes.record(GUILD, USER, "Update interval", "2 min", "1 min")  # back where it started
    await asyncio.sleep(0.05)

    svc.log_dashboard_change.assert_awaited_once_with(
        guild_id=GUILD, user_id=USER, setting="Default threshold", old="10", new="12"
    )


def test_dashboard_limits_match_the_slash_commands():
    """The dashboard promises "the same limits as the commands"; keep them from drifting."""
    from serenity.dashboard import settings as dash
    from serenity.extensions import admin

    def limits(command, option):
        o = getattr(admin, command).options[option].to_command_option()
        return o.min_value, o.max_value

    assert limits("set_threshold", "threshold") == (1, dash.THRESHOLD_MAX)
    assert limits("set_channel_threshold", "threshold") == (0, dash.THRESHOLD_MAX)
    assert limits("set_update_interval", "interval") == dash.INTERVAL_MINUTES


@pytest.mark.asyncio
async def test_missing_fields_are_422_not_500(settings):
    assert (await settings("PUT", "/modules/logging", {}))[0] == 422
    assert (await settings("PATCH", "/slowmode", {"interval": None}))[0] == 422  # int_field
    assert (await settings("PUT", f"/slowmode/channels/{TEXT_CHANNEL}", {"enabled": None}))[
        0
    ] == 422


@pytest.mark.asyncio
async def test_change_log_posts_the_oldest_early_when_full(monkeypatch):
    monkeypatch.setattr(ChangeLog, "DEBOUNCE", 0.05)
    monkeypatch.setattr(ChangeLog, "PENDING_MAX", 2)
    svc = MagicMock(log_dashboard_change=AsyncMock())
    changes = ChangeLog(svc)

    for i in range(3):
        changes.record(GUILD, USER, f"setting {i}", "a", "b")
    assert len(changes._pending) == 2
    assert len(changes._timers) == 2  # the evicted entry's timer was cancelled, not left asleep
    await asyncio.sleep(0)  # the early post runs
    assert svc.log_dashboard_change.await_args.kwargs["setting"] == "setting 0"

    await asyncio.sleep(0.1)
    assert svc.log_dashboard_change.await_count == 3  # each posted once
    assert changes._pending == {}


# --- Moderation (#47, #48) -------------------------------------------------------------------

MOD_A, MOD_B = 777, 778


@pytest_asyncio.fixture
async def cases(pg_repo):
    """60 cases: #1-#60, every 3rd a ban by MOD_B, the rest warnings by MOD_A."""
    repo = ModerationRepository(pg_repo.pool)
    for i in range(1, 61):
        ban = i % 3 == 0
        await repo.create_case(
            GUILD, USER + i % 2, MOD_B if ban else MOD_A, "ban" if ban else "warn", f"reason {i}"
        )
    await repo.create_case(GUILD + 1, USER, MOD_A, "kick", "another server")
    return repo


@pytest.mark.asyncio
async def test_case_list_filters_and_pages(settings, cases):
    status, body = await settings("GET", "/cases")
    assert (status, body["total"], body["all"]) == (200, 60, 60)
    assert [c["number"] for c in body["cases"]][:3] == [60, 59, 58] and len(body["cases"]) == 50
    assert body["facets"]["action"] == {"ban": 20, "warn": 40}
    assert [(m["id"], m["count"]) for m in body["facets"]["moderator"]] == [
        (str(MOD_A), 40),
        (str(MOD_B), 20),
    ]

    status, body = await settings("GET", "/cases", before=str(body["next"]))
    assert [c["number"] for c in body["cases"]] == list(range(10, 0, -1)) and body["next"] is None
    assert "facets" not in body  # only the first page counts them

    # Malformed numbers are ignored, never an asyncpg overflow or ValueError (a 500).
    for bad in ({"before": "9" * 30}, {"user": "²,9" + "9" * 25}, {"q": "9" * 30}, {"q": "²"}):
        assert (await settings("GET", "/cases", **bad))[0] == 200, bad

    async def numbers(**query):
        return [c["number"] for c in (await settings("GET", "/cases", **query))[1]["cases"]]

    assert await numbers(action="ban", moderator=str(MOD_B), q="reason 1") == [18, 15, 12]
    assert await numbers(q="#42") == [42]
    assert await numbers(q="100%") == []  # % is literal, not a wildcard
    assert await numbers(user=str(USER), action="kick") == []  # other guild's kick stays out
    assert len(await numbers(active="1")) == 40  # active warnings; bans have no active state

    # Names come from the gateway cache: "aria" is USER, and MOD_B moderated every ban.
    named = {USER: "aria", MOD_B: "mod-b"}
    settings.bot.cache.get_members_view_for_guild.return_value = {
        i: MagicMock(id=i, username=n, display_name=n) for i, n in named.items()
    }
    assert len(await numbers(q="mod-b")) == 20
    assert len(await numbers(q="ARIA")) == 30  # USER is the target of the even-numbered cases

    # A timeout with no expiry shows as inactive, so "active" must not list it either.
    await cases.create_case(GUILD, USER, MOD_A, "timeout", "no expiry")
    assert 61 not in await numbers(active="1")


@pytest.mark.asyncio
async def test_reason_edit_matches_the_command(settings, cases):
    status, body = await settings("PATCH", "/cases/3", {"reason": "  raid  "})
    assert status == 200 and body["reason"] == "raid"
    assert (await cases.get_case(GUILD, 3))["reason"] == "raid"
    assert [c["number"] for c in body["history"]][:2] == [59, 57]  # USER + 1's cases
    assert ("Case #3 reason", "reason 3", "raid") in settings.changes()

    for bad in ({"reason": ""}, {"reason": "x" * 513}, {"reason": 5}):
        assert (await settings("PATCH", "/cases/3", bad))[0] == 422
    assert (await settings("PATCH", "/cases/999", {"reason": "x"}))[0] == 404


@pytest.mark.asyncio
async def test_clearing_warnings_needs_moderate_members(client_for, pg_repo, cases):
    path = f"/api/guilds/{GUILD}/users/{USER}/clear-warnings"
    await cases.add_warning(GUILD, USER, MOD_A, "spam", case_id=2)

    plain = await client_for(make_bot(), pg_repo)  # Manage Server only
    resp = await plain.post(path, cookies=COOKIES, headers=HEADERS)
    assert resp.status == 403

    perms = hikari.Permissions.MANAGE_GUILD | hikari.Permissions.MODERATE_MEMBERS
    mod = await client_for(make_bot(perms=perms), pg_repo)
    resp = await mod.post(path, cookies=COOKIES, headers=HEADERS)
    assert await resp.json() == {"cleared": 1}
    assert await cases.get_warning_count(GUILD, USER) == 0
    assert (await cases.get_case(GUILD, 2))["is_active"] is False  # as /clearwarns does


@pytest.mark.asyncio
async def test_modstats_totals_match_the_command(settings, cases):
    status, body = await settings("GET", "/modstats", days="7")
    assert body["totals"] == await cases.get_moderation_stats(GUILD, days=7)
    assert len(body["daily"]) == 8 and body["daily"][-1]["counts"] == {"ban": 20, "warn": 40}
    assert [m["id"] for m in body["moderators"]] == [str(MOD_A), str(MOD_B)]
    assert body["active"] == {"timeouts": 0, "warnings": 0}
    assert (await settings("GET", "/modstats", days="8"))[0] == 422


@pytest.mark.asyncio
async def test_change_log_drops_early_posts_when_the_log_is_stuck(monkeypatch):
    monkeypatch.setattr(ChangeLog, "PENDING_MAX", 1)
    monkeypatch.setattr(ChangeLog, "POSTING_MAX", 1)
    stuck = asyncio.Event()

    async def post(**kwargs):
        await stuck.wait()

    svc = MagicMock(log_dashboard_change=AsyncMock(side_effect=post))
    changes = ChangeLog(svc)

    for i in range(4):  # each new setting evicts the previous one into an early post
        changes.record(GUILD, USER, f"setting {i}", "a", "b")
    await asyncio.sleep(0)
    assert len(changes._posting) == 1 and len(changes._pending) == 1 and len(changes._timers) == 1
    stuck.set()
    for t in list(changes._timers.values()):
        t.cancel()


# --- Ticket staff access (#69) -----------------------------------------------------------------

SUPPORT_ROLE, BILLING_ROLE, SENIOR_ROLE, OTHER_ROLE = 801, 802, 803, 804


@pytest_asyncio.fixture
async def ticket_guild(pg_repo, monkeypatch):
    """Support (staff SUPPORT_ROLE; tiers Helpers, Seniors) and Billing (BILLING_ROLE), with
    ticket #1 on Support's first tier, #2 escalated to Seniors, #3 in Billing."""
    monkeypatch.setenv("TICKETS_GUILDS", str(GUILD))
    repo = TicketRepository(pg_repo.pool)
    support = await repo.create_type(GUILD, "Support", staff_role_ids=[SUPPORT_ROLE])
    billing = await repo.create_type(GUILD, "Billing", staff_role_ids=[BILLING_ROLE])
    tier = {"name": "Helpers", "position": 1, "role_ids": [SUPPORT_ROLE]}
    helpers = await repo.create_tier(support["id"], **tier)
    tier = {"name": "Seniors", "position": 2, "role_ids": [SENIOR_ROLE]}
    seniors = await repo.create_tier(support["id"], **tier)
    t1 = await repo.create_ticket(GUILD, support["id"], USER + 1, {})
    t2 = await repo.update_ticket(
        (await repo.create_ticket(GUILD, support["id"], USER + 1, {}))["id"], tier_id=seniors["id"]
    )
    t3 = await repo.create_ticket(GUILD, billing["id"], USER, {})
    return MagicMock(repo=repo, helpers=helpers, seniors=seniors, tickets=(t1, t2, t3))


@pytest.mark.asyncio
async def test_ticket_staff_only_reach_ticket_routes(client_for, pg_repo, ticket_guild):
    async def get(client, path):
        resp = await client.get(f"/api/guilds/{GUILD}{path}", cookies=COOKIES)
        return resp.status, await resp.json()

    staff = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    assert await get(staff, "/access") == (200, {"level": "staff", "tickets": True})
    assert [t["name"] for t in (await get(staff, "/tickets/types"))[1]] == ["Support"]
    for method, path in guild_routes(staff, staff=False):  # settings, modules and the rest
        resp = await staff.request(method, path, headers=HEADERS, cookies=COOKIES)
        assert resp.status == 403, (method, path)
        assert "Ticket staff" in (await resp.json())["error"]["message"]

    manager = await client_for(make_bot(), pg_repo)
    assert (await get(manager, "/access"))[1]["level"] == "manage"
    assert [t["name"] for t in (await get(manager, "/tickets/types"))[1]] == ["Support", "Billing"]

    nobody = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[OTHER_ROLE]), pg_repo)
    for method, path in guild_routes(nobody, staff=True):
        resp = await nobody.request(method, path, headers=HEADERS, cookies=COOKIES)
        assert resp.status == 403, (method, path)


@pytest.mark.asyncio
async def test_tickets_need_the_allowlist(client_for, pg_repo, ticket_guild, monkeypatch):
    monkeypatch.setenv("TICKETS_GUILDS", "")
    manager = await client_for(make_bot(), pg_repo)
    resp = await manager.get(f"/api/guilds/{GUILD}/tickets/types", cookies=COOKIES)
    assert resp.status == 404
    staff = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    assert (await staff.get(f"/api/guilds/{GUILD}/access", cookies=COOKIES)).status == 403


@pytest.mark.asyncio
async def test_tier_roles_see_only_tickets_with_their_tier(client_for, pg_repo, ticket_guild):
    client = await client_for(make_bot(), pg_repo)
    svc = client.api[TICKET_SVC]
    t1, t2, t3 = ticket_guild.tickets

    async def sees(*roles, perms=hikari.Permissions.NONE):
        access = await ticket_access(client.api, GUILD, Membership(perms, frozenset(roles)))
        return [t["number"] for t in (t1, t2, t3) if access.can_see(t, await svc.full_type(t))]

    assert await sees(SUPPORT_ROLE) == [1, 2]  # type staff: every Support ticket
    assert await sees(SENIOR_ROLE) == [2]  # the escalated one only
    assert await sees(BILLING_ROLE, SENIOR_ROLE) == [2, 3]
    assert await sees(OTHER_ROLE) == []
    assert await sees(perms=hikari.Permissions.MANAGE_GUILD) == [1, 2, 3]

    # De-escalated back to Helpers: Seniors no longer see it.
    t2.update(await ticket_guild.repo.update_ticket(t2["id"], tier_id=ticket_guild.helpers["id"]))
    assert await sees(SENIOR_ROLE) == []


@pytest.mark.asyncio
async def test_removing_the_staff_role_revokes_access_within_a_minute(
    client_for, pg_repo, ticket_guild, monkeypatch
):
    bot = make_bot(perms=hikari.Permissions.NONE, roles=[SENIOR_ROLE])
    client = await client_for(bot, pg_repo)
    path = f"/api/guilds/{GUILD}/tickets/types"
    assert (await client.get(path, cookies=COOKIES)).status == 200

    bot.cache.get_member.return_value = make_member(hikari.Permissions.NONE)
    assert (await client.get(path, cookies=COOKIES)).status == 200  # cached roles
    now = time.monotonic()
    monkeypatch.setattr(auth.time, "monotonic", lambda: now + auth.ACCESS_CACHE_SECONDS + 1)
    assert (await client.get(path, cookies=COOKIES)).status == 403

    # Taking the role off the type (here, the tier) applies at once.
    bot.cache.get_member.return_value = make_member(hikari.Permissions.NONE, [SENIOR_ROLE])
    monkeypatch.setattr(auth.time, "monotonic", lambda: now + 3 * auth.ACCESS_CACHE_SECONDS)
    assert (await client.get(path, cookies=COOKIES)).status == 200
    await ticket_guild.repo.update_tier(ticket_guild.seniors["id"], role_ids=[])
    assert (await client.get(path, cookies=COOKIES)).status == 403


@pytest.mark.asyncio
async def test_openers_can_view_their_own_transcripts(client_for, pg_repo, ticket_guild):
    t1, _, t3 = ticket_guild.tickets  # USER opened #3 (Billing), not #1 (Support)

    async def viewer(bot, ticket):
        client = await client_for(bot, pg_repo)
        request = make_mocked_request("GET", "/", app=client.api)
        request[SESSION] = FakeSessions().session
        return await transcript_viewer(request, ticket)

    nobody = dict(perms=hikari.Permissions.NONE)
    assert await viewer(make_bot(**nobody), t3) == "opener"
    assert await viewer(make_bot(member=False), t3) == "opener"  # left the server
    assert await viewer(make_bot(installed=False), t3) == "opener"  # or the bot did
    assert await viewer(make_bot(**nobody), t1) is None
    assert await viewer(make_bot(**nobody, roles=[SUPPORT_ROLE]), t1) == "staff"
    assert await viewer(make_bot(), t3) == "staff"  # managers see notes on their own too


@pytest.mark.asyncio
async def test_picker_marks_ticket_staff_guilds(client_for, pg_repo, ticket_guild):
    bot = make_bot(perms=hikari.Permissions.NONE, roles=[BILLING_ROLE])
    client = await client_for(bot, pg_repo)
    client.api[OAUTH].get = AsyncMock(
        return_value=[
            {"id": str(GUILD), "name": "Main", "permissions": "0"},
            {"id": str(GUILD + 1), "name": "Elsewhere", "permissions": "0"},
        ]
    )
    resp = await client.get("/api/guilds", cookies=COOKIES)
    assert [(g["name"], g["manageable"], g["staff"]) for g in await resp.json()] == [
        ("Elsewhere", False, False),
        ("Main", False, True),
    ]
