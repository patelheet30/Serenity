"""Dashboard: tier ladders and support hours (#76); the off-hours preview is the bot's message."""

from datetime import datetime, timedelta, timezone

import hikari
import pytest
import pytest_asyncio

from serenity.dashboard.common import TICKET_SVC
from serenity.services.ticket_hours import DAYS, next_closed, parse_schedule
from tests.test_dashboard_api import (  # noqa: F401  (fixtures)
    COOKIES,
    GUILD,
    HEADERS,
    OTHER_ROLE,
    SENIOR_ROLE,
    SUPPORT_ROLE,
    USER,
    client_for,
    fake_permissions,
    make_bot,
    ticket_guild,
)


@pytest_asyncio.fixture
async def tickets(client_for, pg_repo, ticket_guild):  # noqa: F811
    """A manager's API client: call(method, path, body) -> (status, json); .svc is the bot's."""
    bot = make_bot()
    roles = {SUPPORT_ROLE: object(), SENIOR_ROLE: object(), OTHER_ROLE: object()}
    bot.cache.get_roles_view_for_guild.return_value = roles
    client = await client_for(bot, pg_repo)
    svc = client.api[TICKET_SVC]

    async def call(method, path, body=None):
        resp = await client.request(
            method, f"/api/guilds/{GUILD}/tickets{path}", json=body, cookies=COOKIES, headers=HEADERS
        )
        return resp.status, await resp.json()

    call.svc, call.guild = svc, ticket_guild
    call.support = await svc.repo.get_type(GUILD, ticket_guild.tickets[0]["type_id"])
    return call


def ladder(body, name="Support"):
    return next(t["tiers"] for t in body if t["name"] == name)


@pytest.mark.asyncio
async def test_tier_ladder(tickets):
    svc, support = tickets.svc, tickets.support
    status, body = await tickets("GET", "/tiers")
    assert status == 200
    assert [t["name"] for t in ladder(body)] == ["Helpers", "Seniors"] and ladder(body, "Billing") == []

    path = f"/types/{support['id']}/tiers"
    assert (await tickets("POST", path, {"name": "Leads"}))[1]["error"]["message"] == "A new tier needs a role."
    assert (await tickets("POST", path, {"name": "seniors", "role_ids": [str(OTHER_ROLE)]}))[0] == 409
    assert (await tickets("POST", path, {"name": "Leads", "role_ids": ["42"]}))[0] == 422
    status, body = await tickets("POST", path, {"name": "Leads", "role_ids": [str(OTHER_ROLE)]})
    assert status == 200
    helpers, seniors, leads = ladder(body)
    assert (leads["name"], leads["position"], leads["role_ids"]) == ("Leads", 3, [str(OTHER_ROLE)])

    change = {"can_summon": [leads["id"]], "requires_reason": True, "reping_minutes": 30}
    status, body = await tickets("PATCH", f"/tiers/{helpers['id']}", change)
    assert status == 200
    assert {k: ladder(body)[0][k] for k in change} == change
    assert (await tickets("PATCH", f"/tiers/{helpers['id']}", {"can_summon": [helpers["id"]]}))[0] == 422
    assert (await tickets("PATCH", f"/tiers/{helpers['id']}", {"reping_minutes": 1441}))[0] == 422
    body = (await tickets("PATCH", f"/tiers/{helpers['id']}", {"reping_minutes": 0}))[1]
    assert ladder(body)[0]["reping_minutes"] is None

    order = f"/types/{support['id']}/tiers/order"
    assert (await tickets("PUT", order, {"tier_ids": [leads["id"], helpers["id"]]}))[0] == 422
    assert (await tickets("PUT", order, {"tier_ids": [leads["id"], str(helpers["id"]), seniors["id"]]}))[0] == 422
    status, body = await tickets("PUT", order, {"tier_ids": [leads["id"], helpers["id"], seniors["id"]]})
    assert [(t["name"], t["position"]) for t in ladder(body)] == [("Leads", 1), ("Helpers", 2), ("Seniors", 3)]
    # The bot sees the new ladder: tickets start on the lowest tier, now Leads.
    first = tickets.guild.tickets[0]
    assert (await svc.full_type(first))["tiers"][0]["name"] == "Leads"

    status, body = await tickets("DELETE", f"/tiers/{leads['id']}")
    assert [t["name"] for t in ladder(body)] == ["Helpers", "Seniors"]
    assert ladder(body)[0]["can_summon"] == []  # Helpers could summon Leads; still editable
    assert (await tickets("PATCH", f"/tiers/{helpers['id']}", {"can_summon": [seniors["id"]]}))[0] == 200
    assert (await tickets("DELETE", f"/tiers/{leads['id']}"))[0] == 404
    assert (await tickets("POST", "/types/999999/tiers", {"name": "X", "role_ids": []}))[0] == 404


@pytest.mark.asyncio
async def test_add_tier_takes_the_next_rung_in_the_insert(tickets, monkeypatch):
    repo, support = tickets.svc.repo, tickets.support
    list_tiers = repo.list_tiers

    async def raced(type_id, name):  # another manager adds a tier between the read and the insert
        tiers = await list_tiers(type_id)
        await repo.create_tier(type_id, name=name, role_ids=[OTHER_ROLE])
        return tiers

    path = f"/types/{support['id']}/tiers"
    monkeypatch.setattr(repo, "list_tiers", lambda t: raced(t, "Leads"))
    status, body = await tickets("POST", path, {"name": "Admins", "role_ids": [str(OTHER_ROLE)]})
    assert status == 200
    assert [(t["name"], t["position"]) for t in ladder(body)][2:] == [("Leads", 3), ("Admins", 4)]
    monkeypatch.setattr(repo, "list_tiers", lambda t: raced(t, "Owners"))
    status, body = await tickets("POST", path, {"name": "Owners", "role_ids": [str(OTHER_ROLE)]})
    assert status == 409 and "a moment ago" in body["error"]["message"]


def closed_today() -> str:
    """Hours that are closed all of today in UTC: an hour two days from now."""
    return f"{DAYS[(datetime.now(timezone.utc).weekday() + 2) % 7]} 10:00-11:00"


@pytest.mark.asyncio
async def test_hours_validation(tickets):
    status, body = await tickets("PATCH", "/hours", {"schedule": "mon-fri 9-5"})
    assert status == 422 and "couldn't read" in body["error"]["message"]
    assert (await tickets("PATCH", "/hours", {"timezone": "Mars/Base"}))[0] == 422
    assert (await tickets("PATCH", "/hours", {"timezone": []}))[0] == 422
    assert (await tickets("PATCH", "/hours", {"away": {"until": "2001-01-01 09:00"}}))[1]["error"][
        "message"
    ] == "That's in the past."
    assert (await tickets("PATCH", "/hours", {}))[0] == 400

    status, body = await tickets(
        "PATCH", "/hours", {"schedule": "mon-fri 09:00-17:00", "timezone": "Europe/London"}
    )
    assert status == 200
    assert body["schedule"] == parse_schedule("mon-fri 09:00-17:00") and body["timezone"] == "Europe/London"
    assert (await tickets.svc.settings(GUILD))["schedule"] == body["schedule"]
    body = (await tickets("PATCH", "/hours", {"schedule": None}))[1]
    assert body["schedule"] is None


@pytest.mark.asyncio
async def test_preview_is_the_message_the_bot_sends(tickets):
    svc, support = tickets.svc, tickets.support
    until = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    draft = {
        "schedule": closed_today(),
        "off_hours_message": "Hey {user}! Back {next_open}. Hours: {hours}",
        "away": {"until": until, "note": "Team offsite"},
    }
    status, preview = await tickets("POST", "/hours/preview", draft)
    assert status == 200 and preview["online"] is False
    assert (await svc.settings(GUILD)).get("off_hours_message") is None  # a preview saves nothing
    assert preview["content"].startswith(f"Hey <@{USER}>! Back <t:")
    assert preview["content"].endswith("\n> Team offsite")

    await tickets("PATCH", "/hours", draft)
    assert preview["content"] == await svc.off_hours_notice(GUILD, support, USER)

    # Without away mode: the default message, and the same again with a type's own hours.
    await tickets("PATCH", "/hours", {"away": None, "off_hours_message": None})
    for body, type_id in [({}, None), ({"type_id": support["id"]}, support["id"])]:
        if type_id:
            own = parse_schedule(closed_today().replace("10:00-11:00", "12:00-13:00"))
            support = await svc.repo.update_type(GUILD, type_id, schedule=own)
        preview = (await tickets("POST", "/hours/preview", body))[1]
        assert preview["content"] == await svc.off_hours_notice(GUILD, support, USER)
        assert preview["content"].startswith(f"Hi <@{USER}>, our support team is away")


@pytest.mark.asyncio
async def test_preview_while_open_shows_the_next_close(tickets):
    status, preview = await tickets("POST", "/hours/preview", {"schedule": None})
    assert (preview["online"], preview["content"]) == (True, None)  # no hours: never closed
    preview = (await tickets("POST", "/hours/preview", {"schedule": "daily 00:00-24:00"}))[1]
    assert preview["content"] is None

    now = datetime.now(timezone.utc)
    day = DAYS[now.weekday()]
    preview = (await tickets("POST", "/hours/preview", {"schedule": f"{day} 00:00-24:00"}))[1]
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), timezone.utc)
    assert preview["online"] is True and preview["at"] == int(tomorrow.timestamp())
    assert f"Our hours: {day.title()} 00:00–24:00 (UTC)." in preview["content"]


def test_next_closed():
    tz = "Europe/London"
    london = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)  # a Monday, GMT
    weekdays = parse_schedule("mon-fri 09:00-17:00")
    assert next_closed(weekdays, tz, london) == datetime(2026, 10, 26, 17, tzinfo=timezone.utc)
    assert next_closed(weekdays, tz, london.replace(hour=20)) == london.replace(hour=20)
    nights = parse_schedule("mon 20:00-24:00; tue 00:00-02:00")
    assert next_closed(nights, tz, london.replace(hour=21)) == datetime(2026, 10, 27, 2, tzinfo=timezone.utc)
    assert next_closed(parse_schedule("daily 00:00-24:00"), tz, london) is None
    assert next_closed(None, tz, london) is None


@pytest.mark.asyncio
async def test_staff_cant_edit_tiers_or_hours(client_for, pg_repo, ticket_guild):  # noqa: F811
    staff = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    for method, path in [("GET", "/tiers"), ("GET", "/hours"), ("POST", "/hours/preview")]:
        resp = await staff.request(
            method, f"/api/guilds/{GUILD}/tickets{path}", json={}, cookies=COOKIES, headers=HEADERS
        )
        assert resp.status == 403, (method, path)
