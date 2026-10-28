"""The ticket queue (#70): open tickets the user can see, with facets and search.

GET {TICKETS}/queue  ?type=<id> &stage=<name> &tier=<id> &status=staff|opener
                     &claim=mine|unclaimed &q=<#number, opener name or id, or a field value>
"""

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from serenity.core.modules import ModuleType
from serenity.dashboard.common import MODULES, SESSION, TICKET_SVC, guild_id
from serenity.dashboard.moderation import _name_matches, _user
from serenity.dashboard.tickets import TICKET_ACCESS, TICKETS, staff_routes, visible_types
from serenity.services.ticket_hours import effective_schedule, within_hours
from serenity.services.ticket_service import PRIORITIES, current_tier, field_type


def type_json(ticket_type: dict) -> dict[str, Any]:
    """A type with what the queue and board need: its fields, stages and tiers."""
    return {
        "id": ticket_type["id"],
        "name": ticket_type["name"],
        "emoji": ticket_type["emoji"],
        "fields": [
            {
                "key": f["key"],
                "label": f["label"],
                "type": field_type(f),
                "required": f["required"],
                "staff_only": bool(f.get("staff_only")),
                "options": f.get("options", []),
            }
            for f in ticket_type["fields"]
        ],
        "stages": [
            {"name": s["name"], "color": s["color"], "terminal": s["terminal"]} for s in ticket_type["stages"]
        ],
        "tiers": [{"id": t["id"], "name": t["name"]} for t in ticket_type["tiers"]],
    }


def ticket_json(request: web.Request, ticket: dict, ticket_type: dict, settings: dict) -> dict[str, Any]:
    tier = current_tier(ticket, ticket_type)
    # ponytail: judged by the schedule as it is now, and away mode at opening isn't recorded.
    schedule = effective_schedule(ticket_type["schedule"], settings)
    opened = datetime.fromtimestamp(ticket["opened_at"], timezone.utc)
    return {
        "number": ticket["number"],
        "type_id": ticket["type_id"],
        "status": ticket["status"],
        "channel_id": str(ticket["channel_id"]) if ticket["channel_id"] else None,
        "opener": _user(request, ticket["opener_id"]),
        "assignee": _user(request, ticket["assignee_id"]) if ticket["assignee_id"] else None,
        "stage": ticket["stage"],
        "tier_id": tier["id"] if tier else None,
        "waiting_on": ticket["waiting_on"],
        "priority": ticket["priority"],
        "opened_at": ticket["opened_at"],
        "last_activity_at": ticket["last_activity_at"],
        "fields": ticket["fields"],
        "off_hours": not within_hours(schedule, settings.get("timezone", "UTC"), opened),
    }


async def full_types(request: web.Request) -> dict[int, dict]:
    """The visible types with their tiers, by id."""
    repo = request.app[TICKET_SVC].repo
    return {t["id"]: {**t, "tiers": await repo.list_tiers(t["id"])} for t in await visible_types(request)}


async def ticket_settings(request: web.Request) -> dict:
    # The app's ModuleManager, not the service's: same database, and tests can fake the service.
    return (await request.app[MODULES].get_module_config(guild_id(request), ModuleType.TICKETS)).settings


@staff_routes.get(f"{TICKETS}/queue")
async def queue(request: web.Request) -> web.Response:
    # ponytail: every open ticket is read and filtered here; fine for hundreds, SQL past that.
    q, access, me = request.query, request[TICKET_ACCESS], request[SESSION].user_id
    types = await full_types(request)
    visible = [
        t
        for t in await request.app[TICKET_SVC].repo.list_open(guild_id(request))
        if t["type_id"] in types and access.can_see(t, types[t["type_id"]])
    ]
    tiers = {t["id"]: (tier["id"] if (tier := current_tier(t, types[t["type_id"]])) else None) for t in visible}
    def count(values: Iterable[Any]) -> dict[str, int]:
        return {str(k): n for k, n in Counter(values).items()}

    facets = {
        "type": count(t["type_id"] for t in visible),
        "stage": count(t["stage"] for t in visible if t["stage"]),
        "tier": count(tiers[t["id"]] for t in visible if tiers[t["id"]]),
        "status": count(t["waiting_on"] for t in visible),
        "mine": sum(t["assignee_id"] == me for t in visible),
        "unclaimed": sum(t["assignee_id"] is None for t in visible),
    }

    text = q.get("q", "").strip().lstrip("#").lower()[:100]
    named = set(_name_matches(request, text)) if text else set()

    def keep(t: dict) -> bool:
        if q.get("type") and str(t["type_id"]) != q["type"]:
            return False
        if q.get("stage") and t["stage"] != q["stage"]:
            return False
        if q.get("tier") and str(tiers[t["id"]]) != q["tier"]:
            return False
        if q.get("status") and t["waiting_on"] != q["status"]:
            return False
        if q.get("claim") == "mine" and t["assignee_id"] != me:
            return False
        if q.get("claim") == "unclaimed" and t["assignee_id"] is not None:
            return False
        return not text or (
            str(t["number"]) == text
            or str(t["opener_id"]) == text
            or t["opener_id"] in named
            or any(text in str(v).lower() for v in t["fields"].values())
        )

    shown = sorted(filter(keep, visible), key=lambda t: (-PRIORITIES.index(t["priority"]), t["opened_at"]))
    settings = await ticket_settings(request)
    return web.json_response(
        {
            "tickets": [ticket_json(request, t, types[t["type_id"]], settings) for t in shown],
            "total": len(visible),
            "types": [type_json(t) for t in types.values()],
            "facets": facets,
        }
    )
