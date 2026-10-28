"""Ticket helpers that don't need Discord or Postgres."""

import hikari
import pytest

from serenity.services.ticket_service import (
    TicketError,
    is_staff,
    parse_color,
    parse_questions,
    render_topic,
    with_stage,
)


def test_parse_questions():
    fields = parse_questions(" Order number | What went wrong? | order number ")
    assert [f["key"] for f in fields] == ["order_number", "what_went_wrong", "order_number_"]
    assert fields[1]["label"] == "What went wrong?"
    assert parse_questions("") == []
    with pytest.raises(TicketError):
        parse_questions("|".join("q" for _ in range(6)))
    with pytest.raises(TicketError):
        parse_questions("x" * 46)


def test_is_staff():
    ticket_type = {"staff_role_ids": [1, 2]}
    none = hikari.Permissions.NONE
    assert is_staff(ticket_type, [3, 2], none)
    assert not is_staff(ticket_type, [3], none)
    assert is_staff(ticket_type, [], hikari.Permissions.MANAGE_GUILD)


def test_with_stage_orders_and_replaces_in_place():
    def names(stages):
        return [s["name"] for s in stages]

    stages = []
    for name in ("Inquiry", "Quoted", "Paid"):
        stages = with_stage(stages, {"name": name})
    assert names(stages) == ["Inquiry", "Quoted", "Paid"]
    assert names(with_stage(stages, {"name": "In progress"}, 3)) == ["Inquiry", "Quoted", "In progress", "Paid"]
    # An existing name is replaced where it is, unless a position moves it.
    replaced = with_stage(stages, {"name": "quoted", "terminal": True})
    assert names(replaced) == ["Inquiry", "quoted", "Paid"] and replaced[1]["terminal"]
    assert names(with_stage(stages, {"name": "Paid"}, 1)) == ["Paid", "Inquiry", "Quoted"]
    assert names(with_stage(stages, {"name": "Late"}, 99))[-1] == "Late"
    full = [{"name": str(i)} for i in range(25)]
    with pytest.raises(TicketError):
        with_stage(full, {"name": "one more"})
    assert len(with_stage(full, {"name": "3"})) == 25


def test_parse_color():
    assert parse_color("#5865F2") == 0x5865F2
    assert parse_color("ffffff") == 0xFFFFFF
    for bad in ("blue", "#1000000", "-1"):
        with pytest.raises(TicketError):
            parse_color(bad)


def test_render_topic():
    ticket = {"number": 4, "stage": "Quoted", "waiting_on": "opener"}
    assert render_topic(ticket, {"name": "Sales"}) == "Ticket #4 · Sales · Quoted · waiting on member"
    ticket.update(stage=None, waiting_on="staff")
    assert render_topic(ticket, {"name": "Sales"}) == "Ticket #4 · Sales · waiting on staff"


def _service(rest=None, repo=None):
    from unittest.mock import AsyncMock, MagicMock

    from hikari.impl import MessageActionRowBuilder

    from serenity.services.ticket_service import TicketService

    rest = rest or AsyncMock()
    rest.build_message_action_row = MagicMock(side_effect=MessageActionRowBuilder)
    modules = AsyncMock()
    modules.get_module_config.return_value = MagicMock(settings={})
    return TicketService(repo or AsyncMock(), rest, MagicMock(), modules)


def _types(n):
    return [{"id": i, "name": f"T{i}", "emoji": None, "description": None} for i in range(1, n + 1)]


def test_panel_buttons_wrap_at_five_per_row():
    panel = {"id": 9, "title": "Help", "description": None, "style": "buttons"}
    _, rows = _service().build_panel(panel, _types(7))
    built = [row.build()[0]["components"] for row in rows]
    assert [len(r) for r in built] == [5, 2]
    assert built[1][1]["custom_id"] == "ticket:open:7"


def test_panel_menu_lists_types():
    panel = {"id": 9, "title": "Help", "description": None, "style": "menu"}
    _, rows = _service().build_panel(panel, _types(3))
    (menu,) = rows[0].build()[0]["components"]
    assert menu["custom_id"] == "ticket:menu:9"
    assert [o["value"] for o in menu["options"]] == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_panel_deleted_by_hand_is_reposted():
    from unittest.mock import AsyncMock, MagicMock

    rest = AsyncMock()
    rest.edit_message.side_effect = hikari.NotFoundError("", {}, b"")
    rest.create_message.return_value = MagicMock(id=555)
    repo = AsyncMock()
    repo.list_types.return_value = _types(1)
    service = _service(rest, repo)
    service.panel_messages.add(3)
    panel = {"id": 9, "guild_id": 1, "channel_id": 2, "message_id": 3, "title": "Help",
             "description": None, "style": "buttons", "type_ids": [1]}

    await service.post_panel(panel)
    repo.update_panel.assert_awaited_once_with(1, 9, message_id=555)
    assert service.panel_messages == {555}  # the dead message id 3 isn't kept

    await service.panel_message_deleted(555)
    repo.forget_panel_message.assert_awaited_once_with(555)
    await service.panel_message_deleted(1234)  # not a panel: no query
    assert repo.forget_panel_message.await_count == 1


def test_render_channel_name():
    from serenity.services.ticket_service import overflow_name, render_channel_name

    def render(template, **fields):
        return render_channel_name(
            template, type_name="Billing Help", number=12, username="Heet.P", fields=fields
        )

    assert render("{type}-{number}") == "billing-help-12"
    assert render("{user} {field:order}", order="#A-99 x") == "heet-p-a-99-x"
    assert render("{field:missing}🎫") == "ticket-12"  # emoji are dropped with other symbols
    assert render("!!!") == "ticket-12"
    assert len(render("x" * 200)) == 100
    assert [overflow_name("Support", i) for i in (1, 2)] == ["Support", "Support 2"]


@pytest.mark.asyncio
async def test_duplicate_type_name_is_a_friendly_error():
    from unittest.mock import AsyncMock, MagicMock

    import asyncpg

    from serenity.extensions.tickets import on_error

    ctx = MagicMock(respond=AsyncMock())
    await on_error(ctx, asyncpg.UniqueViolationError("duplicate key"))
    assert "already exists" in ctx.respond.call_args.args[0]


@pytest.mark.asyncio
async def test_one_broken_panel_doesnt_stop_the_refresh():
    from unittest.mock import AsyncMock

    rest = AsyncMock()
    rest.edit_message.side_effect = [hikari.BadRequestError("", {}, b""), None]
    repo = AsyncMock()
    repo.list_types.return_value = _types(1)
    panel = {"guild_id": 1, "channel_id": 2, "title": "Help", "description": None,
             "style": "buttons", "type_ids": [1]}
    repo.list_panels.return_value = [{**panel, "id": 1, "message_id": 10}, {**panel, "id": 2, "message_id": 11}]
    await _service(rest, repo).refresh_panels(1)
    assert rest.edit_message.await_count == 2


@pytest.mark.asyncio
async def test_duplicate_types_on_a_panel_are_listed_once():
    from unittest.mock import AsyncMock

    from serenity.extensions.tickets import resolve_type_list

    service = AsyncMock()
    service.resolve_type.side_effect = lambda _, name: {"id": {"a": 1, "b": 2}[name]}
    assert await resolve_type_list(service, 1, "a, b, a") == [1, 2]


@pytest.mark.asyncio
async def test_tickets_module_cant_be_enabled_outside_the_allowlist(monkeypatch):
    from unittest.mock import AsyncMock

    from serenity.core.modules import ModuleManager, ModuleType
    from serenity.utils.errors import ConfigurationError

    repo = AsyncMock()
    manager = ModuleManager(repo)
    monkeypatch.setenv("TICKETS_GUILDS", "1")
    await manager.enable_module(1, ModuleType.TICKETS)
    with pytest.raises(ConfigurationError, match="isn't available"):
        await manager.enable_module(2, ModuleType.TICKETS)
    repo.set_module_enabled.assert_awaited_once()


@pytest.mark.asyncio
async def test_panels_are_edited_only_when_the_support_status_changes():
    from unittest.mock import AsyncMock, MagicMock

    repo = AsyncMock()
    repo.list_types.return_value = _types(1)
    repo.panel_guilds.return_value = [1]
    repo.list_panels.return_value = [{"id": 1, "guild_id": 1, "channel_id": 2, "message_id": 10, "title": "Help",
                                      "description": None, "style": "buttons", "type_ids": [1]}]
    service = _service(repo=repo)
    settings: dict = {"away": {"until": None, "note": None}}
    service.modules.get_module_config.return_value = MagicMock(settings=settings)

    await service.refresh_panel_statuses()
    await service.refresh_panel_statuses()
    assert service.rest.edit_message.await_count == 1
    assert service.rest.edit_message.call_args.kwargs["embed"].fields[0].value == "🔴 Offline"

    settings.pop("away")
    settings["schedule"] = [[[0, 1440]]] * 7
    service.rest.edit_message.side_effect = [hikari.BadRequestError("", {}, b""), None]
    await service.refresh_panel_statuses()  # the edit fails, so the next sweep tries again
    await service.refresh_panel_statuses()
    await service.refresh_panel_statuses()
    assert service.rest.edit_message.await_count == 3
    assert service.rest.edit_message.call_args.kwargs["embed"].fields[0].value == "🟢 Online"
    service.rest.edit_message.side_effect = None

    # An away mode that ended by itself, with no schedule: the line goes.
    settings.pop("schedule")
    settings["away"] = {"until": 1, "note": None}
    await service.refresh_panel_statuses()
    assert service.rest.edit_message.await_count == 4
    assert service.rest.edit_message.call_args.kwargs["embed"].fields == []

    # After a restart nothing is known, so every guild is reconciled once, unconfigured or not.
    service._panel_status.clear()
    await service.refresh_panel_statuses()
    await service.refresh_panel_statuses()
    assert service.rest.edit_message.await_count == 5


@pytest.mark.asyncio
async def test_one_broken_guild_doesnt_stop_the_panel_status_sweep():
    from unittest.mock import AsyncMock, MagicMock

    repo = AsyncMock()
    repo.panel_guilds.return_value = [1, 2]
    repo.list_panels.return_value = []
    service = _service(repo=repo)
    service.modules.get_module_config.side_effect = [
        MagicMock(settings={"schedule": [[]] * 7, "timezone": "Not/AZone"}),
        MagicMock(settings={}),
    ]
    await service.refresh_panel_statuses()
    repo.list_panels.assert_awaited_once_with(2)
