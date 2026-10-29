"""Dashboard ticket type editor (#74): the same rules as `/ticket type`."""

import hikari
import pytest
import pytest_asyncio

from serenity.dashboard.common import CHANGES
from serenity.dashboard.tickets_types import hours_text
from serenity.services.ticket_hours import parse_schedule
from tests.test_dashboard_api import (  # noqa: F401  (fixtures)
    COOKIES,
    GUILD,
    HEADERS,
    SUPPORT_ROLE,
    client_for,
    fake_permissions,
    make_bot,
    ticket_guild,
)

BASE = f"/api/guilds/{GUILD}/tickets/types"


@pytest_asyncio.fixture
async def editor(client_for, pg_repo, ticket_guild):  # noqa: F811
    bot = make_bot()
    bot.cache.get_roles_view_for_guild.return_value = {SUPPORT_ROLE: object()}
    client = await client_for(bot, pg_repo)
    client.category = await bot.rest.create_guild_category(GUILD, "Tickets")

    async def call(method, path="", body=None):
        resp = await client.request(method, BASE + path, json=body, headers=HEADERS, cookies=COOKIES)
        return resp.status, (await resp.json() if resp.status != 204 else None)

    client.call = call
    return client


async def new_type(editor, **body):
    status, created = await editor.call("POST", "", {"name": "Appeals", "staff_role_ids": [str(SUPPORT_ROLE)], **body})
    assert status == 201, created
    return created


@pytest.mark.asyncio
async def test_create_edit_and_list(editor):
    created = await new_type(editor, emoji="⚖️")
    assert created["staff_role_ids"] == [str(SUPPORT_ROLE)] and created["name_template"] == "{type}-{number}"

    changes = {
        "name": "Ban appeals", "description": "Appeal a ban", "category_id": str(editor.category.id),
        "claim_mode": "both", "open_limit": 3, "name_template": "appeal-{user}", "transcripts": False,
        "retention_days": 0, "auto_unclaim_hours": 0, "ratings": False, "auto_close_hours": 48,
        "hours": "mon-fri 09:00-12:00 13:00-17:00; sat 10:00-14:00", "open_off_hours": False,
    }  # fmt: skip
    status, updated = await editor.call("PATCH", f"/{created['id']}", changes)
    assert status == 200, updated
    assert {k: updated[k] for k in changes} == {**changes, "auto_unclaim_hours": None}
    assert editor.api[CHANGES].record.called

    status, types = await editor.call("GET", "/config")
    assert [t["name"] for t in types] == ["Support", "Billing", "Ban appeals"]
    assert types[2] == updated

    _, cleared = await editor.call("PATCH", f"/{created['id']}", {"hours": None, "category_id": None, "emoji": ""})
    assert (cleared["hours"], cleared["category_id"], cleared["emoji"]) == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "status", "message"),
    [
        ({"fields": [{"label": f"Q{i}"} for i in range(6)]}, 422, "at most 5 fields"),
        ({"fields": [{"label": f"Q{i}", "type": "user"} for i in range(5)]}, 422, "at most 4 dropdown"),
        ({"fields": [{"label": "Pick", "type": "select", "options": []}]}, 422, "1–25 options"),
        ({"fields": [{"label": "IGN", "type": "pattern", "pattern": "(a+)*"}]}, 422, "too long to check"),
        ({"fields": [{"label": "IGN", "type": "pattern", "pattern": "[a-"}]}, 422, "isn't a valid regular"),
        ({"fields": [{"label": "IGN", "type": "pattern"}]}, 422, "needs its regular expression"),
        ({"fields": [{"label": "A"}, {"label": "a"}]}, 422, "Two fields"),
        ({"fields": [{"label": ""}]}, 422, "label is 1–45"),
        ({"fields": [{"label": "A", "type": "essay"}]}, 422, "Unknown field type"),
        ({"stages": [{"name": " "}]}, 422, "A stage needs a name."),
        ({"stages": [{"name": "Open"}, {"name": "open"}]}, 422, "Two stages"),
        ({"stages": [{"name": "Open", "color": "#zzz"}]}, 422, "isn't a hex colour"),
        ({"stages": [{"name": f"S{i}"} for i in range(26)]}, 422, "at most 25 stages"),
        ({"retention_days": 3}, 422, "Retention is 0"),
        ({"open_limit": 11}, 422, "1 to 10"),
        ({"claim_mode": "never"}, 422, "claim_mode"),
        ({"hours": "someday"}, 422, "couldn't read"),
        ({"category_id": "333000"}, 422, "isn't a category"),
        ({"staff_role_ids": ["999"]}, 422, "isn't in this server"),
        ({"name": "billing"}, 409, "already exists"),
        ({"name": ""}, 422, "name is 1–80"),
        ({"colour": 1}, 422, "Unknown setting"),
    ],
)
async def test_invalid_configs_are_rejected(editor, body, status, message):
    created = await new_type(editor)
    got, error = await editor.call("PATCH", f"/{created['id']}", body)
    assert (got, message in error["error"]["message"]) == (status, True), error
    assert "**" not in error["error"]["message"]  # the slash commands' markdown is stripped
    _, types = await editor.call("GET", "/config")
    assert types[-1] == created  # nothing half-saved


@pytest.mark.asyncio
async def test_create_needs_a_name_and_a_staff_role(editor):
    assert (await editor.call("POST", "", {"name": "Bugs"}))[0] == 422
    assert (await editor.call("POST", "", {"name": "Support", "staff_role_ids": [str(SUPPORT_ROLE)]}))[0] == 409


@pytest.mark.asyncio
async def test_fields_and_stages_are_rebuilt_in_order(editor):
    created = await new_type(editor)
    fields = [
        {"label": "Ban reason", "type": "paragraph"},
        {"label": "IGN", "type": "pattern", "pattern": "[A-Za-z0-9_]{3,16}", "identity": True},
        {"label": "Platform", "type": "select", "options": ["PC", "Console", "PC"], "required": False},
        {"label": "Internal", "staff_only": True},
    ]
    _, saved = await editor.call("PATCH", f"/{created['id']}", {"fields": fields})
    reason, ign, platform, internal = saved["fields"]
    assert [f["key"] for f in saved["fields"]] == ["ban_reason", "ign", "platform", "internal"]
    assert ign["identity"] and ign["pattern"] == "[A-Za-z0-9_]{3,16}"
    assert platform["options"] == ["PC", "Console"] and not platform["required"]
    assert internal["staff_only"] and not internal["required"]

    # Renamed and reordered fields keep their keys; a new field never takes a kept one.
    edited = [{**ign, "label": "Username"}, {"label": "ban reason!"}, {**reason, "label": "Why"}]
    _, saved = await editor.call("PATCH", f"/{created['id']}", {"fields": edited})
    assert [(f["key"], f["label"]) for f in saved["fields"]] == [
        ("ign", "Username"), ("ban_reason_", "ban reason!"), ("ban_reason", "Why")
    ]

    stages = [{"name": "New", "color": "#5865F2"}, {"name": "Waiting", "auto_close": False}, {"name": "Done", "terminal": True}]
    _, saved = await editor.call("PATCH", f"/{created['id']}", {"stages": stages})
    assert saved["stages"] == [
        {"name": "New", "color": 0x5865F2, "terminal": False, "auto_close": True},
        {"name": "Waiting", "color": None, "terminal": False, "auto_close": False},
        {"name": "Done", "color": None, "terminal": True, "auto_close": True},
    ]
    _, saved = await editor.call("PATCH", f"/{created['id']}", {"stages": saved["stages"][::-1]})
    assert [s["name"] for s in saved["stages"]] == ["Done", "Waiting", "New"]


@pytest.mark.asyncio
async def test_delete_keeps_the_bots_rules(editor, ticket_guild):  # noqa: F811
    _, types = await editor.call("GET", "/config")
    support = types[0]
    status, error = await editor.call("DELETE", f"/{support['id']}")  # has tickets
    assert status == 409 and "types with tickets can't be deleted" in error["error"]["message"]

    shown = await new_type(editor)
    panel = await ticket_guild.repo.create_panel(GUILD, 333_000, "Help", [shown["id"]])
    assert (await editor.call("DELETE", f"/{shown['id']}"))[0] == 409  # on a panel
    await ticket_guild.repo.update_panel(GUILD, panel["id"], type_ids=[support["id"]])
    assert (await editor.call("DELETE", f"/{shown['id']}"))[0] == 204
    assert (await editor.call("DELETE", f"/{shown['id']}"))[0] == 404
    assert (await editor.call("PATCH", f"/{shown['id']}", {"open_limit": 2}))[0] == 404


@pytest.mark.asyncio
async def test_staff_cant_edit_types(client_for, pg_repo, ticket_guild):  # noqa: F811
    staff = await client_for(make_bot(perms=hikari.Permissions.NONE, roles=[SUPPORT_ROLE]), pg_repo)
    resp = await staff.get(f"{BASE}/config", cookies=COOKIES)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_channel_name_example_uses_the_bots_renderer(editor):
    body = {"template": "{type}-{user}-{field:ign}-{number}", "type": "Ban Appeals", "fields": [{"key": "ign", "label": "In game name"}]}
    assert await editor.call("POST", "/channel-name", body) == (200, {"name": "ban-appeals-aria-in-game-name-42"})
    assert (await editor.call("POST", "/channel-name", {"template": ""}))[0] == 422


def test_hours_text_reads_back():
    for text in ("mon-fri 09:00-12:00 13:00-17:00; sat 10:00-14:00", "fri-mon 00:00-24:00", "sun 08:30-09:00"):
        assert parse_schedule(hours_text(parse_schedule(text))) == parse_schedule(text)
    assert hours_text(None) is None
