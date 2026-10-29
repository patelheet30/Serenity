"""Dashboard: server-wide ticket settings and the blacklist (#75), as TicketService sees them."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest
import pytest_asyncio

from serenity.dashboard.common import TICKET_SVC
from serenity.services.ticket_service import RAID_PAUSED, TicketError
from tests.test_dashboard_api import (  # noqa: F401  (fixtures)
    COOKIES,
    GUILD,
    HEADERS,
    OTHER_ROLE,
    SUPPORT_ROLE,
    TEXT_CHANNEL,
    USER,
    client_for,
    fake_permissions,
    make_bot,
    ticket_guild,
)

MEMBER = 555


@pytest_asyncio.fixture
async def tickets(client_for, pg_repo, ticket_guild):  # noqa: F811
    """A manager's API client: call(method, path, body) -> (status, json); .svc is the bot's."""
    bot = make_bot()
    bot.cache.get_roles_view_for_guild.return_value = {SUPPORT_ROLE: object(), OTHER_ROLE: object()}
    client = await client_for(bot, pg_repo)
    svc = client.api[TICKET_SVC]

    async def call(method, path, body=None):
        resp = await client.request(
            method, f"/api/guilds/{GUILD}/tickets{path}", json=body, cookies=COOKIES, headers=HEADERS
        )
        return resp.status, await resp.json()

    call.svc, call.support = svc, await svc.repo.get_type(GUILD, ticket_guild.tickets[0]["type_id"])
    return call


def member(*, roles=(), age_days=400):
    return MagicMock(
        id=MEMBER, role_ids=list(roles), created_at=datetime.now(timezone.utc) - timedelta(days=age_days)
    )


async def refused(svc, ticket_type, m):
    with pytest.raises(TicketError) as exc:
        await svc.check_can_open(GUILD, m, ticket_type)
    return str(exc.value)


@pytest.mark.asyncio
async def test_defaults_on_an_empty_server(tickets):
    status, body = await tickets("GET", "/settings")
    assert status == 200
    assert body["retention_days"] == 30 and body["rating_window_days"] == 7
    assert body["raid_limit"] == 0 and body["raid_window_minutes"] == 5
    assert body["required_role_id"] is None and body["log_channel_id"] is None
    assert body["blacklist"] == []


@pytest.mark.asyncio
async def test_limits_match_the_slash_commands(tickets):
    for key, bad in [
        ("retention_days", 6), ("retention_days", 366), ("rating_window_days", 0),
        ("open_cooldown_minutes", 1441), ("raid_limit", 101), ("raid_window_minutes", 0),
        ("min_account_age_days", -1), ("retention_days", True), ("required_role_id", "999"),
    ]:
        status, body = await tickets("PATCH", "/settings", {key: bad})
        assert status == 422, (key, bad)
    assert (await tickets("PATCH", "/settings", {}))[0] == 400
    assert (await tickets("PATCH", "/settings", {"raid_paused": True}))[0] == 422
    limits = (await tickets("GET", "/settings"))[1]["limits"]  # what the page's inputs use
    assert limits["retention_days"] == [7, 365] and limits["open_cooldown_minutes"] == [0, 1440]


@pytest.mark.asyncio
async def test_changes_apply_to_the_next_ticket(tickets):
    svc, support = tickets.svc, tickets.support
    await svc.check_can_open(GUILD, member(), support)  # nothing set: allowed

    status, body = await tickets(
        "PATCH", "/settings", {"min_account_age_days": 30, "required_role_id": str(SUPPORT_ROLE)}
    )
    assert status == 200 and body["required_role_id"] == str(SUPPORT_ROLE)
    assert "role to open tickets" in await refused(svc, support, member())
    assert "30 days old" in await refused(svc, support, member(roles=[SUPPORT_ROLE], age_days=3))
    await tickets("PATCH", "/settings", {"min_account_age_days": 0, "required_role_id": None})
    await svc.check_can_open(GUILD, member(), support)

    await tickets("PATCH", "/settings", {"retention_days": 90})
    assert await svc.retention_days(GUILD, support) == 90

    # Cooldown: MEMBER just opened (and closed) one.
    ticket = await svc.repo.create_ticket(GUILD, support["id"], MEMBER, {})
    await svc.repo.close_ticket(ticket["id"], MEMBER, None)
    await tickets("PATCH", "/settings", {"open_cooldown_minutes": 10})
    assert "open another ticket" in await refused(svc, support, member())
    await tickets("PATCH", "/settings", {"open_cooldown_minutes": 0})
    await svc.check_can_open(GUILD, member(), support)


@pytest.mark.asyncio
async def test_raid_brake_and_resume(tickets):
    svc, support = tickets.svc, tickets.support
    svc.raid_alert = AsyncMock(return_value=True)  # the alert was posted, so it latches
    await tickets("PATCH", "/settings", {"raid_limit": 1, "raid_window_minutes": 10})
    await svc.update_settings(GUILD, raid_resumed_at=0)  # set a while ago, so this second counts
    await svc.repo.create_ticket(GUILD, support["id"], USER, {})
    assert await refused(svc, support, member()) == RAID_PAUSED
    status, body = await tickets("GET", "/settings")
    assert body["raid_paused"] is True

    status, body = await tickets("PATCH", "/settings", {"raid_paused": False})
    assert status == 200 and body["raid_paused"] is False
    await svc.check_can_open(GUILD, member(), support)  # the count starts afresh

    await tickets("PATCH", "/settings", {"raid_limit": 0})
    assert (await svc.settings(GUILD))["raid_limit"] == 0


@pytest.mark.asyncio
async def test_ratings_window(tickets):
    svc, support = tickets.svc, tickets.support
    await svc.repo.update_type(GUILD, support["id"], ratings=True)
    ticket = await svc.repo.create_ticket(GUILD, support["id"], MEMBER, {})
    await svc.repo.close_ticket(ticket["id"], USER, None)
    closed_at = int(time.time()) - 3 * 86400
    await svc.repo.pool.execute("UPDATE tickets SET closed_at = $2 WHERE id = $1", ticket["id"], closed_at)
    svc.modules.is_enabled = AsyncMock(return_value=True)
    await tickets("PATCH", "/settings", {"rating_window_days": 2})
    with pytest.raises(TicketError, match="2 days"):
        await svc.check_can_rate(ticket["id"], MEMBER)
    await tickets("PATCH", "/settings", {"rating_window_days": 5})
    await svc.check_can_rate(ticket["id"], MEMBER)


@pytest.mark.asyncio
async def test_blacklist(tickets):
    svc, support = tickets.svc, tickets.support
    status, body = await tickets("POST", "/blacklist", {"user_id": str(MEMBER), "reason": "spam"})
    assert status == 200
    assert [(b["user_id"], b["reason"], b["moderator_id"]) for b in body["blacklist"]] == [
        (str(MEMBER), "spam", str(USER))
    ]
    assert await refused(svc, support, member()) == "You can't open tickets in this server."
    assert (await tickets("POST", "/blacklist", {"user_id": str(MEMBER)}))[0] == 409
    assert (await tickets("POST", "/blacklist", {"user_id": 5}))[0] == 422
    assert (await tickets("POST", "/blacklist", {"user_id": "5", "reason": "x" * 501}))[0] == 422

    status, body = await tickets("DELETE", f"/blacklist/{MEMBER}")
    assert status == 200 and body["blacklist"] == []
    await svc.check_can_open(GUILD, member(), support)
    assert (await tickets("DELETE", f"/blacklist/{MEMBER}"))[0] == 404


@pytest.mark.asyncio
async def test_ticket_log_channel_shows_the_logging_setting(tickets, client_for, pg_repo):  # noqa: F811
    client = await client_for(make_bot(), pg_repo)
    resp = await client.put(
        f"/api/guilds/{GUILD}/logging/ticket",
        json={"channel_id": str(TEXT_CHANNEL)},
        cookies=COOKIES,
        headers=HEADERS,
    )
    assert resp.status == 200
    body = (await tickets("GET", "/settings"))[1]
    assert body["log_channel_id"] == str(TEXT_CHANNEL) and body["log_enabled"] is True


@pytest.mark.asyncio
async def test_staff_cant_change_settings(client_for, pg_repo, ticket_guild):  # noqa: F811
    staff = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    for method, path in [("GET", "/settings"), ("PATCH", "/settings"), ("POST", "/blacklist")]:
        resp = await staff.request(
            method, f"/api/guilds/{GUILD}/tickets{path}", json={}, cookies=COOKIES, headers=HEADERS
        )
        assert resp.status == 403, (method, path)
