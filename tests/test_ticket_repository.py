"""Ticket repository against Postgres (set TEST_DATABASE_URL), with realistic snowflakes."""

import asyncio

import asyncpg
import pytest

from serenity.database.repository import Repository
from serenity.database.ticket_repository import TicketRepository

GUILD = 1_103_276_413_850_812_416
OTHER_GUILD = 1_103_276_413_850_812_417
CHANNEL = 1_241_890_335_472_164_904
CATEGORY = 1_241_890_335_472_164_905
STAFF_ROLE = 1_241_890_335_472_164_906
USER = 1_029_384_756_102_938_475
MOD = 1_029_384_756_102_938_476

FIELDS = [{"key": "order", "label": "Order number", "style": "short", "required": True}]


@pytest.fixture
def tickets(pg_repo: Repository) -> TicketRepository:
    assert pg_repo.pool is not None
    return TicketRepository(pg_repo.pool)


@pytest.mark.asyncio
async def test_types_round_trip_json_and_arrays(tickets: TicketRepository):
    created = await tickets.create_type(
        GUILD, "Billing", category_id=CATEGORY, staff_role_ids=[STAFF_ROLE], fields=FIELDS
    )
    assert created["fields"] == FIELDS
    assert created["claim_mode"] == "reply"

    fetched = await tickets.get_type(GUILD, created["id"])
    assert fetched is not None
    assert fetched["staff_role_ids"] == [STAFF_ROLE]
    assert await tickets.get_type(OTHER_GUILD, created["id"]) is None
    assert (await tickets.get_type_by_name(GUILD, "billing"))["id"] == created["id"]  # type: ignore[index]

    updated = await tickets.update_type(GUILD, created["id"], claim_mode="both", fields=[])
    assert updated is not None and updated["claim_mode"] == "both" and updated["fields"] == []

    with pytest.raises(asyncpg.CheckViolationError):
        await tickets.update_type(GUILD, created["id"], retention_days=3)
    with pytest.raises(ValueError):
        await tickets.update_type(GUILD, created["id"], guild_id=OTHER_GUILD)


@pytest.mark.asyncio
async def test_ticket_numbers_are_per_guild_and_concurrent_safe(tickets: TicketRepository):
    support = await tickets.create_type(GUILD, "Support")
    other = await tickets.create_type(OTHER_GUILD, "Support")

    created = await asyncio.gather(
        *(tickets.create_ticket(GUILD, support["id"], USER, {}) for _ in range(10))
    )
    assert sorted(t["number"] for t in created) == list(range(1, 11))
    assert (await tickets.create_ticket(OTHER_GUILD, other["id"], USER, {}))["number"] == 1

    first = await tickets.get_ticket(GUILD, 1)
    assert first is not None and first["status"] == "open" and first["fields"] == {}
    assert [e["kind"] for e in await tickets.list_events(first["id"])] == ["opened"]
    assert await tickets.count_open(GUILD, USER, support["id"]) == 10


@pytest.mark.asyncio
async def test_ticket_channel_lookup(tickets: TicketRepository):
    support = await tickets.create_type(GUILD, "Support")
    ticket = await tickets.create_ticket(GUILD, support["id"], USER, {"order": "A1"})
    assert await tickets.open_ticket_channels() == []

    await tickets.set_ticket_channel(ticket["id"], CHANNEL)
    found = await tickets.get_ticket_by_channel(CHANNEL)
    assert found is not None and found["fields"] == {"order": "A1"}
    assert await tickets.open_ticket_channels() == [CHANNEL]

    # A type with tickets can't be deleted; an unused one can.
    assert not await tickets.delete_type(GUILD, support["id"])
    unused = await tickets.create_type(GUILD, "Unused")
    assert await tickets.delete_type(GUILD, unused["id"])


@pytest.mark.asyncio
async def test_panels(tickets: TicketRepository):
    a = await tickets.create_type(GUILD, "A")
    b = await tickets.create_type(GUILD, "B")
    panel = await tickets.create_panel(GUILD, CHANNEL, "Get help", [a["id"], b["id"]])
    assert panel["message_id"] is None and panel["style"] == "buttons"

    await tickets.update_panel(GUILD, panel["id"], message_id=99)
    forgotten = await tickets.forget_panel_message(99)
    assert forgotten is not None and forgotten["message_id"] is None
    assert [p["id"] for p in await tickets.list_panels(GUILD)] == [panel["id"]]
    assert await tickets.delete_panel(GUILD, panel["id"]) is not None
    assert await tickets.list_panels(GUILD) == []


@pytest.mark.asyncio
async def test_notes_transcripts_and_blacklist(tickets: TicketRepository):
    support = await tickets.create_type(GUILD, "Support")
    ticket = await tickets.create_ticket(GUILD, support["id"], USER, {})

    await tickets.add_note(ticket["id"], MOD, "Refund approved")
    assert [n["body"] for n in await tickets.list_notes(ticket["id"])] == ["Refund approved"]

    await tickets.save_transcript(ticket["id"], GUILD, {"messages": [1]}, None)
    await tickets.save_transcript(ticket["id"], GUILD, {"messages": [1, 2]}, 123)
    transcript = await tickets.get_transcript(ticket["id"])
    assert transcript is not None
    assert transcript["document"] == {"messages": [1, 2]} and transcript["expires_at"] == 123

    assert await tickets.blacklist_add(GUILD, USER, MOD, "spam")
    assert not await tickets.blacklist_add(GUILD, USER, MOD, "again")
    assert (await tickets.get_blacklist_entry(GUILD, USER))["reason"] == "spam"  # type: ignore[index]
    assert await tickets.blacklist_remove(GUILD, USER)
    assert await tickets.get_blacklist_entry(GUILD, USER) is None



@pytest.mark.asyncio
async def test_guild_boundaries_and_name_uniqueness(tickets: TicketRepository):
    ours = await tickets.create_type(GUILD, "Billing")
    theirs = await tickets.create_type(OTHER_GUILD, "Billing")

    with pytest.raises(asyncpg.UniqueViolationError):
        await tickets.create_type(GUILD, "billing")
    with pytest.raises(ValueError):
        await tickets.create_ticket(GUILD, theirs["id"], USER, {})
    with pytest.raises(ValueError):
        await tickets.create_panel(GUILD, CHANNEL, "Help", [ours["id"], theirs["id"]])

    panel = await tickets.create_panel(GUILD, CHANNEL, "Help", [ours["id"]])
    with pytest.raises(ValueError):
        await tickets.update_panel(GUILD, panel["id"], type_ids=[theirs["id"]])
    assert (await tickets.update_panel(GUILD, panel["id"]))["id"] == panel["id"]  # type: ignore[index]

    # A type a panel still shows can't be deleted.
    assert not await tickets.delete_type(GUILD, ours["id"])
    await tickets.delete_panel(GUILD, panel["id"])
    assert await tickets.delete_type(GUILD, ours["id"])


@pytest.mark.asyncio
async def test_update_ticket_with_nothing_to_change(tickets: TicketRepository):
    support = await tickets.create_type(GUILD, "Support")
    ticket = await tickets.create_ticket(GUILD, support["id"], USER, {})
    assert (await tickets.update_ticket(ticket["id"]))["number"] == 1  # type: ignore[index]
