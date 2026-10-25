"""Dashboard login (Discord OAuth2 + server-side sessions) and per-guild authorization."""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp
import asyncpg
import hikari
import toolbox
from cryptography.fernet import Fernet, InvalidToken

from serenity.utils.logging import get_logger

logger = get_logger(__name__)

DISCORD_API = "https://discord.com/api/v10"
SESSION_IDLE_SECONDS = 7 * 24 * 3600
ACCESS_CACHE_SECONDS = 60

# What the bot itself needs in a guild (slowmode, logging, moderation).
BOT_PERMISSIONS = (
    hikari.Permissions.VIEW_CHANNEL
    | hikari.Permissions.SEND_MESSAGES
    | hikari.Permissions.EMBED_LINKS
    | hikari.Permissions.READ_MESSAGE_HISTORY
    | hikari.Permissions.MANAGE_CHANNELS
    | hikari.Permissions.MANAGE_MESSAGES
    | hikari.Permissions.KICK_MEMBERS
    | hikari.Permissions.BAN_MEMBERS
    | hikari.Permissions.MODERATE_MEMBERS
)


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    avatar: Optional[str]
    id_hash: bytes


class DiscordOAuth:
    """The few Discord OAuth2 calls the dashboard makes."""

    def __init__(
        self, http: aiohttp.ClientSession, client_id: str, client_secret: str, redirect_uri: str
    ):
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def authorize_url(self, state: str, prompt: str = "none") -> str:
        return "https://discord.com/oauth2/authorize?" + urlencode(
            {
                "client_id": self.client_id,
                "response_type": "code",
                "scope": "identify guilds",
                "redirect_uri": self.redirect_uri,
                "state": state,
                # "none" skips the screen for returning users; callback() retries with
                # "consent" if Discord answers consent_required.
                "prompt": prompt,
            }
        )

    def invite_url(self, guild_id: Optional[str] = None) -> str:
        params = {
            "client_id": self.client_id,
            "scope": "bot applications.commands",
            "permissions": str(BOT_PERMISSIONS.value),
        }
        if guild_id:
            params |= {"guild_id": guild_id, "disable_guild_select": "true"}
        return "https://discord.com/oauth2/authorize?" + urlencode(params)

    async def _token(self, **data: str) -> dict[str, Any]:
        async with self.http.post(
            f"{DISCORD_API}/oauth2/token",
            data=data,
            auth=aiohttp.BasicAuth(self.client_id, self.client_secret),
        ) as resp:
            resp.raise_for_status()
            body = await resp.json()
        return {
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_at": int(time.time()) + int(body["expires_in"]),
        }

    async def exchange(self, code: str) -> dict[str, Any]:
        return await self._token(
            grant_type="authorization_code", code=code, redirect_uri=self.redirect_uri
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._token(grant_type="refresh_token", refresh_token=refresh_token)

    async def get(self, path: str, access_token: str) -> Any:
        async with self.http.get(
            f"{DISCORD_API}{path}", headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            resp.raise_for_status()
            return await resp.json()


class Sessions:
    """Server-side sessions in Postgres. Discord tokens are stored Fernet-encrypted."""

    def __init__(self, pool: asyncpg.Pool, secret: str):
        self.pool = pool
        self.fernet = Fernet(secret)

    @staticmethod
    def _hash(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    async def create(self, user: dict[str, Any], tokens: dict[str, Any]) -> str:
        """Store a session and return the cookie token."""
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        # Lazy cleanup: logins are rare, so this keeps the table small without a cron job.
        await self.pool.execute(
            "DELETE FROM dashboard_sessions WHERE last_seen < $1", now - SESSION_IDLE_SECONDS
        )
        await self.pool.execute(
            """INSERT INTO dashboard_sessions
            (id_hash, user_id, username, avatar, tokens, created_at, last_seen)
            VALUES ($1, $2, $3, $4, $5, $6, $6)""",
            self._hash(token),
            int(user["id"]),
            user.get("global_name") or user["username"],
            user.get("avatar"),
            self.fernet.encrypt(json.dumps(tokens).encode()),
            now,
        )
        return token

    async def get(self, token: str) -> Optional[Session]:
        """The live session for a cookie token (touching its idle timer), or None."""
        now = int(time.time())
        row = await self.pool.fetchrow(
            """UPDATE dashboard_sessions SET last_seen = $2
            WHERE id_hash = $1 AND last_seen >= $3
            RETURNING user_id, username, avatar, id_hash""",
            self._hash(token),
            now,
            now - SESSION_IDLE_SECONDS,
        )
        return Session(**row) if row else None

    async def delete(self, session: Session) -> None:
        await self.pool.execute(
            "DELETE FROM dashboard_sessions WHERE id_hash = $1", session.id_hash
        )

    async def access_token(self, session: Session, oauth: DiscordOAuth) -> Optional[str]:
        """A valid Discord access token for the session, refreshing it if needed.

        None means Discord no longer accepts the grant (e.g. the user deauthorised the app).
        """
        blob = await self.pool.fetchval(
            "SELECT tokens FROM dashboard_sessions WHERE id_hash = $1", session.id_hash
        )
        if blob is None:
            return None
        try:
            tokens = json.loads(self.fernet.decrypt(blob))
        except InvalidToken:  # DASHBOARD_SECRET was rotated
            return None

        if tokens["expires_at"] - 60 > time.time():
            return tokens["access_token"]

        try:
            tokens = await oauth.refresh(tokens["refresh_token"])
        except aiohttp.ClientResponseError as e:
            if e.status in (400, 401):  # invalid_grant: revoked or already rotated
                return None
            raise  # rate limit or Discord outage: keep the session

        await self.pool.execute(
            "UPDATE dashboard_sessions SET tokens = $2 WHERE id_hash = $1",
            session.id_hash,
            self.fernet.encrypt(json.dumps(tokens).encode()),
        )
        return tokens["access_token"]


class NotInstalledError(Exception):
    """The bot isn't in the guild."""


class GuildAccess:
    """Decides whether a user may manage a guild, from the bot's view of their current roles.

    The OAuth guild list is never trusted for this: it can be stale and is client-visible.
    Decisions are cached for ACCESS_CACHE_SECONDS, so a revoked permission applies within that.
    """

    def __init__(self, bot: hikari.GatewayBot):
        self.bot = bot
        self._cache: dict[tuple[int, int], tuple[float, Optional[str]]] = {}

    async def level(self, user_id: int, guild_id: int) -> Optional[str]:
        """ "manage" if the user may manage the guild, else None. Raises NotInstalledError."""
        if self.bot.cache.get_guild(guild_id) is None:
            raise NotInstalledError  # never cached, so inviting the bot takes effect at once

        key = (user_id, guild_id)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return cached[1]

        level = await self._compute(user_id, guild_id)
        if len(self._cache) > 10_000:  # ponytail: crude bound, fine at our traffic
            self._cache.clear()
        self._cache[key] = (now + ACCESS_CACHE_SECONDS, level)
        return level

    async def _compute(self, user_id: int, guild_id: int) -> Optional[str]:
        member = self.bot.cache.get_member(guild_id, user_id)
        if member is None:
            try:
                member = await self.bot.rest.fetch_member(guild_id, user_id)
            except hikari.NotFoundError:
                return None

        if toolbox.calculate_permissions(member) & hikari.Permissions.MANAGE_GUILD:
            return "manage"
        # Tickets dashboard milestone: return "staff" here for members with a ticket-staff role.
        return None
