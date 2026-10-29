"""Ticket stats (#79): volume, response and close times, stages, ratings and who did the work.

GET {TICKETS}/stats?days=7|30|90

Scoped to the types the user can see; ticket staff see only their own row of the staff table.
First response is reported raw and in support hours (the type's schedule, else the server's).
"""

import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from aiohttp import web

from serenity.dashboard.common import SESSION, TICKET_SVC, ApiError, guild_id
from serenity.dashboard.moderation import STATS_DAYS, _user
from serenity.dashboard.tickets import TICKET_ACCESS, TICKETS, _type_json, staff_routes
from serenity.dashboard.tickets_queue import full_types, ticket_settings
from serenity.services.ticket_hours import business_seconds_between, effective_schedule


def _times(values: list[float]) -> Optional[dict[str, float]]:
    """Average and median seconds, or None without any."""
    return {"avg": statistics.fmean(values), "median": statistics.median(values)} if values else None


def _csat(ratings: list[int], asked: int) -> dict[str, Any]:
    counts = Counter(ratings)
    return {
        "avg": statistics.fmean(ratings) if ratings else None,
        "count": len(ratings),
        "distribution": [counts[i] for i in range(1, 6)],  # 1 to 5 stars
        "response_rate": len(ratings) / asked if asked else None,
    }


def _summary(rows: list[dict], since: int, types: dict[int, dict]) -> dict[str, Any]:
    """Counts, times and ratings for a set of the repository's stats ticket rows."""
    opened = [t for t in rows if t["opened_at"] >= since]
    closed = [t for t in rows if t["closed_at"] and t["closed_at"] >= since and t["status"] == "closed"]
    answered = [t for t in opened if t["first_response_at"]]
    open_now = [t for t in rows if t["status"] == "open"]
    return {
        "open": len(open_now),
        "unclaimed": sum(t["assignee_id"] is None for t in open_now),
        "waiting_on_staff": sum(t["waiting_on"] == "staff" for t in open_now),
        "opened": len(opened),
        "closed": len(closed),
        "auto_closed": sum(t["auto_closed"] for t in closed),
        "escalated": sum(t["escalated"] for t in opened),
        "first_response": {
            "raw": _times([t["first_response_at"] - t["opened_at"] for t in answered]),
            "business": _times([t["business_response"] for t in answered]),
        },
        "time_to_close": _times([t["closed_at"] - t["opened_at"] for t in closed]),
        "csat": _csat(
            [t["rating"] for t in closed if t["rating"] is not None],
            sum(types[t["type_id"]]["ratings"] for t in closed),
        ),
    }


@staff_routes.get(f"{TICKETS}/stats")
async def stats(request: web.Request) -> web.Response:
    days = int(request.query.get("days", "30")) if request.query.get("days", "").isdigit() else 30
    if days not in STATS_DAYS:
        raise ApiError(422, "invalid", f"days must be one of {', '.join(map(str, STATS_DAYS))}.")
    access, me = request[TICKET_ACCESS], request[SESSION].user_id
    types = await full_types(request)
    now = int(time.time())
    today = now // 86400
    since = (today - days) * 86400  # whole UTC days, as Mod stats
    # ponytail: every ticket in range is read for the support-hours maths; fine for thousands.
    data = await request.app[TICKET_SVC].repo.stats(
        guild_id(request), None if access.manager else sorted(types), since, now
    )
    # Types deleted since don't show; tier-only staff count just the tickets with their tier.
    rows = [t for t in data["tickets"] if t["type_id"] in types and access.can_see(t, types[t["type_id"]])]
    settings = await ticket_settings(request)
    tz = settings.get("timezone", "UTC")
    for t in rows:  # once per ticket: both the totals and the per-type summary read it
        if t["first_response_at"] and t["opened_at"] >= since:
            t["business_response"] = business_seconds_between(
                effective_schedule(types[t["type_id"]]["schedule"], settings),
                tz,
                datetime.fromtimestamp(t["opened_at"], timezone.utc),
                datetime.fromtimestamp(t["first_response_at"], timezone.utc),
            )

    daily: dict[int, Counter] = defaultdict(Counter)
    for t in rows:
        if t["opened_at"] >= since:
            daily[t["opened_at"] // 86400]["opened"] += 1
        if t["status"] == "closed" and t["closed_at"] >= since:
            daily[t["closed_at"] // 86400]["closed"] += 1

    stages: dict[int, dict[str, dict]] = defaultdict(dict)
    for s in data["stages"]:  # counted in SQL over every tier, so only for the type's own staff
        if access.manager or s["type_id"] in access.staff_types:
            stages[s["type_id"]][s["stage"]] = s

    by_tier: dict[Optional[int], list[dict]] = defaultdict(list)
    for t in rows:
        if t["status"] == "closed" and t["closed_at"] >= since:
            by_tier[t["tier_id"] or (types[t["type_id"]]["tiers"] or [{"id": None}])[0]["id"]].append(t)

    people: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"claimed": 0, "handled": 0, "assisted": 0, "escalated_from": 0, "csat": None}
    )
    for row in data["staff"]:
        people[row["user_id"]][row["kind"]] = row["n"]
    for row in data["csat"]:
        people[row["user_id"]]["csat"] = {"avg": row["avg"], "count": row["n"]}

    by_type: dict[int, list[dict]] = defaultdict(list)
    for t in rows:
        by_type[t["type_id"]].append(t)

    def type_stats(ty: dict) -> dict[str, Any]:
        summary = _summary(by_type[ty["id"]], since, types)
        return {
            **_type_json(ty),
            **summary,
            # Reached: tickets opened in range that were at the stage at some point.
            "stages": [
                {
                    "name": s["name"],
                    "reached": stages[ty["id"]].get(s["name"], {}).get("reached", 0),
                    "avg_seconds": stages[ty["id"]].get(s["name"], {}).get("avg_seconds"),
                }
                for s in ty["stages"]
                if access.manager or ty["id"] in access.staff_types  # tier-only staff: none, so it's hidden
            ],
        }

    return web.json_response(
        {
            "days": days,
            **_summary(rows, since, types),
            # One entry per UTC day, oldest first.
            "daily": [
                {"day": (today - i) * 86400, **{k: daily[today - i][k] for k in ("opened", "closed")}}
                for i in range(days, -1, -1)
            ],
            "types": [type_stats(ty) for ty in types.values()],
            "tiers": [
                {
                    "id": tier["id"],
                    "name": tier["name"],
                    "type": ty["name"],
                    "csat": _csat(
                        [t["rating"] for t in by_tier[tier["id"]] if t["rating"] is not None],
                        sum(ty["ratings"] for _ in by_tier[tier["id"]]),
                    ),
                }
                for ty in types.values()
                for tier in ty["tiers"]
                if access.manager or ty["id"] in access.staff_types or tier["id"] in access.tiers
            ],
            "staff": sorted(
                (
                    {"user": _user(request, user_id), **counts}
                    for user_id, counts in people.items()
                    if access.manager or user_id == me
                ),
                key=lambda p: (-p["handled"], -p["claimed"]),
            ),
        }
    )
