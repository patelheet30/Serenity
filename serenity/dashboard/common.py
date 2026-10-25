"""Shared by the dashboard's route modules: app keys, JSON errors, input checks, the change log."""

import asyncio
from typing import Any, Optional

import hikari
from aiohttp import web

from serenity.core.modules import ModuleManager
from serenity.dashboard.auth import DiscordOAuth, GuildAccess, Session
from serenity.database.logging_repository import LoggingRepository
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.repository import Repository
from serenity.services.logging_service import LoggingService

BOT = web.AppKey("bot", hikari.GatewayBot)
BASE_URL = web.AppKey("base_url", str)
ORIGIN = web.AppKey("origin", str)
SECURE = web.AppKey("secure", bool)
ACCESS = web.AppKey("access", GuildAccess)
SESSIONS = web.AppKey("sessions", Any)  # Sessions, or _NoSessions when login isn't configured
OAUTH = web.AppKey("oauth", DiscordOAuth)
REPO = web.AppKey("repo", Repository)
LOG_REPO = web.AppKey("log_repo", LoggingRepository)
MOD_REPO = web.AppKey("mod_repo", ModerationRepository)
MODULES = web.AppKey("modules", ModuleManager)
CHANGES = web.AppKey("changes", Any)  # ChangeLog
SESSION = web.RequestKey("session", Session)
ACCESS_LEVEL = web.RequestKey("access_level", str)

TEXT_TYPES = {hikari.ChannelType.GUILD_TEXT, hikari.ChannelType.GUILD_NEWS}


class ApiError(Exception):
    """Turned into {"error": {"code", "message", ...extra}} with this status by error_middleware."""

    def __init__(self, status: int, code: str, message: str, **extra: Any):
        super().__init__(message)
        self.status = status
        self.body = {"error": {"code": code, "message": message, **extra}}


def guild_id(request: web.Request) -> int:
    return int(request.match_info["guild_id"])


async def read_json(request: web.Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "Expected a JSON object.")
    return data


def int_field(data: dict[str, Any], key: str, lo: int, hi: int) -> int:
    value = data[key]
    if type(value) is not int or not lo <= value <= hi:  # type() so True isn't 1
        raise ApiError(422, "invalid", f"{key} must be a whole number from {lo} to {hi}.")
    return value


def bool_field(data: dict[str, Any], key: str) -> bool:
    if not isinstance(data[key], bool):
        raise ApiError(422, "invalid", f"{key} must be true or false.")
    return data[key]


def guild_channel(
    request: web.Request, channel_id: Any, types: set[hikari.ChannelType] = TEXT_TYPES
) -> hikari.GuildChannel:
    """A channel of one of `types` in the request's guild, or a 422."""
    channel = None
    if isinstance(channel_id, (str, int)) and str(channel_id).isdigit():
        channel = request.app[BOT].cache.get_guild_channel(int(channel_id))
    if channel is None or channel.guild_id != guild_id(request) or channel.type not in types:
        raise ApiError(422, "invalid_channel", "That channel isn't a text channel in this server.")
    return channel


def channel_label(request: web.Request, channel_id: Optional[int]) -> str:
    channel = request.app[BOT].cache.get_guild_channel(channel_id) if channel_id else None
    return f"#{channel.name}" if channel else ("none" if not channel_id else str(channel_id))


class ChangeLog:
    """Posts dashboard edits to the Server log (#42).

    Autosave sends many small writes, so edits to one setting by one user within DEBOUNCE
    seconds become a single "first old → last new" entry, and nothing if it ended unchanged.
    """

    DEBOUNCE = 10.0

    def __init__(self, logging_svc: LoggingService):
        self.logging_svc = logging_svc
        self._pending: dict[tuple[int, int, str], list[str]] = {}
        self._tasks: set[asyncio.Task] = set()

    def record(self, guild_id: int, user_id: int, setting: str, old: Any, new: Any) -> None:
        key = (guild_id, user_id, setting)
        if key in self._pending:
            self._pending[key][1] = str(new)
            return
        self._pending[key] = [str(old), str(new)]
        # ponytail: entries still waiting when the bot stops are lost; fine for an audit nicety.
        task = asyncio.create_task(self._post_later(key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _post_later(self, key: tuple[int, int, str]) -> None:
        await asyncio.sleep(self.DEBOUNCE)
        old, new = self._pending.pop(key)
        if old != new:
            guild, user, setting = key
            await self.logging_svc.log_dashboard_change(
                guild_id=guild, user_id=user, setting=setting, old=old, new=new
            )
