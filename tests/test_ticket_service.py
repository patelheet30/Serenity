"""Ticket helpers that don't need Discord or Postgres."""

import hikari
import pytest

from serenity.services.ticket_service import TicketError, is_staff, parse_questions


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


def _service(rest=None, repo=None):
    from unittest.mock import AsyncMock, MagicMock

    from hikari.impl import MessageActionRowBuilder

    from serenity.services.ticket_service import TicketService

    rest = rest or AsyncMock()
    rest.build_message_action_row = MagicMock(side_effect=MessageActionRowBuilder)
    return TicketService(repo or AsyncMock(), rest, MagicMock(), AsyncMock())


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
    panel = {"id": 9, "guild_id": 1, "channel_id": 2, "message_id": 3, "title": "Help",
             "description": None, "style": "buttons", "type_ids": [1]}

    await service.post_panel(panel)
    repo.update_panel.assert_awaited_once_with(1, 9, message_id=555)
    assert service.panel_messages == {555}

    await service.panel_message_deleted(555)
    repo.forget_panel_message.assert_awaited_once_with(555)
    await service.panel_message_deleted(1234)  # not a panel: no query
    assert repo.forget_panel_message.await_count == 1


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
