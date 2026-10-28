"""Dashboard ticket stats (#79): the numbers, the credit chain and who sees what."""

import time

import hikari
import pytest

from serenity.database.moderation_repository import ModerationRepository
from tests import test_dashboard_api as base
from tests.test_dashboard_api import COOKIES, GUILD, SUPPORT_ROLE, USER, make_bot

client_for, fake_permissions, ticket_guild = base.client_for, base.fake_permissions, base.ticket_guild

HELPER, ASSIST, SENIOR = USER, USER + 10, USER + 11


async def stats(client, query=""):
    resp = await client.get(f"/api/guilds/{GUILD}/tickets/stats?{query}", cookies=COOKIES)
    assert resp.status == 200, await resp.text()
    return await resp.json()


async def escalated_ticket(repo, g, *, assist: bool, senior_replies: bool, rating: int) -> dict:
    """Claimed by HELPER, escalated to Seniors, taken by SENIOR, closed and rated."""
    t = await repo.create_ticket(GUILD, g.helpers["type_id"], USER + 1, {})
    await repo.claim(t["id"], HELPER)
    await repo.add_event(t["id"], "claimed", HELPER, {"assignee": HELPER})
    await repo.record_message(t["id"], HELPER, staff=True)
    if assist:
        await repo.record_message(t["id"], ASSIST, staff=True)
    await repo.update_ticket(t["id"], tier_id=g.seniors["id"])
    await repo.add_event(t["id"], "escalated", HELPER, {"from": g.helpers["id"], "to": g.seniors["id"]})
    await repo.claim(t["id"], SENIOR, force=True)
    await repo.add_event(t["id"], "claimed", SENIOR, {"assignee": SENIOR})
    if senior_replies:
        await repo.record_message(t["id"], SENIOR, staff=True)
    await repo.close_ticket(t["id"], SENIOR, None)
    await repo.add_rating(t["id"], USER + 1, rating, None)
    return t


@pytest.mark.asyncio
async def test_handled_counts_match_the_credit_chain(client_for, pg_repo, ticket_guild):
    repo, g = ticket_guild.repo, ticket_guild
    a = await escalated_ticket(repo, g, assist=False, senior_replies=True, rating=5)
    b = await escalated_ticket(repo, g, assist=True, senior_replies=False, rating=2)

    body = await stats(await client_for(make_bot(), pg_repo))
    staff = {int(p["user"]["id"]): p for p in body["staff"]}

    credited = await repo.pool.fetch(
        "SELECT user_id, COUNT(*) AS n FROM ticket_handlers WHERE credited AND ticket_id = ANY($1) GROUP BY user_id",
        [a["id"], b["id"]],
    )
    assert {r["user_id"]: r["n"] for r in credited} == {u: p["handled"] for u, p in staff.items() if p["handled"]}
    assert {u: p["handled"] for u, p in staff.items()} == {HELPER: 2, ASSIST: 1, SENIOR: 1}
    assert staff[HELPER] | {"user": None} == {
        "user": None, "claimed": 2, "handled": 2, "assisted": 0, "escalated_from": 2, "csat": {"avg": 3.5, "count": 2}
    }
    assert staff[ASSIST]["assisted"] == 1 and staff[ASSIST]["claimed"] == 0
    assert staff[SENIOR]["claimed"] == 2 and staff[SENIOR]["csat"] == {"avg": 5.0, "count": 1}  # b didn't credit them

    assert body["closed"] == 2 and body["escalated"] == 2
    assert body["csat"] == {"avg": 3.5, "count": 2, "distribution": [0, 1, 0, 0, 1], "response_rate": 1.0}
    seniors = next(t for t in body["tiers"] if t["name"] == "Seniors")
    assert seniors["csat"]["count"] == 2 and seniors["type"] == "Support"


@pytest.mark.asyncio
async def test_times_stages_and_auto_close(client_for, pg_repo, ticket_guild):
    repo = ticket_guild.repo
    stages = [{"name": n, "color": None, "terminal": False, "auto_close": True} for n in ("New", "Quoted", "Paid")]
    work = await repo.create_type(GUILD, "Work", stages=stages)
    now = int(time.time())
    a = await repo.create_ticket(GUILD, work["id"], USER + 1, {})
    b = await repo.create_ticket(GUILD, work["id"], USER + 2, {})
    await repo.pool.execute(
        "UPDATE tickets SET stage = 'New', opened_at = $2, first_response_at = $3 WHERE id = $1",
        a["id"], now - 3000, now - 2400,
    )
    await repo.pool.execute("UPDATE tickets SET stage = 'New', opened_at = $2 WHERE id = $1", b["id"], now - 1000)
    await repo.add_event(a["id"], "stage", HELPER, {"from": "New", "to": "Quoted"})
    await repo.pool.execute("UPDATE ticket_events SET created_at = $2 WHERE ticket_id = $1", a["id"], now - 2000)
    await repo.close_ticket(a["id"], HELPER, None)
    await repo.pool.execute("UPDATE tickets SET closed_at = $2 WHERE id = $1", a["id"], now - 1500)
    await repo.add_event(a["id"], "auto_closed", None)

    body = await stats(await client_for(make_bot(), pg_repo))
    ty = next(t for t in body["types"] if t["name"] == "Work")
    by_stage = {s["name"]: s for s in ty["stages"]}
    assert by_stage["New"]["reached"] == 2 and by_stage["Quoted"]["reached"] == 1 and by_stage["Paid"]["reached"] == 0
    assert by_stage["Quoted"]["avg_seconds"] == 500  # until it closed
    assert 1000 <= by_stage["New"]["avg_seconds"] <= 1001  # a: 1000s; b: still there, 1000s so far
    assert ty["first_response"]["raw"] == {"avg": 600, "median": 600}
    assert ty["first_response"]["business"] == {"avg": 600, "median": 600}  # no schedule: every hour counts
    assert ty["time_to_close"]["avg"] == 1500
    assert ty["auto_closed"] == 1 and ty["closed"] == 1 and ty["open"] == 1
    assert sum(d["opened"] for d in body["daily"]) == body["opened"] == 5  # with the fixture's three

    # Support hours: closed all week means none of the wait counted.
    await repo.update_type(GUILD, work["id"], schedule=[[] for _ in range(7)])
    body = await stats(await client_for(make_bot(), pg_repo))
    assert next(t for t in body["types"] if t["name"] == "Work")["first_response"]["business"]["avg"] == 0


@pytest.mark.asyncio
async def test_staff_see_their_types_and_their_own_row(client_for, pg_repo, ticket_guild):
    repo, (t1, _, t3) = ticket_guild.repo, ticket_guild.tickets
    for t, user in ((t1, USER), (t3, USER + 5)):
        await repo.claim(t["id"], user)
        await repo.add_event(t["id"], "claimed", user, {"assignee": user})

    staff = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    body = await stats(staff)
    assert [t["name"] for t in body["types"]] == ["Support"]
    assert body["open"] == 2  # Billing's #3 isn't counted
    assert [p["user"]["id"] for p in body["staff"]] == [str(USER)]

    manager = await stats(await client_for(make_bot(), pg_repo))
    assert manager["open"] == 3 and len(manager["staff"]) == 2

    resp = await staff.get(f"/api/guilds/{GUILD}/tickets/stats?days=12", cookies=COOKIES)
    assert resp.status == 422


@pytest.mark.asyncio
async def test_cases_link_the_ticket_they_came_from(client_for, pg_repo, ticket_guild):
    mod = ModerationRepository(pg_repo.pool)
    linked = await mod.create_case(GUILD, USER + 1, USER, "warn", "from a ticket")
    await mod.create_case(GUILD, USER + 1, USER, "warn", "not")
    await mod.set_case_source(GUILD, linked, ticket_guild.tickets[1]["id"])

    client = await client_for(make_bot(), pg_repo)
    body = await (await client.get(f"/api/guilds/{GUILD}/cases", cookies=COOKIES)).json()
    assert [c["ticket"] for c in body["cases"]] == [None, 2]
    detail = await (await client.get(f"/api/guilds/{GUILD}/cases/{linked}", cookies=COOKIES)).json()
    assert detail["ticket"] == 2
