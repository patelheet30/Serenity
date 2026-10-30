"""Dashboard routes for modules (#43), slowmode settings and activity (#44, #45), logging (#46).

Each write does what the matching slash command does, with the same limits, and is posted to
the Server log by ChangeLog (#42). The bot reads all of this from Postgres on use, so changes
apply on the next slowmode tick or log event without a restart.
"""

import time
from typing import Any, Optional

import hikari
from aiohttp import web

from serenity.core.modules import (
    MODULE_DEPENDENCIES,
    ModuleType,
)
from serenity.dashboard.common import (
    BOT,
    CHANGES,
    LOG_REPO,
    MODULES,
    REPO,
    SESSION,
    TEXT_TYPES,
    ApiError,
    bool_field,
    channel_label,
    guild_channel,
    guild_id,
    int_field,
    read_json,
)
from serenity.services.logging_service import LOG_TYPES, setup_log_channels
from serenity.utils.errors import ConfigurationError

routes = web.RouteTableDef()
GUILD = r"/guilds/{guild_id:\d+}"

THRESHOLD_MAX = 100  # /serenity guild threshold and /serenity channel threshold
INTERVAL_MINUTES = (1, 5)  # /serenity guild interval


def _record(request: web.Request, setting: str, old: Any, new: Any) -> None:
    request.app[CHANGES].record(guild_id(request), request[SESSION].user_id, setting, old, new)


# --- Modules -------------------------------------------------------------------------------


async def _modules_json(request: web.Request) -> dict[str, Any]:
    gid = guild_id(request)
    enabled = set(await request.app[MODULES].get_enabled_modules(gid))
    return {
        "enabled": {m.value: m in enabled for m in ModuleType},
        "requires": {
            m.value: [d.value for d in deps]
            for m, deps in MODULE_DEPENDENCIES.items()
        },
    }


@routes.get(f"{GUILD}/modules")
async def get_modules(request: web.Request) -> web.Response:
    return web.json_response(await _modules_json(request))


@routes.put(GUILD + r"/modules/{module}")
async def put_module(request: web.Request) -> web.Response:
    """Enable or disable one module, with /module's dependency rules."""
    try:
        module = ModuleType(request.match_info["module"])
    except ValueError:
        raise ApiError(404, "not_found", "No such module.") from None
    enabled = bool_field(await read_json(request), "enabled")
    manager = request.app[MODULES]
    gid = guild_id(request)

    was = await manager.is_enabled(gid, module)
    try:
        if enabled:
            await manager.enable_module(gid, module)
        else:
            await manager.disable_module(gid, module)
    except ConfigurationError as e:
        raise ApiError(409, "dependency", str(e)) from None
    if was != enabled:
        _record(request, f"{module.value.title()} module", _on(was), _on(enabled))
    return web.json_response(await _modules_json(request))


def _on(value: bool) -> str:
    return "on" if value else "off"


# --- Slowmode ------------------------------------------------------------------------------


def _current_slowmode(bot: hikari.GatewayBot, channel_id: int) -> Optional[int]:
    channel = bot.cache.get_guild_channel(channel_id)
    if isinstance(channel, hikari.GuildTextChannel):
        return int(channel.rate_limit_per_user.total_seconds())
    return None


def _minutes(update_interval: int) -> int:
    """Stored seconds as the 1-5 minutes the dashboard shows (the table default is 30 s)."""
    return min(max(update_interval // 60, INTERVAL_MINUTES[0]), INTERVAL_MINUTES[1])


def _interval_text(seconds: int) -> str:
    """The stored value as-is for the Server log, so 30 s -> 1 min still reads as a change."""
    return f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


async def _slowmode_json(request: web.Request) -> dict[str, Any]:
    gid = guild_id(request)
    repo, bot = request.app[REPO], request.app[BOT]
    config = await repo.get_guild_config(gid)
    return {
        "enabled": config.is_enabled,
        "default_threshold": config.default_threshold,
        "interval": _minutes(config.update_interval),
        "channels": [
            {
                "id": str(c.channel_id),
                "enabled": c.is_enabled,
                "threshold": c.threshold,
                "slowmode": _current_slowmode(bot, c.channel_id),
                "paused_until": c.paused_until if (c.paused_until or 0) > time.time() else None,
            }
            for c in await repo.get_channel_configs(gid)
            if bot.cache.get_guild_channel(c.channel_id)  # deleted channels drop out
        ],
    }


@routes.get(f"{GUILD}/slowmode")
async def get_slowmode(request: web.Request) -> web.Response:
    return web.json_response(await _slowmode_json(request))


@routes.patch(f"{GUILD}/slowmode")
async def patch_slowmode(request: web.Request) -> web.Response:
    """Server settings: enabled, default_threshold (msgs/min), interval (minutes)."""
    data = await read_json(request)
    gid = guild_id(request)
    repo = request.app[REPO]
    old = await repo.get_guild_config(gid)
    changes: dict[str, Any] = {}
    if "enabled" in data:
        changes["is_enabled"] = bool_field(data, "enabled")
    if "default_threshold" in data:
        changes["default_threshold"] = int_field(data, "default_threshold", 1, THRESHOLD_MAX)
    if "interval" in data:
        changes["update_interval"] = int_field(data, "interval", *INTERVAL_MINUTES) * 60

    await repo.update_guild_config(gid, **changes)
    if "is_enabled" in changes:
        _record(request, "Auto slowmode", _on(old.is_enabled), _on(changes["is_enabled"]))
    if "default_threshold" in changes:
        _record(request, "Default threshold", old.default_threshold, changes["default_threshold"])
    if "update_interval" in changes:
        _record(
            request,
            "Update interval",
            _interval_text(old.update_interval),
            _interval_text(changes["update_interval"]),
        )
    return web.json_response(await _slowmode_json(request))


@routes.put(GUILD + r"/slowmode/channels/{channel_id:\d+}")
async def put_slowmode_channel(request: web.Request) -> web.Response:
    """Add or update a channel: enabled, threshold (null or 0 = server default)."""
    channel = guild_channel(
        request, request.match_info["channel_id"], {hikari.ChannelType.GUILD_TEXT}
    )
    data = await read_json(request)
    repo = request.app[REPO]
    old = await repo.get_channel_config(channel.id, guild_id(request))  # creates the row

    enabled = bool_field(data, "enabled") if "enabled" in data else None
    threshold = None
    if "threshold" in data:
        threshold = (
            0 if data["threshold"] is None else int_field(data, "threshold", 0, THRESHOLD_MAX)
        )

    await repo.update_channel_config(channel.id, is_enabled=enabled, threshold=threshold)
    if enabled is not None:
        _record(request, f"Auto slowmode in #{channel.name}", _on(old.is_enabled), _on(enabled))
    if threshold is not None:
        _record(
            request,
            f"Threshold for #{channel.name}",
            old.threshold or "server default",
            threshold or "server default",
        )
    return web.json_response(await _slowmode_json(request))


@routes.delete(GUILD + r"/slowmode/channels/{channel_id:\d+}")
async def delete_slowmode_channel(request: web.Request) -> web.Response:
    channel_id = int(request.match_info["channel_id"])
    configs = await request.app[REPO].get_channel_configs(guild_id(request))
    if any(c.channel_id == channel_id for c in configs):  # only this guild's rows
        await request.app[REPO].delete_channel_config(channel_id)
        _record(request, f"Auto slowmode in <#{channel_id}>", "managed", "removed")
    return web.json_response(await _slowmode_json(request))


# --- Slowmode activity (#45) ----------------------------------------------------------------

SPARK_MINUTES = 24
HISTORY_LIMIT = 100


@routes.get(f"{GUILD}/slowmode/live")
async def slowmode_live(request: web.Request) -> web.Response:
    """Polled by the Slowmode page: per configured channel, the 1-minute rate (what /stats
    shows), messages per minute for the sparkline, and the slowmode Discord has now."""
    repo, bot = request.app[REPO], request.app[BOT]
    ids = [
        c.channel_id
        for c in await repo.get_channel_configs(guild_id(request))
        if bot.cache.get_guild_channel(c.channel_id)
    ]
    counts = await repo.get_minute_counts(ids, SPARK_MINUTES)
    return web.json_response(
        {
            "channels": [
                {
                    "id": str(i),
                    # The newest bucket is get_message_rate(i, 60)'s window: a 60 s count is
                    # messages per minute, so no extra query per channel.
                    "rate": counts[i][-1],
                    "history": counts[i],
                    "slowmode": _current_slowmode(bot, i),
                }
                for i in ids
            ]
        }
    )


def _guild_channel_ids(request: web.Request) -> list[int]:
    return list(request.app[BOT].cache.get_guild_channels_view_for_guild(guild_id(request)))


@routes.get(f"{GUILD}/slowmode/history")
async def slowmode_history(request: web.Request) -> web.Response:
    """Recent automatic changes (old -> new, reason, rate, confidence), newest first."""
    rows = await request.app[REPO].get_slowmode_changes(_guild_channel_ids(request), HISTORY_LIMIT)
    return web.json_response(
        [
            {
                "channel_id": str(r["channel_id"]),
                "old": r["old_value"],
                "new": r["new_value"],
                "reason": r["reason"],
                "rate": r["message_rate"],
                "confidence": r["confidence"],
                "at": r["timestamp"],
            }
            for r in rows
        ]
    )


@routes.get(f"{GUILD}/slowmode/analytics")
async def slowmode_analytics(request: web.Request) -> web.Response:
    """Per hour over 24 h (messages, users, avg/max slowmode), change counts, and the rate
    before/after slowmode raises over 30 days."""
    repo, ids, now = request.app[REPO], _guild_channel_ids(request), int(time.time())
    return web.json_response(
        {
            "hourly": await repo.get_hourly_analytics(ids, 24),
            # Counted in SQL: the history list is capped at HISTORY_LIMIT rows.
            "changes": {
                "6h": await repo.count_slowmode_changes(ids, now - 6 * 3600),
                "24h": await repo.count_slowmode_changes(ids, now - 24 * 3600),
            },
            "effectiveness": [
                {
                    "channel_id": str(r["channel_id"]),
                    "before": r["before"],
                    "after": r["after"],
                    "samples": r["samples"],
                }
                for r in await repo.get_effectiveness(ids)
            ],
        }
    )


# --- Logging -------------------------------------------------------------------------------


async def _logging_json(request: web.Request) -> dict[str, Any]:
    gid = guild_id(request)
    repo, cache = request.app[LOG_REPO], request.app[BOT].cache
    configs = {c["log_type"]: c for c in await repo.get_all_log_channels(gid)}

    def name(target_type: str, target_id: int) -> Optional[str]:
        if target_type == "user":
            user = cache.get_member(gid, target_id) or cache.get_user(target_id)
            return user.username if user else None
        channel = cache.get_guild_channel(target_id)
        return channel.name if channel else None

    return {
        "types": [
            {
                "type": t,
                "channel_id": str(c["channel_id"])
                if (c := configs.get(t)) and c["channel_id"]
                else None,
                "enabled": bool(c and c["is_enabled"] and c["channel_id"]),
            }
            for t in LOG_TYPES
        ],
        "ignores": [
            {
                "target_type": i["target_type"],
                "target_id": str(i["target_id"]),
                "log_type": i["log_type"],
                "name": name(i["target_type"], i["target_id"]),
            }
            for i in await repo.list_ignores(gid)
        ],
    }


@routes.get(f"{GUILD}/logging")
async def get_logging(request: web.Request) -> web.Response:
    return web.json_response(await _logging_json(request))


@routes.put(GUILD + r"/logging/{log_type}")
async def put_log_type(request: web.Request) -> web.Response:
    """Set a log type's channel and/or turn it on or off (/logging <type>-log, enable, disable)."""
    log_type = request.match_info["log_type"]
    if log_type not in LOG_TYPES:
        raise ApiError(404, "not_found", "No such log type.")
    data = await read_json(request)
    gid = guild_id(request)
    repo = request.app[LOG_REPO]
    old = await repo.get_log_channel(gid, log_type) or {"channel_id": None, "is_enabled": False}
    old_on = bool(old["is_enabled"] and old["channel_id"])
    label = f"{log_type.title()} log"

    if "channel_id" in data:
        channel = guild_channel(request, data["channel_id"])
        enabled = bool_field(data, "enabled") if "enabled" in data else True
        await repo.set_log_channel(gid, log_type, channel.id, enabled)
        old_channel = channel_label(request, old["channel_id"])
        _record(request, f"{label} channel", old_channel, f"#{channel.name}")
    elif "enabled" in data:
        enabled = bool_field(data, "enabled")
        if enabled and not old["channel_id"]:
            raise ApiError(409, "no_channel", f"Pick a channel for {label}s first.")
        await (repo.enable_log_channel if enabled else repo.disable_log_channel)(gid, log_type)
    else:
        raise ApiError(400, "bad_request", "Send channel_id and/or enabled.")

    if enabled != old_on:
        _record(request, label, _on(old_on), _on(enabled))
    return web.json_response(await _logging_json(request))


@routes.post(f"{GUILD}/logging/setup")
async def logging_setup(request: web.Request) -> web.Response:
    """One-click setup, same as /logging setup. Safe to run again."""
    bot = request.app[BOT]
    me = bot.get_me()
    try:
        channels, failed = await setup_log_channels(
            bot.rest,
            bot.cache,
            request.app[LOG_REPO],
            guild_id(request),
            me.id if me else None,
            tickets_enabled=await request.app[MODULES].is_enabled(
                guild_id(request), ModuleType.TICKETS
            ),
        )
    except hikari.ForbiddenError:
        raise ApiError(
            409, "missing_permissions", "Serenity needs Manage Channels to create log channels."
        ) from None
    _record(request, "Logging setup", "-", f"{len(channels)} log channels")
    return web.json_response({**await _logging_json(request), "failed": failed})


IGNORABLE_CHANNELS = TEXT_TYPES | {
    hikari.ChannelType.GUILD_VOICE,
    hikari.ChannelType.GUILD_STAGE,
    hikari.ChannelType.GUILD_FORUM,
}


def _ignore_target(
    request: web.Request, data: dict[str, Any], check_channel: bool = True
) -> tuple[str, int]:
    target_type, target_id = data.get("target_type"), str(data.get("target_id", ""))
    if target_type == "channel" and check_channel:
        return target_type, guild_channel(request, target_id, IGNORABLE_CHANNELS).id
    if target_type in ("user", "channel") and target_id.isdigit():
        return target_type, int(target_id)
    raise ApiError(422, "invalid", "target_type must be user or channel, with a target_id.")


def _scope_text(log_type: str) -> str:
    return "every log" if log_type == "*" else f"{log_type} logs"


def _mention(target_type: str, target_id: int) -> str:
    return f"<#{target_id}>" if target_type == "channel" else f"<@{target_id}>"


def _scope(value: Any) -> str:
    if value not in ("*", *LOG_TYPES):
        raise ApiError(422, "invalid", f"log_type must be * or one of {', '.join(LOG_TYPES)}.")
    return value


@routes.post(f"{GUILD}/logging/ignores")
async def add_ignore(request: web.Request) -> web.Response:
    """Like /logging ignore-*: "*" covers every log type and replaces per-type rows."""
    data = await read_json(request)
    target_type, target_id = _ignore_target(request, data)
    log_type = _scope(data.get("log_type", "*"))
    if await request.app[LOG_REPO].add_ignore(guild_id(request), target_type, target_id, log_type):
        setting = f"Log ignores for {_mention(target_type, target_id)}"
        _record(request, setting, "logged", f"ignored in {_scope_text(log_type)}")
    return web.json_response(await _logging_json(request))


@routes.delete(f"{GUILD}/logging/ignores")
async def remove_ignore(request: web.Request) -> web.Response:
    """Query: target_type, target_id, and log_type (one scope) or none (every scope)."""
    # No channel check: an ignore for a since-deleted channel must still be removable.
    target_type, target_id = _ignore_target(request, dict(request.query), check_channel=False)
    log_type = _scope(request.query["log_type"]) if "log_type" in request.query else None
    removed = await request.app[LOG_REPO].remove_ignore(
        guild_id(request), target_type, target_id, log_type
    )
    if removed:
        setting = f"Log ignores for {_mention(target_type, target_id)}"
        _record(request, setting, f"ignored in {_scope_text(log_type or '*')}", "logged")
    return web.json_response(await _logging_json(request))
