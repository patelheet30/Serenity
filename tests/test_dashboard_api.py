"""Dashboard API: the login/session boundary and the per-guild authorization check."""

import time
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
from serenity.dashboard.api import OAUTH, SESSION_COOKIE, SESSIONS, STATE_COOKIE, build_api
from serenity.dashboard.auth import GuildAccess, NotInstalledError, Session, Sessions

REAL_PERMISSIONS = toolbox.calculate_permissions  # before fake_permissions patches it

GUILD = 111
USER = 222
ORIGIN = "http://localhost:5173"


def make_bot(*, installed=True, member=True, perms=hikari.Permissions.MANAGE_GUILD):
    """A bot whose cache knows one guild; calculate_permissions is patched to read .perms."""
    bot = MagicMock()
    bot.cache.get_guild.side_effect = lambda gid: object() if installed and gid == GUILD else None
    bot.cache.get_member.return_value = MagicMock(perms=perms) if member else None
    bot.rest.fetch_member = AsyncMock(side_effect=hikari.NotFoundError("", {}, b""))
    bot.cache.get_guild_channels_view_for_guild.return_value = {}
    bot.cache.get_roles_view_for_guild.return_value = {}
    bot.cache.get_members_view_for_guild.return_value = {}
    bot.rest.search_members = AsyncMock(return_value=[])
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

    async def make(bot):
        api = build_api(bot, pool=None)
        api[SESSIONS] = FakeSessions()

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


def guild_routes(client):
    """Every /api route that takes a guild id, with the id filled in."""
    paths = []
    for resource in client.server.app.router.resources():
        if hasattr(resource, "_app"):  # the /api sub-app
            for sub in resource._app.router.resources():
                canonical = sub.canonical
                if "{guild_id}" in canonical:
                    paths.append(canonical.replace("{guild_id}", str(GUILD)))
    assert paths, "expected guild routes"
    return paths


@pytest.mark.asyncio
async def test_health_is_public(client_for):
    client = await client_for(make_bot())
    resp = await client.get("/api/health")
    assert resp.status == 200
    assert await resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_every_guild_route_needs_a_session_and_permission(client_for):
    denied = await client_for(make_bot(perms=hikari.Permissions.NONE))
    for path in guild_routes(denied):
        resp = await denied.get(path)
        assert resp.status == 401, path
        assert (await resp.json())["error"]["code"] == "unauthenticated"

        resp = await denied.get(path, cookies={SESSION_COOKIE: "good"})
        assert resp.status == 403, path

    allowed = await client_for(make_bot())
    for path in guild_routes(allowed):
        resp = await allowed.get(path, cookies={SESSION_COOKIE: "good"})
        assert resp.status == 200, path


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
