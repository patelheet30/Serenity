"""Ticket flows end to end on Postgres (set TEST_DATABASE_URL), with Discord mocked."""

import asyncio
import itertools
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest
from hikari.impl import MessageActionRowBuilder

from serenity.core.modules import ModuleManager
from serenity.database.repository import Repository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.ticket_service import TicketError, TicketService

GUILD = 1_103_276_413_850_812_416
STAFF_ROLE = 1_241_890_335_472_164_906
ALICE = 1_029_384_756_102_938_475
BOB = 1_029_384_756_102_938_476


def member(user_id: int, name: str) -> MagicMock:
    m = MagicMock(id=user_id, username=name, role_ids=[])
    m.mention = f"<@{user_id}>"
    return m


@pytest.fixture
def service(pg_repo: Repository) -> TicketService:
    assert pg_repo.pool is not None
    ids = itertools.count(1_300_000_000_000_000_000)
    rest = AsyncMock()
    rest.build_message_action_row = MagicMock(side_effect=MessageActionRowBuilder)

    async def create_channel(*_, **__):
        await asyncio.sleep(0)  # let the other opener run
        return MagicMock(id=next(ids))

    rest.create_guild_text_channel.side_effect = create_channel
    cache = MagicMock()
    cache.get_me.return_value = None
    return TicketService(TicketRepository(pg_repo.pool), rest, cache, ModuleManager(pg_repo))


@pytest.mark.asyncio
async def test_simultaneous_opens_get_distinct_numbers_and_channels(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    a, b = await asyncio.gather(
        service.open_ticket(GUILD, member(ALICE, "alice"), support, {}),
        service.open_ticket(GUILD, member(BOB, "bob"), support, {}),
    )
    assert {a["number"], b["number"]} == {1, 2}
    assert a["channel_id"] != b["channel_id"]
    assert service.open_channels == {a["channel_id"], b["channel_id"]}

    names = sorted(c.args[1] for c in service.rest.create_guild_text_channel.call_args_list)
    assert names == ["support-1", "support-2"]
    welcome = service.rest.create_message.call_args_list[0]
    assert f"<@&{STAFF_ROLE}>" in welcome.args[1]


@pytest.mark.asyncio
async def test_limits_and_blacklist_create_nothing(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support")
    await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})

    with pytest.raises(TicketError, match="already have an open"):
        await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})

    await service.repo.blacklist_add(GUILD, BOB, ALICE, None)
    with pytest.raises(TicketError, match="can't open tickets"):
        await service.open_ticket(GUILD, member(BOB, "bob"), support, {})

    assert service.rest.create_guild_text_channel.await_count == 1
    assert await service.repo.get_ticket(GUILD, 2) is None


@pytest.mark.asyncio
async def test_failed_channel_creation_rolls_the_ticket_back(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support")
    service.rest.create_guild_text_channel.side_effect = hikari.ForbiddenError("", {}, b"")

    with pytest.raises(TicketError, match="Manage Channels"):
        await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    assert await service.repo.count_open(GUILD, ALICE, support["id"]) == 0


@pytest.mark.asyncio
async def test_full_category_overflows_into_a_new_one(service: TicketService):
    base = MagicMock(id=10, type=hikari.ChannelType.GUILD_CATEGORY, position=3,
                     permission_overwrites={}, parent_id=None)
    base.name = "Support"
    children = [MagicMock(id=100 + i, parent_id=10, type=hikari.ChannelType.GUILD_TEXT)
                for i in range(50)]
    service.cache.get_guild_channels_view_for_guild.return_value = {
        c.id: c for c in [base, *children]
    }
    service.rest.create_guild_category.return_value = MagicMock(id=11)
    support = await service.repo.create_type(GUILD, "Support", category_id=10)

    await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    assert service.rest.create_guild_category.call_args.args[1] == "Support 2"
    assert service.rest.create_guild_text_channel.call_args.kwargs["category"] == 11


def message(channel_id: int, author: MagicMock) -> MagicMock:
    return MagicMock(channel_id=channel_id, author_id=author.id, member=author, is_bot=False)


def staff(user_id: int, name: str) -> MagicMock:
    m = member(user_id, name)
    m.role_ids = [STAFF_ROLE]
    return m


CAROL = 1_029_384_756_102_938_477


@pytest.mark.asyncio
async def test_first_staff_reply_claims_and_everyone_who_replied_is_credited(
    service: TicketService, monkeypatch: pytest.MonkeyPatch
):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.NONE)
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    channel = ticket["channel_id"]

    await service.on_message(message(channel, member(ALICE, "alice")))  # opener: no claim
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["assignee_id"] is None  # type: ignore[index]

    await service.on_message(message(channel, staff(BOB, "bob")))
    after = await service.repo.get_ticket_by_id(ticket["id"])
    assert after is not None and after["assignee_id"] == BOB
    assert after["waiting_on"] == "opener" and after["first_response_at"] is not None

    await service.on_message(message(channel, staff(CAROL, "carol")))  # assists, doesn't claim
    await service.on_message(message(channel, staff(BOB, "bob")))
    # Claimed by an admin's reassign but never wrote: in the chain, not credited.
    dave = 1_029_384_756_102_938_478
    await service.claim(after, dave, by=BOB, announce=False)

    await service.repo.credit_handlers(ticket["id"])
    handlers = {h["user_id"]: h for h in await service.repo.list_handlers(ticket["id"])}
    assert {u: (h["role"], h["message_count"], h["credited"]) for u, h in handlers.items()} == {
        BOB: ("claimed", 2, True),
        CAROL: ("assisted", 1, True),
        dave: ("claimed", 0, False),
    }


@pytest.mark.asyncio
async def test_claim_button_mode_needs_an_explicit_claim(service: TicketService, monkeypatch):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.NONE)
    support = await service.repo.create_type(
        GUILD, "Support", staff_role_ids=[STAFF_ROLE], claim_mode="button"
    )
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    await service.on_message(message(ticket["channel_id"], staff(BOB, "bob")))
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["assignee_id"] is None  # type: ignore[index]

    await service.claim(ticket, CAROL)
    with pytest.raises(TicketError, match="already has"):
        await service.claim(ticket, BOB)


@pytest.mark.asyncio
async def test_auto_unclaim_after_silence(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support", auto_unclaim_hours=2)
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    await service.claim(ticket, BOB, announce=False)
    await service.repo.record_message(ticket["id"], ALICE, staff=False)  # opener waiting

    claimed = await service.repo.get_ticket_by_id(ticket["id"])
    assert claimed is not None
    await service.auto_unclaim(claimed["last_activity_at"] + 3600)
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["assignee_id"] == BOB  # type: ignore[index]

    await service.auto_unclaim(claimed["last_activity_at"] + 2 * 3600 + 1)
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["assignee_id"] is None  # type: ignore[index]
    assert "No staff reply for 2h" in service.rest.create_message.call_args.args[1]


@pytest.mark.asyncio
async def test_auto_unclaim_spares_a_ticket_reassigned_meanwhile(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support", auto_unclaim_hours=1)
    first = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    second = await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    for t in (first, second):
        await service.claim(t, CAROL, announce=False)
    later = int(__import__("time").time()) + 7200
    due = await service.repo.due_for_unclaim(later)
    assert {t["id"] for t in due} == {first["id"], second["id"]}

    # first is reassigned between the sweep's SELECT and its UPDATE; second's channel is gone.
    await service.repo.claim(first["id"], ALICE, force=True)
    service.repo.due_for_unclaim = AsyncMock(return_value=due)
    service.rest.create_message.side_effect = hikari.NotFoundError("", {}, b"")
    await service.auto_unclaim(later)

    assert (await service.repo.get_ticket_by_id(first["id"]))["assignee_id"] == ALICE  # type: ignore[index]
    assert (await service.repo.get_ticket_by_id(second["id"]))["assignee_id"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_failed_welcome_still_opens_and_failed_link_rolls_back(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support")
    service.rest.create_message.side_effect = hikari.ForbiddenError("", {}, b"")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    assert ticket["channel_id"] in service.open_channels

    service.rest.create_message.side_effect = None
    service.repo.set_ticket_channel = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    service.rest.delete_channel.assert_awaited_once()
    assert await service.repo.count_open(GUILD, BOB, support["id"]) == 0
