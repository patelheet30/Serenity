"""Dashboard panel editor and template packs (#73), on Postgres with Discord mocked."""

import itertools
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest
import pytest_asyncio
from hikari.impl import MessageActionRowBuilder

from serenity.core.modules import ModuleManager
from serenity.dashboard import tickets_panels
from serenity.dashboard.common import TICKET_SVC
from tests import test_dashboard_api as dash
from tests.test_dashboard_api import COOKIES, GUILD, HEADERS, TEXT_CHANNEL, make_bot

client_for, fake_permissions = dash.client_for, dash.fake_permissions  # its fixtures

STAFF_ROLE = 801


@pytest_asyncio.fixture
async def manager(client_for, pg_repo, monkeypatch):
    """call(method, path, body) as a manager; .bot and .svc are the Discord mock and service."""
    monkeypatch.setenv("TICKETS_GUILDS", str(GUILD))
    bot = make_bot()
    bot.cache.get_roles_view_for_guild.return_value = {STAFF_ROLE: MagicMock()}
    bot.cache.get_me.return_value = None
    ids = itertools.count(900_000)
    bot.rest.create_message = AsyncMock(side_effect=lambda *a, **k: MagicMock(id=next(ids)))
    bot.rest.edit_message = AsyncMock()
    bot.rest.delete_message = AsyncMock()
    bot.rest.create_guild_text_channel = AsyncMock(side_effect=lambda *a, **k: MagicMock(id=next(ids)))
    bot.rest.build_message_action_row = MagicMock(side_effect=MessageActionRowBuilder)
    client = await client_for(bot, pg_repo)
    svc = client.api[TICKET_SVC]
    svc.modules = ModuleManager(pg_repo)  # panels and opening read the ticket settings

    async def call(method, path, body=None):
        resp = await client.request(
            method, f"/api/guilds/{GUILD}/tickets{path}", json=body, cookies=COOKIES, headers=HEADERS
        )
        return resp.status, await resp.json()

    call.bot, call.svc = bot, svc
    yield call
    for task in svc._topic_tasks.values():
        task.cancel()


def custom_ids(components) -> list[str]:
    return [c.custom_id for row in components for c in row.components]


@pytest.mark.asyncio
async def test_game_server_pack_publishes_a_panel_members_can_open_tickets_from(manager):
    status, body = await manager("POST", "/packs/game", {"staff_role_id": str(STAFF_ROLE)})
    assert status == 200
    names = [t["name"] for t in body["types"]]
    assert names == ["Player report", "Ban appeal", "Bug report", "Store"]

    draft = body["panel"]
    status, panel = await manager("POST", "/panels", {**draft, "channel_id": str(TEXT_CHANNEL)})
    assert status == 201 and panel["url"].endswith(panel["message_id"])
    sent = manager.bot.rest.create_message.call_args
    assert sent.args[0] == TEXT_CHANNEL and sent.kwargs["embed"].title == "Server support"
    report_id = draft["type_ids"][0]
    assert custom_ids(sent.kwargs["components"]) == [f"ticket:open:{i}" for i in draft["type_ids"]]

    # A member presses the published Player report button and fills in its form.
    type_id = int(custom_ids(sent.kwargs["components"])[0].rsplit(":", 1)[1])
    assert type_id == report_id
    report = await manager.svc.repo.get_type(GUILD, type_id)
    assert report["staff_role_ids"] == [STAFF_ROLE]
    assert [s["name"] for s in report["stages"]] == ["Reviewing", "Action taken", "No action"]
    member = MagicMock(id=555, username="alice", role_ids=[])
    answers = dict(zip([f["key"] for f in report["fields"]], ["Steve", "Griefer", "Broke my base", ""]))
    ticket = await manager.svc.open_ticket(GUILD, member, report, answers)
    assert ticket["number"] == 1 and ticket["type_id"] == type_id
    assert (await manager.svc.repo.get_ticket(GUILD, 1))["fields"]["your_in_game_name"] == "Steve"

    status, listed = await manager("GET", "/panels")
    assert [p["id"] for p in listed["panels"]] == [panel["id"]]

    # Updating edits the same message; deleting removes it.
    status, updated = await manager("PATCH", f"/panels/{panel['id']}", {"title": "Help", "style": "menu"})
    assert status == 200 and updated["message_id"] == panel["message_id"]
    edit = manager.bot.rest.edit_message.call_args
    assert edit.args[1] == int(panel["message_id"]) and edit.kwargs["embed"].title == "Help"
    assert custom_ids(edit.kwargs["components"]) == [f"ticket:menu:{panel['id']}"]
    assert (await manager("DELETE", f"/panels/{panel['id']}"))[0] == 200
    manager.bot.rest.delete_message.assert_awaited_once_with(TEXT_CHANNEL, int(panel["message_id"]))
    assert (await manager("GET", "/panels"))[1]["panels"] == []


@pytest.mark.asyncio
async def test_packs_reuse_existing_types_and_check_the_role(manager):
    support = await manager.svc.repo.create_type(GUILD, "support", emoji="🛟")
    status, body = await manager("POST", "/packs/community", {})
    assert status == 200
    assert body["types"][0]["id"] == support["id"]  # same name, any case: kept as it is
    again = (await manager("POST", "/packs/community", {}))[1]
    assert again["types"] == body["types"]
    assert len(await manager.svc.repo.list_types(GUILD)) == 4

    assert (await manager("POST", "/packs/blank", {}))[1]["panel"]["type_ids"] == []
    assert (await manager("POST", "/packs/nope", {}))[0] == 404
    assert (await manager("POST", "/packs/game", {"staff_role_id": "12345"}))[0] == 422


@pytest.mark.asyncio
async def test_panel_input_is_checked_like_the_command(manager):
    t = await manager.svc.repo.create_type(GUILD, "Support")
    other = await manager.svc.repo.create_type(GUILD + 1, "Elsewhere")
    good = {"channel_id": str(TEXT_CHANNEL), "title": "Help", "style": "buttons", "type_ids": [t["id"]]}
    for bad in (
        {"title": " "},
        {"title": "x" * 257},
        {"description": "x" * 2001},
        {"style": "grid"},
        {"type_ids": []},
        {"type_ids": [other["id"]]},
        {"type_ids": [str(t["id"])]},
        {"channel_id": "1"},
    ):
        assert (await manager("POST", "/panels", {**good, **bad}))[0] == 422, bad
    assert manager.bot.rest.create_message.await_count == 0

    status, panel = await manager("POST", "/panels", {**good, "type_ids": [t["id"], t["id"]]})
    assert panel["type_ids"] == [t["id"]]
    assert (await manager("PATCH", f"/panels/{panel['id']}", {"channel_id": "1"}))[0] == 422
    assert (await manager("PATCH", "/panels/999", {}))[0] == 404


@pytest.mark.asyncio
async def test_a_panel_that_cant_be_posted_isnt_kept(manager):
    t = await manager.svc.repo.create_type(GUILD, "Support")
    manager.bot.rest.create_message.side_effect = hikari.ForbiddenError("", {}, b"")
    body = {"channel_id": str(TEXT_CHANNEL), "title": "Help", "style": "buttons", "type_ids": [t["id"]]}
    status, error = await manager("POST", "/panels", body)
    assert status == 422 and "Send Messages" in error["error"]["message"]
    assert await manager.svc.repo.list_panels(GUILD) == []


@pytest.mark.asyncio
async def test_an_edit_discord_refuses_keeps_the_old_panel(manager):
    t = await manager.svc.repo.create_type(GUILD, "Support")
    body = {"channel_id": str(TEXT_CHANNEL), "title": "Help", "style": "buttons", "type_ids": [t["id"]]}
    status, panel = await manager("POST", "/panels", body)
    assert status == 201
    manager.bot.rest.edit_message.side_effect = hikari.ForbiddenError("", {}, b"")
    status, _ = await manager("PATCH", f"/panels/{panel['id']}", {"title": "New", "style": "menu"})
    assert status == 422
    kept = await manager.svc.repo.get_panel(GUILD, panel["id"])
    assert (kept["title"], kept["style"], kept["message_id"]) == ("Help", "buttons", int(panel["message_id"]))


def test_panel_limits_match_the_command():
    from serenity.extensions import tickets

    assert tickets_panels.PANEL_STYLES is tickets.PANEL_STYLES  # one list for both
    assert tickets_panels.MAX_PANEL_TYPES == tickets.MAX_PANEL_TYPES == 25


def test_every_pack_passes_the_type_and_panel_rules():
    from serenity.services.ticket_service import MAX_FIELDS, asked_fields

    assert set(tickets_panels.PACKS) == {"game", "freelance", "community", "blank"}
    for key, pack in tickets_panels.PACKS.items():
        assert pack["panel"]["style"] in tickets_panels.PANEL_STYLES.values(), key
        assert len(pack["types"]) <= tickets_panels.MAX_PANEL_TYPES, key
        for spec in pack["types"]:
            values = tickets_panels._pack_values(spec)  # set_field / with_stage raise on a bad pack
            assert len(asked_fields(values)) <= MAX_FIELDS, (key, spec["name"])
