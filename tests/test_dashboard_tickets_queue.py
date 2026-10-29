"""Dashboard ticket queue (#70): filters, search and what ticket staff see."""

from unittest.mock import MagicMock

import hikari
import pytest

from serenity.services.ticket_service import set_field
from tests import test_dashboard_api as base
from tests.test_dashboard_api import COOKIES, GUILD, SENIOR_ROLE, SUPPORT_ROLE, USER, make_bot

client_for, fake_permissions, ticket_guild = base.client_for, base.fake_permissions, base.ticket_guild


async def queue(client, query=""):
    resp = await client.get(f"/api/guilds/{GUILD}/tickets/queue?{query}", cookies=COOKIES)
    assert resp.status == 200, await resp.text()
    return await resp.json()


def stage(name, terminal=False):
    return {"name": name, "color": None, "terminal": terminal, "auto_close": True}


def numbers(body):
    return [t["number"] for t in body["tickets"]]


@pytest.mark.asyncio
async def test_queue_filters_and_searches(client_for, pg_repo, ticket_guild):
    repo = ticket_guild.repo
    t1, t2, t3 = ticket_guild.tickets
    fields = set_field([], "IGN", identity=True)
    appeals = await repo.create_type(GUILD, "Appeals", fields=fields, stages=[stage("New"), stage("Review")])
    a = await repo.create_ticket(GUILD, appeals["id"], USER + 5, {"ign": "Notch_2"})
    b = await repo.create_ticket(GUILD, appeals["id"], USER + 6, {"ign": "jeb_"})
    await repo.update_ticket(b["id"], stage="Review", priority="urgent", waiting_on="opener")
    await repo.claim(t3["id"], USER)
    await repo.close_ticket(t1["id"], USER, None)

    bot = make_bot()
    aria = MagicMock(id=USER + 6, username="aria_b", display_name="Aria")
    bot.cache.get_members_view_for_guild.return_value = {aria.id: aria}
    client = await client_for(bot, pg_repo)

    body = await queue(client)
    assert numbers(body) == [b["number"], 2, 3, a["number"]]  # urgent first, then oldest; #1 is closed
    assert body["total"] == 4
    assert body["facets"]["type"] == {str(ticket_guild.tickets[1]["type_id"]): 1, str(t3["type_id"]): 1, str(appeals["id"]): 2}
    assert body["facets"]["stage"] == {"New": 1, "Review": 1}
    assert body["facets"]["mine"] == 1 and body["facets"]["unclaimed"] == 3
    appeal = next(t for t in body["types"] if t["name"] == "Appeals")
    assert appeal["fields"][0]["key"] == "ign" and [s["name"] for s in appeal["stages"]] == ["New", "Review"]

    assert numbers(await queue(client, "q=notch")) == [a["number"]]  # a field value, any case
    assert numbers(await queue(client, "q=%233")) == [3]  # #3
    assert numbers(await queue(client, "q=aria")) == [b["number"]]  # the opener's name
    assert numbers(await queue(client, f"type={appeals['id']}&stage=New")) == [a["number"]]
    assert numbers(await queue(client, "status=opener")) == [b["number"]]
    assert numbers(await queue(client, "claim=mine")) == [3]
    assert numbers(await queue(client, f"tier={ticket_guild.seniors['id']}")) == [2]


@pytest.mark.asyncio
async def test_staff_queue_shows_only_their_tickets(client_for, pg_repo, ticket_guild):
    seniors = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SENIOR_ROLE]), pg_repo)
    body = await queue(seniors)
    assert numbers(body) == [2] and body["total"] == 1
    assert [t["name"] for t in body["types"]] == ["Support"]

    support = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    assert numbers(await queue(support)) == [1, 2]
    assert numbers(await queue(support, "q=3")) == []  # Billing's #3 stays hidden


@pytest.mark.asyncio
async def test_off_hours_badge(client_for, pg_repo, ticket_guild):
    closed_all_week = [[] for _ in range(7)]
    await ticket_guild.repo.update_type(GUILD, ticket_guild.tickets[2]["type_id"], schedule=closed_all_week)
    body = await queue(await client_for(make_bot(), pg_repo))
    assert {t["number"]: t["off_hours"] for t in body["tickets"]} == {1: False, 2: False, 3: True}
