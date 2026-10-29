"""Dashboard ticket detail and actions (#71): they go through TicketService, so Discord sees them,
and ticket staff can act only on tickets they can see."""

from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest
from hikari.impl import MessageActionRowBuilder

from serenity.core.modules import ModuleType
from serenity.dashboard.common import ACCESS, BOT, CHANGES, MODULES, TICKET_SVC
from serenity.services.ticket_service import set_field
from tests import test_dashboard_api as base
from tests.test_dashboard_api import (
    BILLING_ROLE,
    COOKIES,
    GUILD,
    HEADERS,
    SENIOR_ROLE,
    SUPPORT_ROLE,
    USER,
    make_bot,
    make_member,
)
from tests.test_ticket_flows import FakeHistory

client_for, fake_permissions, ticket_guild = base.client_for, base.fake_permissions, base.ticket_guild
ME = 999  # the bot


def discord(bot):
    """The REST calls TicketService makes, recorded."""
    rest = bot.rest
    for name in ("create_message", "edit_message", "edit_channel", "delete_channel", "edit_permission_overwrite", "create_dm_channel"):
        setattr(rest, name, AsyncMock())
    rest.build_message_action_row = MagicMock(side_effect=MessageActionRowBuilder)
    rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    bot.cache.get_me.return_value = MagicMock(id=ME)
    guild = MagicMock(owner_id=1)
    guild.name = "Test"
    bot.cache.get_guild.side_effect = lambda gid: guild if gid == GUILD else None
    return bot


async def setup(client_for, pg_repo, ticket_guild, bot):
    client = await client_for(discord(bot), pg_repo)
    client.api[TICKET_SVC].modules = client.api[MODULES]  # the real one: close reads settings
    for n, t in enumerate(ticket_guild.tickets):
        await ticket_guild.repo.set_ticket_channel(t["id"], 7000 + n)
    return client


async def call(client, method, path, body=None):
    resp = await client.request(
        method, f"/api/guilds/{GUILD}/tickets/{path}", json=body, headers=HEADERS, cookies=COOKIES
    )
    return resp.status, await resp.json()


def posted(bot):
    return [(c.args[1] if len(c.args) > 1 else "") for c in bot.rest.create_message.call_args_list]


@pytest.mark.asyncio
async def test_actions_reach_discord(client_for, pg_repo, ticket_guild):
    repo = ticket_guild.repo
    bot = make_bot()
    client = await setup(client_for, pg_repo, ticket_guild, bot)
    t1 = ticket_guild.tickets[0]

    status, body = await call(client, "POST", "1/claim")
    assert status == 200 and body["assignee"]["id"] == str(USER)
    assert body["handlers"] == [{"user": body["assignee"], "role": "claimed", "messages": 0}]
    assert "claimed this ticket" in posted(bot)[-1]
    assert (await call(client, "POST", "1/claim"))[0] == 422  # already theirs

    status, body = await call(client, "POST", "1/escalate", {"tier": "seniors", "reason": "needs a senior"})
    assert status == 200 and body["tier_id"] == ticket_guild.seniors["id"]
    assert bot.rest.edit_permission_overwrite.call_args.args[1] == SENIOR_ROLE
    assert "escalated this ticket to **Seniors**" in posted(bot)[-1]
    status, body = await call(client, "POST", "1/deescalate", {})
    assert status == 422 and body["error"]["message"] == "Add a reason: it's posted for the tier taking the ticket back."
    status, body = await call(client, "POST", "1/deescalate", {"reason": "sorted"})
    assert body["tier_id"] == ticket_guild.helpers["id"] and body["assignee"] is None

    await call(client, "POST", "1/priority", {"priority": "urgent"})
    assert bot.rest.edit_channel.call_args.kwargs["name"].startswith("urgent-")
    status, body = await call(client, "POST", "1/priority", {"priority": "urgent"})
    assert status == 422 and body["error"]["message"] == "This ticket is already urgent priority."

    stages = [{"name": n, "color": None, "terminal": n == "Done", "auto_close": True} for n in ("New", "Done")]
    await repo.update_type(GUILD, t1["type_id"], stages=stages, fields=set_field([], "IGN"))
    status, body = await call(client, "POST", "1/stage", {"stage": "done"})
    assert status == 200 and body["stage"] == "Done"
    assert "moved this ticket to **Done**" in bot.rest.create_message.call_args.kwargs["embed"].description

    welcome = MagicMock(author=MagicMock(id=ME), embeds=[hikari.Embed(title="Support · #1")])
    bot.rest.fetch_messages.return_value = FakeHistory([welcome])
    status, body = await call(client, "PATCH", "1/fields", {"fields": {}})
    assert status == 422 and body["error"]["message"] == "Please answer IGN."
    status, body = await call(client, "PATCH", "1/fields", {"fields": {"ign": "Notch_2"}})
    assert body["fields"] == {"ign": "Notch_2"}
    assert bot.rest.edit_message.call_args.kwargs["embed"].fields[0].value == "Notch_2"

    status, body = await call(client, "POST", "1/notes", {"text": "  seen before "})
    assert [n["body"] for n in body["notes"]] == ["seen before"]

    bot.rest.fetch_messages.return_value = FakeHistory([])  # the transcript reads the channel
    status, body = await call(client, "POST", "1/close", {"reason": "fixed"})
    assert status == 200 and body["status"] == "closed" and body["close_reason"] == "fixed"
    bot.rest.delete_channel.assert_awaited_once_with(7000, reason="Ticket #1 closed")
    assert (await call(client, "POST", "1/claim"))[0] == 409
    assert {e["kind"] for e in body["events"]} >= {"claimed", "escalated", "priority", "stage", "closed"}


@pytest.mark.asyncio
async def test_staff_act_only_on_tickets_they_see(client_for, pg_repo, ticket_guild):
    seniors = await setup(
        client_for, pg_repo, ticket_guild, make_bot(perms=hikari.Permissions.NONE, roles=[SENIOR_ROLE])
    )
    assert (await call(seniors, "POST", "1/claim"))[0] == 404  # still with Helpers
    assert (await call(seniors, "POST", "3/claim"))[0] == 404  # Billing
    assert (await call(seniors, "GET", "3"))[0] == 404
    status, body = await call(seniors, "GET", "2")
    assert status == 200 and body["can"]["reassign"]  # the tier it was escalated to

    # The reassign picker offers the type's staff only.
    staff_member, other = make_member(hikari.Permissions.NONE, [SENIOR_ROLE]), make_member(hikari.Permissions.NONE)
    for i, m in enumerate((staff_member, other)):
        m.id, m.username, m.display_name = USER + 7 + i, f"sam{i}", f"Sam {i}"
    seniors.api[BOT].cache.get_members_view_for_guild.return_value = {m.id: m for m in (staff_member, other)}
    status, body = await call(seniors, "GET", "2/assignees?query=SAM")
    assert [u["id"] for u in body] == [str(USER + 7)]

    # Seniors may reassign #2 (to staff); Support's base tier may not.
    assert (await call(seniors, "POST", "2/assign", {"user_id": str(USER + 7)}))[0] == 200
    support = await client_for(discord(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE])), pg_repo)
    status, body = await call(support, "POST", "2/assign", {"user_id": str(USER)})
    assert status == 403
    # Not theirs to unclaim either.
    status, body = await call(support, "POST", "2/unclaim")
    assert status == 422 and body["error"]["message"].startswith("Only @aria or an admin")

    billing = await client_for(discord(make_bot(perms=hikari.Permissions.NONE, roles=[BILLING_ROLE])), pg_repo)
    assert (await call(billing, "POST", "3/notes", {"text": "x" * 2001}))[0] == 422
    assert (await call(billing, "POST", "3/notes", {"text": "ok"}))[0] == 200


@pytest.mark.asyncio
async def test_mod_action_keeps_the_bot_checks(client_for, pg_repo, ticket_guild):
    bot = make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE])
    moderator, target = bot.cache.get_member.return_value, make_member(hikari.Permissions.NONE)
    moderator.id, target.id = USER, USER + 1
    bot.cache.get_member.return_value = None
    bot.cache.get_member.side_effect = lambda _, user_id: {USER: moderator, USER + 1: target}.get(user_id)
    bot.cache.get_roles_view_for_guild.return_value = {SUPPORT_ROLE: MagicMock(position=5)}
    client = await setup(client_for, pg_repo, ticket_guild, bot)
    client.api[CHANGES].logging_svc = AsyncMock()
    warn = {"user_id": str(USER + 1), "action": "warn", "reason": "spam"}

    status, body = await call(client, "POST", "1/mod", warn)
    assert status == 422 and "Moderation module" in body["error"]["message"]
    await client.api[MODULES].enable_module(GUILD, ModuleType.LOGGING)
    await client.api[MODULES].enable_module(GUILD, ModuleType.MODERATION)
    status, body = await call(client, "POST", "1/mod", warn)
    assert status == 422 and body["error"]["message"] == "You need Moderate Members to warn members."
    status, body = await call(client, "POST", "1/mod", {**warn, "action": "timeout", "duration": "1y"})
    assert status == 422 and "30m, 2h or 7d" in body["error"]["message"]

    moderator.perms = hikari.Permissions.MODERATE_MEMBERS  # fresh member reads on every request
    client.api[ACCESS]._cache.clear()
    status, body = await call(client, "POST", "1/mod", warn)
    assert status == 200 and body["case"] == 1
    assert body["moderation"][0]["user"]["id"] == str(USER + 1)  # the opener's record
    assert body["moderation"][0]["warnings"] == 1 and body["can"]["mod"] == ["warn", "timeout"]


@pytest.mark.asyncio
async def test_conversation_preview_reads_discord(client_for, pg_repo, ticket_guild):
    bot = make_bot()
    client = await setup(client_for, pg_repo, ticket_guild, bot)
    author = MagicMock(id=USER + 1, display_name="Notch", is_bot=False, display_avatar_url="a.png")
    newest_first = [
        MagicMock(id=2**60 + i, author=author, content=f"msg {i}", embeds=[], attachments=[], edited_timestamp=None)
        for i in (2, 1)
    ]
    for m in newest_first:
        m.timestamp.timestamp.return_value = 1_700_000_000
    bot.rest.fetch_messages.return_value = FakeHistory(newest_first)
    status, body = await call(client, "GET", "1/messages")
    assert status == 200 and [m["content"] for m in body] == ["msg 1", "msg 2"]
    assert body[0]["id"] == str(2**60 + 1) and body[0]["author"]["id"] == str(USER + 1)


@pytest.mark.asyncio
async def test_slow_renames_go_to_the_background_only_up_to_a_cap(client_for, pg_repo, ticket_guild, monkeypatch):
    import asyncio

    from serenity.dashboard import tickets_actions

    bot = make_bot()
    client = await setup(client_for, pg_repo, ticket_guild, bot)

    async def slow_rename(*_, **__):
        await asyncio.sleep(0.05)

    bot.rest.edit_channel = AsyncMock(side_effect=slow_rename)
    monkeypatch.setattr(tickets_actions, "RENAME_WAIT", 0.01)
    status, body = await call(client, "POST", "1/priority", {"priority": "high"})
    assert status == 200 and body["renaming"] is True  # finishes in the background
    monkeypatch.setattr(tickets_actions, "MAX_RENAMES", len(tickets_actions._renames))
    status, body = await call(client, "POST", "3/priority", {"priority": "urgent"})
    assert status == 200 and not body.get("renaming")  # at the cap: waited for in the request
    await asyncio.gather(*tickets_actions._renames)


@pytest.mark.asyncio
async def test_a_cancelled_request_still_tracks_its_rename(client_for, pg_repo, ticket_guild, monkeypatch):
    import asyncio

    import aiohttp

    from serenity.dashboard import tickets_actions

    bot = make_bot()
    client = await setup(client_for, pg_repo, ticket_guild, bot)
    renamed = asyncio.Event()
    async def slow_rename(*_, **__):
        await renamed.wait()

    bot.rest.edit_channel = AsyncMock(side_effect=slow_rename)

    async def client_left(*_, **__):  # the request is cancelled while it waits on Discord
        raise asyncio.CancelledError

    monkeypatch.setattr(tickets_actions.asyncio, "wait", client_left)
    with pytest.raises(aiohttp.ServerDisconnectedError):  # the handler was cancelled
        await client.post(
            f"/api/guilds/{GUILD}/tickets/1/priority", json={"priority": "high"}, headers=HEADERS, cookies=COOKIES
        )
    [task] = tickets_actions._renames  # but the change carries on, tracked
    assert not task.done()
    renamed.set()
    await task
    assert not tickets_actions._renames  # and is dropped once it finishes
