"""Transcripts in the dashboard (#78): the staff list and the opener/staff viewer."""
# ruff: noqa: F811  (pytest fixtures imported from test_dashboard_api, used as arguments)

from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest
import pytest_asyncio

from serenity.core.modules import ModuleManager, ModuleType
from serenity.dashboard.api import NEXT_COOKIE, OAUTH, SESSIONS
from serenity.dashboard.common import BOT, TICKET_SVC
from tests.test_dashboard_api import (  # noqa: F401  (fixtures)
    BILLING_ROLE,
    COOKIES,
    GUILD,
    HEADERS,
    SENIOR_ROLE,
    SUPPORT_ROLE,
    USER,
    client_for,
    fake_permissions,
    make_bot,
    ticket_guild,
)

NOBODY = dict(perms=hikari.Permissions.NONE)
HANDLER = 900


def document(ticket):
    return {
        "ticket": {"number": ticket["number"], "type": "Support", "opener_id": ticket["opener_id"],
                   "opened_at": 100, "closed_at": 200, "closed_by": HANDLER, "reason": "sorted"},
        "fields": {"Order": "A-1"},
        "staff_fields": {"Risk": "high"},
        "messages": [{
            "id": 2**60 + 1, "content": "hi", "timestamp": 150, "edited": False, "embeds": [],
            "attachments": [],
            "author": {"id": 2**60 + 2, "name": "alice", "avatar": "", "color": 0xFF0000, "bot": False},
        }],
        "events": [
            {"kind": "opened", "actor_id": ticket["opener_id"], "data": {}, "created_at": 100},
            {"kind": "escalated", "actor_id": HANDLER, "data": {"reason": "rude"}, "created_at": 160},
        ],
        "notes": [{"author_id": HANDLER, "body": "watch this one", "created_at": 170}],
    }


@pytest_asyncio.fixture
async def closed(ticket_guild, pg_repo):
    """ticket_guild's three tickets, closed with transcripts: #1 by its opener, #2 auto-closed,
    #3 by HANDLER, who handled it; #2 is held for case 7."""
    repo = ticket_guild.repo
    t1, t2, t3 = ticket_guild.tickets
    await repo.record_message(t3["id"], HANDLER, staff=True)
    for t, by in ((t1, t1["opener_id"]), (t2, HANDLER), (t3, HANDLER)):
        t.update(await repo.close_ticket(t["id"], by, None))
        await repo.save_transcript(t["id"], GUILD, document(t), t["closed_at"] + 30 * 86400)
    await repo.add_event(t2["id"], "auto_closed", None)
    await repo.hold_transcript(t2["id"], 7)
    await ModuleManager(pg_repo).enable_module(GUILD, ModuleType.TICKETS)
    return ticket_guild


@pytest.fixture
def client(client_for, pg_repo):
    async def make(**bot):
        c = await client_for(make_bot(**bot), pg_repo)
        c.api[TICKET_SVC].modules = ModuleManager(pg_repo)  # for the rating checks
        cache = c.api[BOT].cache
        installed, guild = cache.get_guild.side_effect, MagicMock(icon_hash=None)
        guild.name = "Main"
        cache.get_user.return_value = None
        cache.get_guild.side_effect = lambda gid: guild if installed(gid) else None
        return c

    return make


async def listed(client, **params):
    resp = await client.get(f"/api/guilds/{GUILD}/tickets/transcripts", params=params, cookies=COOKIES)
    assert resp.status == 200
    return [t["number"] for t in (await resp.json())["transcripts"]]


@pytest.mark.asyncio
async def test_list_filters_and_shows_only_visible_types(client, closed):
    t1, t2, t3 = closed.tickets
    manager = await client()
    assert await listed(manager) == [3, 2, 1]
    assert await listed(manager, type=str(t3["type_id"])) == [3]
    assert await listed(manager, opener=str(USER)) == [3]
    assert await listed(manager, staff=str(HANDLER)) == [3]
    assert await listed(manager, outcome="opener") == [1]
    assert await listed(manager, outcome="auto") == [2]
    assert await listed(manager, outcome="staff") == [3]
    assert await listed(manager, since=str(t1["closed_at"] + 86400)) == []
    assert await listed(manager, before="3") == [2, 1]

    resp = await manager.get(f"/api/guilds/{GUILD}/tickets/transcripts", cookies=COOKIES)
    rows = {t["number"]: t for t in (await resp.json())["transcripts"]}
    assert rows[2]["held_by_case"] == 7 and rows[2]["outcome"] == "auto"
    assert rows[3]["staff"][0]["id"] == str(HANDLER) and rows[3]["expires_at"] == t3["closed_at"] + 30 * 86400

    # Staff see their types, tier roles only the tickets with their tier.
    assert await listed(await client(**NOBODY, roles=[BILLING_ROLE])) == [3]
    assert await listed(await client(**NOBODY, roles=[BILLING_ROLE]), type=str(t1["type_id"])) == []
    assert await listed(await client(**NOBODY, roles=[SENIOR_ROLE])) == [2]
    assert await listed(await client(**NOBODY, roles=[SUPPORT_ROLE])) == [2, 1]


async def view(client, ticket, **kwargs):
    resp = await client.get(f"/api/transcripts/{ticket['id']}", **kwargs)
    return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_opener_views_without_notes_after_leaving_and_others_get_404(client, closed, monkeypatch):
    t1, _, t3 = closed.tickets  # USER opened #3 only
    gone = await client(member=False)  # USER left the server
    status, body = await view(gone, t3, cookies=COOKIES)
    assert status == 200 and body["viewer"] == "opener" and body["is_opener"]
    assert body["fields"] == {"Order": "A-1"} and body["staff_fields"] == {} and body["notes"] == []
    assert [e["kind"] for e in body["events"]] == ["opened"]  # not the escalation's reason
    assert body["messages"][0]["id"] == str(2**60 + 1)  # snowflakes as strings
    assert body["can_rate"] and not body["can_delete"]

    # Someone else's transcript: the same 404 as one that doesn't exist.
    stranger = await client(**NOBODY)
    assert (await view(stranger, t1, cookies=COOKIES))[0] == 404
    assert (await stranger.get("/api/transcripts/999999", cookies=COOKIES)).status == 404
    assert (await view(stranger, t3))[0] == 401  # no public links

    # Staff of the type see notes and staff-only answers; managers may delete.
    status, body = await view(await client(**NOBODY, roles=[SUPPORT_ROLE]), t1, cookies=COOKIES)
    assert body["viewer"] == "staff" and body["notes"][0]["body"] == "watch this one"
    assert body["staff_fields"] == {"Risk": "high"} and len(body["events"]) == 2
    assert not body["can_delete"] and not body["can_rate"]
    assert (await view(await client(), t1, cookies=COOKIES))[1]["can_delete"]

    monkeypatch.setenv("TICKETS_GUILDS", "")
    assert (await view(gone, t3, cookies=COOKIES))[0] == 404


@pytest.mark.asyncio
async def test_delete_needs_manage_server_and_respects_evidence_holds(client, closed):
    t1, t2, t3 = closed.tickets
    opener = await client(**NOBODY)
    assert (await opener.delete(f"/api/transcripts/{t3['id']}", cookies=COOKIES, headers=HEADERS)).status == 403
    staff = await client(**NOBODY, roles=[SUPPORT_ROLE])
    assert (await staff.delete(f"/api/transcripts/{t1['id']}", cookies=COOKIES, headers=HEADERS)).status == 403

    manager = await client()
    resp = await manager.delete(f"/api/transcripts/{t2['id']}", cookies=COOKIES, headers=HEADERS)
    assert resp.status == 409 and "case #7" in (await resp.json())["error"]["message"]
    resp = await manager.delete(f"/api/transcripts/{t1['id']}", cookies=COOKIES, headers=HEADERS)
    assert resp.status == 204
    assert await closed.repo.get_transcript(t1["id"]) is None
    assert (await view(manager, t1, cookies=COOKIES))[0] == 404


@pytest.mark.asyncio
async def test_opener_asks_for_deletion_and_rates_once(client, closed):
    t1, _, t3 = closed.tickets
    opener = await client(member=False)
    path = f"/api/transcripts/{t3['id']}"
    assert (await opener.post(f"{path}/deletion-request", cookies=COOKIES, headers=HEADERS)).status == 204
    assert (await opener.post(f"{path}/deletion-request", cookies=COOKIES, headers=HEADERS)).status == 409
    assert (await closed.repo.get_transcript(t3["id"]))["deletion_requested_at"]
    other = f"/api/transcripts/{t1['id']}/deletion-request"
    assert (await opener.post(other, cookies=COOKIES, headers=HEADERS)).status == 404

    async def rate(body):
        resp = await opener.post(f"{path}/rating", json=body, cookies=COOKIES, headers=HEADERS)
        return resp.status, await resp.json()

    assert (await rate({"rating": 6}))[0] == 422
    assert await rate({"rating": 4, "comment": " quick "}) == (200, {"rating": 4, "comment": "quick"})
    status, body = await rate({"rating": 5})
    assert status == 422 and "already rated" in body["error"]["message"]
    _, body = await view(opener, t3, cookies=COOKIES)
    assert body["rating"] == {"rating": 4, "comment": "quick"} and not body["can_rate"]


@pytest.mark.asyncio
async def test_login_comes_back_to_the_transcript(client_for):
    client = await client_for(make_bot())
    client.api[SESSIONS].create = AsyncMock(return_value="tok")
    client.api[OAUTH].exchange = AsyncMock(return_value={"access_token": "a"})
    client.api[OAUTH].get = AsyncMock(return_value={"id": str(USER)})

    async def login(next_path):
        await client.get("/api/auth/login", params={"next": next_path}, allow_redirects=False)
        state = client.session.cookie_jar.filter_cookies(client.make_url("/"))["serenity_oauth_state"]
        resp = await client.get(f"/api/auth/callback?code=c&state={state.value}", allow_redirects=False)
        return resp.headers["Location"]

    assert (await login("/transcripts/12")).endswith("/transcripts/12")
    assert NEXT_COOKIE not in client.session.cookie_jar.filter_cookies(client.make_url("/"))
    assert (await login("//evil.example")).endswith("/servers")
    assert (await login("https://evil.example")).endswith("/servers")
    assert (await login("/servers/../x")).endswith("/servers")  # only transcript pages come back
