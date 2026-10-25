"""Dashboard routes for moderation cases (#47) and stats (#48).

Reason edits and clearing warnings make the same database writes as /reason and /clearwarns.
"""

import time
from collections import defaultdict
from typing import Any, Optional

import hikari
from aiohttp import web

from serenity.dashboard.common import (
    BOT,
    CHANGES,
    MOD_REPO,
    PERMISSIONS,
    SESSION,
    ApiError,
    guild_id,
    read_json,
)

routes = web.RouteTableDef()
GUILD = r"/guilds/{guild_id:\d+}"

PAGE_SIZE = 50
REASON_MAX = 512  # what the mod-log embed shows
STATS_DAYS = (7, 30, 90)


def _user(request: web.Request, user_id: int) -> dict[str, Any]:
    """Name and avatar from the gateway cache; the dashboard shows the id when it's unknown."""
    cache = request.app[BOT].cache
    user = cache.get_member(guild_id(request), user_id) or cache.get_user(user_id)
    return {
        "id": str(user_id),
        "name": user.username if user else None,
        "avatar_url": str(user.display_avatar_url) if user else None,
    }


def _status(case: dict[str, Any], now: int) -> Optional[bool]:
    """Active/inactive for warnings and timeouts (as /case shows it); None for the rest."""
    if case["action"] == "timeout":
        return bool(case["is_active"]) and (case["expires_at"] or 0) > now
    if case["action"] == "warn":
        return bool(case["is_active"])
    return None


def _case_json(request: web.Request, case: dict[str, Any], now: int) -> dict[str, Any]:
    return {
        "number": case["case_number"],
        "action": case["action"],
        "target": _user(request, case["target_user_id"]),
        "moderator": _user(request, case["moderator_id"]),
        "reason": case["reason"],
        "duration": case["duration_sections"],
        "created_at": case["created_at"],
        "expires_at": case["expires_at"],
        "active": _status(case, now),
    }


def _ids(value: str) -> list[int]:
    return [int(v) for v in value.split(",") if v.isdigit()]


def _name_matches(request: web.Request, text: str) -> list[int]:
    """Members whose name contains `text`, so searching "kiri" finds kiri_nyx's cases."""
    text = text.lower()
    members = request.app[BOT].cache.get_members_view_for_guild(guild_id(request)).values()
    return [m.id for m in members if text in m.username.lower() or text in m.display_name.lower()][
        :100
    ]


@routes.get(f"{GUILD}/cases")
async def list_cases(request: web.Request) -> web.Response:
    """Query: action, moderator, user (comma lists), active=1, q (search), before (paging)."""
    q = request.query
    text = q.get("q", "").strip().lstrip("#")[:100]
    before = q.get("before", "")
    cases, total = await request.app[MOD_REPO].search_cases(
        guild_id(request),
        actions=[a for a in q.get("action", "").split(",") if a],
        moderators=_ids(q.get("moderator", "")),
        users=_ids(q.get("user", "")),
        active=q.get("active") == "1",
        text=text,
        text_user_ids=_name_matches(request, text) if text else [],
        before=int(before) if before.isdigit() else None,
        limit=PAGE_SIZE,
    )
    facets = await request.app[MOD_REPO].case_facets(guild_id(request))
    now = int(time.time())
    return web.json_response(
        {
            "cases": [_case_json(request, c, now) for c in cases],
            "total": total,
            "all": sum(facets["action"].values()),
            "next": cases[-1]["case_number"] if len(cases) == PAGE_SIZE else None,
            "facets": {
                "action": facets["action"],
                "moderator": [
                    {**_user(request, m), "count": n} for m, n in facets["moderator"].items()
                ],
            },
        }
    )


async def _case_or_404(request: web.Request) -> dict[str, Any]:
    case = await request.app[MOD_REPO].get_case(
        guild_id(request), int(request.match_info["number"])
    )
    if case is None:
        raise ApiError(404, "not_found", "No such case in this server.")
    return case


async def _case_detail(request: web.Request, case: dict[str, Any]) -> dict[str, Any]:
    repo, gid, now = request.app[MOD_REPO], guild_id(request), int(time.time())
    history = await repo.get_user_cases(gid, case["target_user_id"], limit=50)
    return {
        **_case_json(request, case, now),
        "history": [_case_json(request, c, now) for c in history],
        "warnings": await repo.get_warning_count(gid, case["target_user_id"]),
    }


@routes.get(GUILD + r"/cases/{number:\d+}")
async def get_case(request: web.Request) -> web.Response:
    """A case with the target's history and active warning count."""
    return web.json_response(await _case_detail(request, await _case_or_404(request)))


@routes.patch(GUILD + r"/cases/{number:\d+}")
async def edit_case(request: web.Request) -> web.Response:
    """Edit the reason, as /reason does."""
    case = await _case_or_404(request)
    reason = (await read_json(request)).get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > REASON_MAX:
        raise ApiError(422, "invalid", f"The reason must be 1 to {REASON_MAX} characters.")
    reason = reason.strip()
    await request.app[MOD_REPO].update_case_reason(guild_id(request), case["case_number"], reason)
    request.app[CHANGES].record(
        guild_id(request),
        request[SESSION].user_id,
        f"Case #{case['case_number']} reason",
        case["reason"] or "No reason provided",
        reason,
    )
    return web.json_response(await _case_detail(request, {**case, "reason": reason}))


@routes.post(GUILD + r"/users/{user_id:\d+}/clear-warnings")
async def clear_warnings(request: web.Request) -> web.Response:
    """Clear a user's active warnings, as /clearwarns does (which needs Moderate Members)."""
    if not request[PERMISSIONS] & hikari.Permissions.MODERATE_MEMBERS:
        raise ApiError(403, "forbidden", "Clearing warnings needs Moderate Members.")
    user_id = int(request.match_info["user_id"])
    cleared = await request.app[MOD_REPO].clear_warnings(guild_id(request), user_id)
    if cleared:
        request.app[CHANGES].record(
            guild_id(request),
            request[SESSION].user_id,
            f"Warnings for <@{user_id}>",
            f"{cleared} active",
            "cleared",
        )
    return web.json_response({"cleared": cleared})


@routes.get(f"{GUILD}/modstats")
async def modstats(request: web.Request) -> web.Response:
    """?days=7|30|90. `totals` is exactly what /modstats reports for the same range."""
    days = int(request.query.get("days", "30")) if request.query.get("days", "").isdigit() else 30
    if days not in STATS_DAYS:
        raise ApiError(422, "invalid", f"days must be one of {', '.join(map(str, STATS_DAYS))}.")
    repo, gid = request.app[MOD_REPO], guild_id(request)

    today = int(time.time()) // 86400
    daily: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    moderators: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in await repo.case_breakdown(gid, days):
        daily[row["day"]][row["action"]] += row["n"]
        moderators[row["moderator_id"]][row["action"]] += row["n"]

    return web.json_response(
        {
            "days": days,
            "totals": await repo.get_moderation_stats(gid, days=days),
            "active": await repo.count_active(gid),
            # One entry per UTC day, oldest first, including the partial day `days` ago.
            "daily": [
                {"day": (today - i) * 86400, "counts": daily.get(today - i, {})}
                for i in range(days, -1, -1)
            ],
            "moderators": sorted(
                ({**_user(request, m), "counts": c} for m, c in moderators.items()),
                key=lambda m: -sum(m["counts"].values()),
            ),
        }
    )
