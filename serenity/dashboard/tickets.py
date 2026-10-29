"""Dashboard routes for tickets (milestone "Tickets in the dashboard"), and who may use them (#69).

Managers (Manage Server) can use every route. Ticket staff, members holding a type's staff role
or one of its tier roles, can use only `staff_routes`, and only see the tickets their roles reach
(TicketAccess.can_see). auth_middleware enforces the routes; handlers filter what they return with request[TICKET_ACCESS].
"""

from dataclasses import dataclass
from typing import Any, Optional

from aiohttp import web

from serenity.dashboard.auth import Membership, NotInstalledError, access_level
from serenity.dashboard.common import ACCESS, SESSION, TICKET_SVC, ApiError, guild_id
from serenity.services.ticket_service import current_tier

routes = web.RouteTableDef()  # managers only
staff_routes = web.RouteTableDef()  # managers and ticket staff
TICKETS = r"/guilds/{guild_id:\d+}/tickets"


@dataclass(frozen=True)
class TicketAccess:
    """What a user may see of a guild's tickets."""

    manager: bool = False  # everything
    staff_types: frozenset[int] = frozenset()  # holds the type's staff role: all its tickets
    tiers: frozenset[int] = frozenset()  # holds the tier's role: tickets currently with it
    types: frozenset[int] = frozenset()  # staff_types and the types of `tiers`

    @property
    def staff(self) -> bool:
        return bool(self.types)

    def can_see(self, ticket: dict, ticket_type: dict) -> bool:
        """`ticket_type` with its tiers, as TicketService.full_type returns it."""
        if self.manager or ticket["type_id"] in self.staff_types:
            return True
        if ticket["type_id"] not in self.types:
            return False
        tier = current_tier(ticket, ticket_type)
        return tier is not None and tier["id"] in self.tiers


TICKET_ACCESS = web.RequestKey("ticket_access", TicketAccess)


async def ticket_access(
    app: web.Application, guild_id: int, member: Optional[Membership]
) -> TicketAccess:
    """A member's TicketAccess, from their (cached) roles and the types' current staff roles."""
    if member is None:
        return TicketAccess()
    if access_level(member.permissions):
        return TicketAccess(manager=True)
    staff_types, tiers = set(), set()
    for row in await app[TICKET_SVC].repo.staff_roles(guild_id):
        if member.role_ids.intersection(row["staff_role_ids"]):
            staff_types.add(row["type_id"])
        if member.role_ids.intersection(row["tier_role_ids"]):
            tiers.add((row["tier_id"], row["type_id"]))
    return TicketAccess(
        staff_types=frozenset(staff_types),
        tiers=frozenset(t for t, _ in tiers),
        types=frozenset(staff_types | {ty for _, ty in tiers}),
    )


async def visible_types(request: web.Request) -> list[dict]:
    """The guild's ticket types the user can see any ticket of, without their tiers."""
    access = request[TICKET_ACCESS]
    types = await request.app[TICKET_SVC].repo.list_types(guild_id(request))
    return [t for t in types if access.manager or t["id"] in access.types]


async def ticket_or_404(request: web.Request) -> tuple[dict, dict]:
    """The ticket numbered {number} in the path and its full type, if the user can see it."""
    svc = request.app[TICKET_SVC]
    ticket = await svc.repo.get_ticket(guild_id(request), int(request.match_info["number"]))
    if ticket is not None:
        ticket_type = await svc.full_type(ticket)
        if request[TICKET_ACCESS].can_see(ticket, ticket_type):
            return ticket, ticket_type
    raise ApiError(404, "not_found", "No such ticket in this server.")  # same for hidden ones


async def transcript_viewer(request: web.Request, ticket: dict) -> Optional[str]:
    """How the user may see a ticket's transcript: "staff" (with notes), "opener" (without), or None.

    Needs no guild access, so a route outside /guilds can serve openers who left the server.
    """
    user_id = request[SESSION].user_id
    try:
        member = await request.app[ACCESS].member(user_id, ticket["guild_id"])
    except NotInstalledError:
        member = None
    access = await ticket_access(request.app, ticket["guild_id"], member)
    if access.manager or ticket["type_id"] in access.types:
        if access.can_see(ticket, await request.app[TICKET_SVC].full_type(ticket)):
            return "staff"
    return "opener" if ticket["opener_id"] == user_id else None


def _type_json(ticket_type: dict) -> dict[str, Any]:
    return {"id": ticket_type["id"], "name": ticket_type["name"], "emoji": ticket_type["emoji"]}


@staff_routes.get(f"{TICKETS}/types")
async def list_types(request: web.Request) -> web.Response:
    """The types the user can see tickets of, for pickers and facets."""
    return web.json_response([_type_json(t) for t in await visible_types(request)])


from serenity.dashboard import (  # noqa: E402, F401  # registers their routes
    tickets_actions,
    tickets_panels,
    tickets_queue,
    tickets_settings,
    tickets_stats,
    tickets_tiers_hours,
    tickets_types,
)
