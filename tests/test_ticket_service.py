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


@pytest.mark.asyncio
async def test_duplicate_type_name_is_a_friendly_error():
    from unittest.mock import AsyncMock, MagicMock

    import asyncpg

    from serenity.extensions.tickets import on_error

    ctx = MagicMock(respond=AsyncMock())
    await on_error(ctx, asyncpg.UniqueViolationError("duplicate key"))
    assert "already exists" in ctx.respond.call_args.args[0]
