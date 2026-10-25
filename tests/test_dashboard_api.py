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
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet

from serenity.dashboard import auth
from serenity.dashboard.api import CHANGES, OAUTH, SESSION_COOKIE, SESSIONS, STATE_COOKIE, build_api
from serenity.dashboard.auth import GuildAccess, NotInstalledError, Session, Sessions
from serenity.dashboard.common import ChangeLog

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


def make_bot(*, installed=True, member=True, perms=hikari.Permissions.MANAGE_GUILD):
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
    bot.cache.get_member.return_value = MagicMock(perms=perms, username="aria") if member else None
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
        api = build_api(bot, repo or MagicMock(pool=None))
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
}


def guild_routes(client):
    """(method, path) for every /api route that takes a guild id, placeholders filled in."""
    found = []
    for resource in client.server.app.router.resources():
        if hasattr(resource, "_app"):  # the /api sub-app
            for route in resource._app.router.routes():
                path = route.resource.canonical
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
async def test_every_guild_route_needs_a_session_and_permission(client_for, pg_repo):
    origin = {"Origin": ORIGIN}  # so writes reach the auth check instead of the CSRF one
    denied = await client_for(make_bot(perms=hikari.Permissions.NONE), pg_repo)
    for method, path in guild_routes(denied):
        resp = await denied.request(method, path, headers=origin)
        assert resp.status == 401, (method, path)
        assert (await resp.json())["error"]["code"] == "unauthenticated"

        resp = await denied.request(method, path, headers=origin, cookies={SESSION_COOKIE: "good"})
        assert resp.status == 403, (method, path)

    allowed = await client_for(make_bot(), pg_repo)
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
        ("Update interval", "0 min", "3 min"),
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
async def test_logging_setup_is_idempotent(settings, pg_repo):
    rest = settings.bot.rest
    status, body = await settings("POST", "/logging/setup")
    assert status == 200 and body["failed"] == []
    assert all(t["enabled"] and t["channel_id"] for t in body["types"])
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
