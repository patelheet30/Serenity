"""Ticket flows end to end on Postgres (set TEST_DATABASE_URL), with Discord mocked."""

import asyncio
import itertools
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest
from hikari.impl import MessageActionRowBuilder

from serenity.core.modules import ModuleManager, ModuleType
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


class FakeHistory:
    def __init__(self, messages):
        self.messages = messages

    def limit(self, _):
        return self

    def __aiter__(self):
        async def gen():
            for m in self.messages:
                yield m

        return gen()


def chat(message_id: int, author_id: int, name: str, content: str) -> MagicMock:
    from datetime import datetime, timezone

    author = MagicMock(id=author_id, username=name, display_name=name, is_bot=False)
    author.display_avatar_url = f"https://cdn/{author_id}.png"
    return MagicMock(
        id=message_id, author=author, content=content, embeds=[], attachments=[],
        timestamp=datetime(2026, 10, 28, tzinfo=timezone.utc), edited_timestamp=None,
    )


@pytest.mark.asyncio
async def test_close_stores_transcript_credits_and_cleans_up(service: TicketService):
    from serenity.services.ticket_service import render_transcript

    service.cache.get_member.return_value = None
    support = await service.repo.create_type(GUILD, "Support", fields=[
        {"key": "order", "label": "Order", "style": "short", "required": True}
    ])
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {"order": "A1"})
    await service.repo.record_message(ticket["id"], BOB, staff=True)
    await service.repo.add_note(ticket["id"], BOB, "refund approved")
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory(
        [chat(1, ALICE, "alice", "it broke"), chat(2, BOB, "bob", "fixed")]
    ))

    closed = await service.close(ticket, support, BOB, "fixed")
    assert closed["status"] == "closed" and closed["channel_id"] is None
    assert closed["close_reason"] == "fixed" and closed["closed_by"] == BOB
    service.rest.delete_channel.assert_awaited_once()
    assert ticket["channel_id"] not in service.open_channels
    assert (await service.repo.list_handlers(ticket["id"]))[0]["credited"] is True

    transcript = await service.repo.get_transcript(ticket["id"])
    assert transcript is not None
    assert transcript["expires_at"] == closed["closed_at"] + 30 * 86400
    doc = transcript["document"]
    assert [m["content"] for m in doc["messages"]] == ["it broke", "fixed"]
    assert doc["fields"] == {"Order": "A1"} and doc["notes"][0]["body"] == "refund approved"
    assert "refund approved" not in render_transcript(doc, staff=False)
    assert "refund approved" in render_transcript(doc, staff=True)

    dm = service.rest.create_message.call_args
    assert dm.kwargs["attachment"].filename == "ticket-1.txt"
    with pytest.raises(TicketError, match="already closed"):
        await service.close(ticket, support, BOB, None)


@pytest.mark.asyncio
async def test_transcript_retention_and_holds(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support", retention_days=0)
    kept = await service.repo.create_ticket(GUILD, support["id"], ALICE, {})
    expired = await service.repo.create_ticket(GUILD, support["id"], ALICE, {})
    held = await service.repo.create_ticket(GUILD, support["id"], ALICE, {})
    await service.repo.save_transcript(kept["id"], GUILD, {}, None)
    await service.repo.save_transcript(expired["id"], GUILD, {}, 100)
    await service.repo.save_transcript(held["id"], GUILD, {}, 100)
    await service.repo.hold_transcript(held["id"], 7)
    for t in (kept, expired, held):
        await service.repo.add_note(t["id"], BOB, "note")

    assert await service.repo.purge_expired_transcripts(now=200) == 1
    assert await service.repo.get_transcript(expired["id"]) is None
    assert await service.repo.list_notes(expired["id"]) == []  # notes go with the transcript
    assert len(await service.repo.list_notes(held["id"])) == 1

    assert await service.repo.get_transcript(kept["id"]) is not None
    assert await service.repo.get_transcript(held["id"]) is not None
    assert await service.retention_days(GUILD, support) == 0
    assert await service.repo.delete_transcript(kept["id"])
    assert await service.repo.list_notes(kept["id"]) == []


@pytest.mark.asyncio
async def test_reopen_restores_and_keeps_earlier_messages(service: TicketService, monkeypatch):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.MANAGE_GUILD)
    service.cache.get_member.return_value = None
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([chat(1, ALICE, "alice", "first")]))
    await service.close(ticket, support, ALICE, None)

    reopened = await service.reopen(GUILD, 1, staff(BOB, "bob"))
    assert reopened["status"] == "open" and reopened["channel_id"] in service.open_channels
    assert (await service.repo.get_transcript(ticket["id"]))["expires_at"] is None  # type: ignore[index]

    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([chat(5, ALICE, "alice", "again")]))
    await service.close(reopened, support, BOB, None)
    doc = (await service.repo.get_transcript(ticket["id"]))["document"]  # type: ignore[index]
    assert [m["content"] for m in doc["messages"]] == ["first", "again"]

    await service.repo.pool.execute("UPDATE tickets SET closed_at = closed_at - 8 * 86400")
    with pytest.raises(TicketError, match="more than 7 days"):
        await service.reopen(GUILD, 1, staff(BOB, "bob"))


@pytest.mark.asyncio
async def test_ticket_channels_never_get_auto_slowmode(monkeypatch):
    from serenity.core.types import GuildConfig
    from serenity.extensions import tasks

    ticket_channel, normal_channel = 501, 502
    tasks._LAST_SCORED.clear()
    client = MagicMock()
    client.app.cache.get_available_guilds_view.return_value = {GUILD: None}
    client.app.cache.get_guild_channel.side_effect = lambda _: MagicMock(spec=hikari.GuildTextChannel)
    client.get_type_dependency.return_value = MagicMock(open_channels={ticket_channel})
    repo = AsyncMock()
    repo.get_guild_config.return_value = GuildConfig(GUILD, True, 10, 1)
    repo.get_enabled_channels.return_value = [ticket_channel, normal_channel]
    engine = AsyncMock()
    engine.calculate_with_current.side_effect = RuntimeError("stop here")
    monkeypatch.setattr(tasks.asyncio, "sleep", AsyncMock())

    await tasks.update_slowmode._coro(client=client, repo=repo, engine=engine)
    assert [c.args[0] for c in engine.calculate_with_current.call_args_list] == [normal_channel]


@pytest.mark.asyncio
async def test_ticket_events_go_to_the_ticket_log_and_respect_ignores(pg_repo: Repository):
    from serenity.database.logging_repository import LoggingRepository
    from serenity.services.logging_service import LoggingService

    assert pg_repo.pool is not None
    log_repo = LoggingRepository(pg_repo.pool)
    await log_repo.set_log_channel(GUILD, "ticket", 900)
    rest = AsyncMock()
    logging = LoggingService(log_repo, rest)

    await logging.log_ticket_event(
        guild_id=GUILD, kind="opened", number=1, type_name="Support", actor_id=ALICE, channel_id=5
    )
    assert rest.create_message.call_args.args[0] == 900
    assert "Ticket opened | #1" in rest.create_message.call_args.kwargs["embed"].title

    await log_repo.add_ignore(GUILD, "user", BOB, "ticket")
    await logging.log_ticket_event(
        guild_id=GUILD, kind="closed", number=1, type_name="Support", actor_id=BOB
    )
    assert rest.create_message.await_count == 1


@pytest.mark.asyncio
async def test_ticket_flows_are_logged(service: TicketService):
    service.logging = AsyncMock()
    service.cache.get_member.return_value = None
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    await service.claim(ticket, BOB, announce=False)
    await service.close(ticket, support, BOB, "done")
    assert await service.request_transcript_deletion(ticket["id"], ALICE)
    assert not await service.request_transcript_deletion(ticket["id"], ALICE)
    kinds = [c.kwargs["kind"] for c in service.logging.log_ticket_event.call_args_list]
    assert kinds == ["opened", "claimed", "closed", "deletion_requested"]
    closed_event = service.logging.log_ticket_event.call_args_list[2].kwargs
    assert closed_event["channel_id"] == ticket["channel_id"]  # for channel ignores


@pytest.mark.asyncio
async def test_transfer_changes_who_can_see_the_ticket(service: TicketService):
    other_role = 1_241_890_335_472_164_907
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    billing = await service.repo.create_type(GUILD, "Billing", staff_role_ids=[other_role])
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    await service.claim(ticket, BOB, announce=False)
    guest = hikari.PermissionOverwrite(
        id=hikari.Snowflake(CAROL), type=hikari.PermissionOverwriteType.MEMBER,
        allow=hikari.Permissions.VIEW_CHANNEL,
    )
    opener = hikari.PermissionOverwrite(
        id=hikari.Snowflake(ALICE), type=hikari.PermissionOverwriteType.MEMBER,
        allow=hikari.Permissions.VIEW_CHANNEL,
    )
    service.cache.get_guild_channel.return_value = MagicMock(
        permission_overwrites={ALICE: opener, CAROL: guest}
    )
    service.cache.get_member.return_value = None

    moved = await service.transfer(ticket, support, billing, BOB)
    assert moved["type_id"] == billing["id"]
    move, rename = (c.kwargs for c in service.rest.edit_channel.call_args_list)
    assert rename["name"] == "billing-1"
    edit = move
    roles = {o.id for o in edit["permission_overwrites"] if o.type == hikari.PermissionOverwriteType.ROLE}
    users = {o.id for o in edit["permission_overwrites"] if o.type == hikari.PermissionOverwriteType.MEMBER}
    assert roles == {GUILD, other_role} and users == {ALICE, CAROL}
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["assignee_id"] is None  # type: ignore[index]
    with pytest.raises(TicketError, match="already"):
        await service.transfer(moved, billing, billing, BOB)


@pytest.mark.asyncio
async def test_priority_renames_and_opener_cant_be_removed(service: TicketService):
    service.cache.get_member.return_value = None
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})

    updated = await service.set_priority(ticket, support, "urgent", BOB)
    assert updated["priority"] == "urgent"
    assert service.rest.edit_channel.call_args.kwargs["name"] == "urgent-support-1"
    await service.set_priority(updated, support, "normal", BOB)
    assert service.rest.edit_channel.call_args.kwargs["name"] == "support-1"

    with pytest.raises(TicketError, match="opener"):
        await service.remove_member(ticket, ALICE, BOB)
    await service.add_member(ticket, support, CAROL, BOB)
    await service.remove_member(ticket, CAROL, BOB)
    kinds = [e["kind"] for e in await service.repo.list_events(ticket["id"])]
    assert kinds == ["opened", "priority", "priority", "member_added", "member_removed"]




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
async def test_concurrent_reopens_create_one_channel(service: TicketService, monkeypatch):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.MANAGE_GUILD)
    service.cache.get_member.return_value = None
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    await service.close(ticket, support, ALICE, None)
    created = service.rest.create_guild_text_channel.await_count

    results = await asyncio.gather(
        service.reopen(GUILD, 1, staff(BOB, "bob")),
        service.reopen(GUILD, 1, staff(CAROL, "carol")),
        return_exceptions=True,
    )
    assert sum(isinstance(r, TicketError) for r in results) == 1
    assert service.rest.create_guild_text_channel.await_count == created + 1


@pytest.mark.asyncio
async def test_close_survives_a_channel_deleted_by_hand(service: TicketService):
    service.cache.get_member.return_value = None
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})

    class Gone(FakeHistory):
        def __aiter__(self):
            async def gen():
                raise hikari.NotFoundError("", {}, b"")
                yield

            return gen()

    service.rest.fetch_messages = MagicMock(return_value=Gone([]))
    service.rest.delete_channel.side_effect = hikari.InternalServerError("", 500, {}, b"")
    closed = await service.close(ticket, support, ALICE, None)
    assert closed["status"] == "closed"
    assert (await service.repo.get_transcript(ticket["id"]))["document"]["messages"] == []  # type: ignore[index]


@pytest.mark.asyncio
async def test_priority_survives_reopen_and_rolls_back_without_permission(service: TicketService, monkeypatch):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.MANAGE_GUILD)
    service.cache.get_member.return_value = None
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    urgent = await service.set_priority(ticket, support, "urgent", BOB)

    service.rest.edit_channel.side_effect = hikari.ForbiddenError("", {}, b"")
    with pytest.raises(TicketError, match="Manage Channels"):
        await service.set_priority(urgent, support, "low", BOB)
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["priority"] == "urgent"  # type: ignore[index]

    await service.close(urgent, support, BOB, None)
    await service.reopen(GUILD, 1, staff(BOB, "bob"))
    assert service.rest.create_guild_text_channel.call_args.args[1] == "urgent-support-1"




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


@pytest.mark.asyncio
async def test_transfer_reads_overwrites_from_discord_on_a_cache_miss(service: TicketService):
    other_role = 1_241_890_335_472_164_907
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    billing = await service.repo.create_type(GUILD, "Billing", staff_role_ids=[other_role])
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    guest = hikari.PermissionOverwrite(
        id=hikari.Snowflake(CAROL), type=hikari.PermissionOverwriteType.MEMBER,
        allow=hikari.Permissions.VIEW_CHANNEL,
    )
    service.cache.get_guild_channel.return_value = None
    service.cache.get_member.return_value = None
    opener = hikari.PermissionOverwrite(
        id=hikari.Snowflake(ALICE), type=hikari.PermissionOverwriteType.MEMBER,
        allow=hikari.Permissions.VIEW_CHANNEL,
    )
    service.rest.fetch_channel.return_value = MagicMock(
        permission_overwrites={ALICE: opener, CAROL: guest}
    )

    await service.transfer(ticket, support, billing, BOB)
    move = service.rest.edit_channel.call_args_list[0].kwargs
    users = {o.id for o in move["permission_overwrites"] if o.type == hikari.PermissionOverwriteType.MEMBER}
    assert users == {ALICE, CAROL}  # the opener and the guest both keep access

    service.rest.fetch_channel.side_effect = hikari.NotFoundError("", {}, b"")
    moved = await service.repo.get_ticket_by_id(ticket["id"])
    with pytest.raises(TicketError, match="can't read this channel"):
        await service.transfer(moved, billing, support, BOB)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_off_hours_auto_reply_and_types_closed_outside_hours(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support")
    urgent = await service.repo.create_type(GUILD, "Urgent", open_off_hours=False)
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    assert "off_hours_notice" not in ticket  # nothing configured: always open

    await service.modules.get_module_config(GUILD, ModuleType.TICKETS)
    await service.modules.update_module_settings(
        GUILD, ModuleType.TICKETS, {"away": {"until": None, "note": "Back after the holidays"}}
    )
    ticket = await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    notice = ticket["off_hours_notice"]
    assert f"<@{BOB}>" in notice and "Back after the holidays" in notice
    assert service.rest.create_message.call_args.args == (ticket["channel_id"], notice)

    # Away mode alone doesn't refuse a type that's closed outside hours: the schedule decides.
    opened = await service.open_ticket(GUILD, member(ALICE, "alice"), urgent, {})
    assert "Back after the holidays" in opened["off_hours_notice"]

    from datetime import datetime, timezone

    tomorrow_only: list = [[] for _ in range(7)]
    tomorrow_only[(datetime.now(timezone.utc).weekday() + 1) % 7] = [[0, 1440]]
    urgent = await service.repo.update_type(GUILD, urgent["id"], schedule=tomorrow_only)
    with pytest.raises(TicketError, match="only be opened during support hours"):
        await service.open_ticket(GUILD, member(CAROL, "carol"), urgent, {})  # type: ignore[arg-type]
