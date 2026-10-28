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


@pytest.mark.asyncio
async def test_failed_welcome_still_opens_and_failed_link_rolls_back(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support")
    service.rest.create_message.side_effect = hikari.ForbiddenError("", {}, b"")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    assert ticket["channel_id"] in service.open_channels

    # A non-Discord failure while building the welcome (the settings read) is tolerated too.
    service.rest.create_message.side_effect = None
    service.retention_days = AsyncMock(side_effect=ValueError("bad setting"))
    carol = await service.open_ticket(GUILD, member(ALICE + 5, "carol"), support, {})
    assert carol["channel_id"] in service.open_channels

    service.rest.create_message.side_effect = None
    service.repo.set_ticket_channel = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    service.rest.delete_channel.assert_awaited_once()
    assert await service.repo.count_open(GUILD, BOB, support["id"]) == 0

    # Even if removing the channel fails too, the ticket row goes and the original error surfaces.
    service.rest.delete_channel.side_effect = hikari.ForbiddenError("", {}, b"")
    with pytest.raises(RuntimeError):
        await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    assert await service.repo.count_open(GUILD, BOB, support["id"]) == 0
