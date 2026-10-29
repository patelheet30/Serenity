"""Ticket flows end to end on Postgres (set TEST_DATABASE_URL), with Discord mocked."""

import asyncio
import itertools
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import hikari
import pytest
from hikari.impl import MessageActionRowBuilder

from serenity.core.modules import ModuleManager, ModuleType
from serenity.database.repository import Repository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.ticket_service import TicketError, TicketService, set_field

GUILD = 1_103_276_413_850_812_416
STAFF_ROLE = 1_241_890_335_472_164_906
ALICE = 1_029_384_756_102_938_475
BOB = 1_029_384_756_102_938_476


def member(user_id: int, name: str) -> MagicMock:
    m = MagicMock(id=user_id, username=name, role_ids=[])
    m.mention = f"<@{user_id}>"
    return m


@pytest.fixture
def service(pg_repo: Repository):
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
    service = TicketService(TicketRepository(pg_repo.pool), rest, cache, ModuleManager(pg_repo))
    yield service
    for task in service._topic_tasks.values():  # pending topic edits, before the pool closes
        task.cancel()


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
async def test_ticket_events_reach_subscribed_webhooks(service: TicketService, monkeypatch):
    support = await service.repo.create_type(
        GUILD, "Support", staff_role_ids=[STAFF_ROLE], transcripts=False, fields=set_field([], "q")
    )
    billing = await service.repo.create_type(GUILD, "Billing", staff_role_ids=[STAFF_ROLE])
    monkeypatch.setattr("serenity.services.ticket_webhooks.check_url", AsyncMock())  # no DNS
    every = await service.webhooks.add(GUILD, "https://hooks.example/all", [], None)
    closes = await service.webhooks.add(GUILD, "https://hooks.example/c", ["ticket.closed"], support["id"])
    await service.webhooks.add(GUILD, "https://hooks.example/b", [], billing["id"])
    assert len(every["secret"]) == 64

    sent = []

    async def post(hook, event, delivery_id, body):
        sent.append((hook["id"], event, json.loads(body)))
        return 200, 12, None

    monkeypatch.setattr(service.webhooks, "post", post)
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {"q": "help"})
    await service.close(ticket, support, BOB, "fixed")
    await asyncio.gather(*service.webhooks._tasks)

    assert sorted((h, e) for h, e, _ in sent) == [
        (every["id"], "ticket.closed"),
        (every["id"], "ticket.opened"),
        (closes["id"], "ticket.closed"),
    ]
    body = next(b for h, e, b in sent if h == closes["id"])
    assert body["ticket"]["number"] == 1 and body["ticket"]["status"] == "closed"
    assert body["ticket"]["fields"] == {"q": "help"} and body["actor_id"] == str(BOB)
    assert body["type"]["name"] == "Support"

    hooks = {h["id"]: h for h in await service.repo.list_webhooks(GUILD)}
    assert hooks[closes["id"]]["last_status"] == 200 and hooks[closes["id"]]["last_error"] is None
    assert await service.repo.purge_webhook_deliveries(2**40) == 3
    assert not await service.repo.remove_webhook(GUILD + 1, closes["id"])  # another server's
    assert await service.repo.remove_webhook(GUILD, closes["id"])


@pytest.mark.asyncio
async def test_opener_rates_once_from_the_close_dm(service: TicketService, monkeypatch):
    from serenity.core.modules import ModuleType

    await service.modules.enable_module(GUILD, ModuleType.TICKETS)
    service.logging = AsyncMock()
    service.cache.get_member.return_value = None
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    with pytest.raises(TicketError, match="reopened"):  # still open
        await service.rate(ticket["id"], ALICE, 5, None)
    await service.repo.record_message(ticket["id"], BOB, staff=True)
    await service.close(ticket, support, BOB, None)

    rows = service.rest.create_message.call_args.kwargs["components"]
    ids = [c.custom_id for c in rows[0].components]
    assert ids == [f"ticket:rate:{ticket['id']}:{n}" for n in range(1, 6)]

    with pytest.raises(TicketError, match="Only the person who opened"):
        await service.rate(ticket["id"], BOB, 5, None)
    stored = await service.rate(ticket["id"], ALICE, 2, "slow reply")
    assert stored["rating"] == 2 and stored["handler_ids"] == [BOB]
    alert = service.logging.log_ticket_event.call_args.kwargs
    assert alert["kind"] == "low_rating" and f"<@{BOB}>" in alert["detail"] and "slow reply" in alert["detail"]
    with pytest.raises(TicketError, match="already rated"):
        await service.rate(ticket["id"], ALICE, 5, None)
    assert (await service.repo.get_rating(ticket["id"]))["rating"] == 2  # type: ignore[index]

    # Past the window, and types with ratings off: no buttons, and no rating.
    late = await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    await service.close(late, support, BOB, None)
    await service.repo.pool.execute("UPDATE tickets SET closed_at = closed_at - 8 * 86400 WHERE id = $1", late["id"])
    with pytest.raises(TicketError, match="for 7 days"):  # the default window
        await service.rate(late["id"], BOB, 4, None)
    with pytest.raises(TicketError, match="from 1 to 5"):
        await service.rate(late["id"], BOB, 6, None)
    with pytest.raises(asyncpg.CheckViolationError):  # and the table holds the line too
        await service.repo.add_rating(late["id"], BOB, 6, None)
    await service.modules.update_module_settings(GUILD, ModuleType.TICKETS, {"rating_window_days": 10})
    assert (await service.rate(late["id"], BOB, 4, None))["rating"] == 4  # /ticket settings ratings
    appeals = await service.repo.create_type(GUILD, "Appeals", ratings=False)
    appeal = await service.open_ticket(GUILD, member(ALICE, "alice"), appeals, {})
    await service.close(appeal, appeals, ALICE, None)
    assert all("rate" not in c.custom_id for r in service.rest.create_message.call_args.kwargs["components"]
               for c in r.components)
    with pytest.raises(TicketError, match="aren't rated"):
        await service.rate(appeal["id"], ALICE, 4, None)

    # A failed rating lookup costs the buttons, not the close.
    monkeypatch.setattr(service.repo, "get_rating", AsyncMock(side_effect=asyncpg.PostgresError()))
    last = await service.open_ticket(GUILD, member(CAROL, "carol"), support, {})
    monkeypatch.setenv("DASHBOARD_URL", "https://dash.example/")  # links the transcript viewer
    await service.close(last, support, CAROL, None)
    rows = service.rest.create_message.call_args.kwargs["components"]
    delreq, view = rows[0].components
    assert delreq.custom_id == f"ticket:delreq:{last['id']}" and len(rows) == 1
    assert view.url == f"https://dash.example/transcripts/{last['id']}"
    assert service.logging.log_ticket_event.call_args.kwargs["kind"] == "closed"


@pytest.mark.asyncio
async def test_cooldown_and_requirements_refuse_opens(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support", open_limit=5)
    await service.update_settings(GUILD, open_cooldown_minutes=10)
    await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    with pytest.raises(TicketError, match="open another ticket <t:"):
        await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})

    await service.update_settings(GUILD, min_account_age_days=7, required_role_id=STAFF_ROLE)
    bob = member(BOB, "bob")
    with pytest.raises(TicketError, match="role to open tickets"):
        await service.check_can_open(GUILD, bob, support)
    bob.role_ids = [STAFF_ROLE]
    bob.created_at = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(TicketError, match="at least 7 days old"):
        await service.check_can_open(GUILD, bob, support)
    bob.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    await service.check_can_open(GUILD, bob, support)


@pytest.mark.asyncio
async def test_a_burst_of_opens_trips_the_raid_brake(service: TicketService):
    service.logging = AsyncMock()
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    await service.update_settings(GUILD, raid_limit=2, raid_window_minutes=5)
    for i in range(2):
        await service.open_ticket(GUILD, member(ALICE + 10 + i, f"user{i}"), support, {})

    with pytest.raises(TicketError, match="paused"):
        await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    service.logging.log_ticket_raid_brake.assert_awaited_once()
    assert service.logging.log_ticket_raid_brake.await_args.kwargs["role_ids"] == [STAFF_ROLE]
    # Stays paused, without alerting again, until staff resume.
    with pytest.raises(TicketError, match="paused"):
        await service.check_can_open(GUILD, member(BOB, "bob"), support)
    service.logging.log_ticket_raid_brake.assert_awaited_once()
    assert await service.repo.get_ticket(GUILD, 3) is None

    # Resuming counts afresh, so the burst still in the window doesn't trip it again.
    await service.resume_opening(GUILD)
    await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    service.logging.log_ticket_raid_brake.assert_awaited_once()


@pytest.mark.asyncio
async def test_raid_brake_without_a_ticket_log_refuses_but_does_not_latch(service: TicketService):
    support = await service.repo.create_type(GUILD, "Support")
    await service.update_settings(GUILD, cooldown_unrelated=1)
    await service.update_settings(GUILD, raid_limit=1)
    assert (await service.settings(GUILD))["cooldown_unrelated"] == 1  # merged, not replaced
    await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    with pytest.raises(TicketError, match="paused"):
        await service.open_ticket(GUILD, member(BOB, "bob"), support, {})
    assert not (await service.settings(GUILD)).get("raid_paused")


T2_ROLE = 1_241_890_335_472_164_910
T3_ROLE = 1_241_890_335_472_164_911
DEV_ROLE = 1_241_890_335_472_164_912


async def ladder(service: TicketService) -> dict:
    """Support with T1 (its staff) → T2 → T3, and Developers off to the side."""
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    t1 = await service.repo.create_tier(support["id"], name="T1", position=1, role_ids=[STAFF_ROLE])
    await service.repo.create_tier(support["id"], name="T2", position=2, role_ids=[T2_ROLE], reping_minutes=30)
    await service.repo.create_tier(support["id"], name="T3", position=3, role_ids=[T3_ROLE], requires_reason=True)
    dev = await service.repo.create_tier(support["id"], name="Developers", position=9, role_ids=[DEV_ROLE])
    await service.repo.update_tier(t1["id"], can_summon=[dev["id"]])
    return support


def in_role(user_id: int, name: str, role: int) -> MagicMock:
    m = member(user_id, name)
    m.role_ids = [role]
    return m


@pytest.mark.asyncio
async def test_t1_t2_t3_escalation_credits_all_three(service: TicketService, monkeypatch):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.NONE)
    support = await ladder(service)
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    channel = ticket["channel_id"]
    dave = 1_029_384_756_102_938_478

    await service.on_message(message(channel, staff(BOB, "bob")))  # T1 claims by replying
    current, full = await service.ticket_here(channel)
    current = await service.escalate(current, full, BOB, None, None)
    overwrite = service.rest.edit_permission_overwrite.call_args
    assert overwrite.args[1] == T2_ROLE and overwrite.kwargs["target_type"] == hikari.PermissionOverwriteType.ROLE
    ping = service.rest.create_message.call_args
    assert f"<@&{T2_ROLE}>" in ping.args[1] and ping.kwargs["role_mentions"] == [T2_ROLE]

    await service.on_message(message(channel, in_role(CAROL, "carol", T2_ROLE)))  # T2 picks it up
    current, full = await service.ticket_here(channel)
    assert current["assignee_id"] == CAROL and current["tier_pinged_at"] is None

    with pytest.raises(TicketError, match="needs a reason"):
        await service.escalate(current, full, CAROL, None, None)
    await service.escalate(current, full, CAROL, None, "needs a refund")
    await service.on_message(message(channel, in_role(dave, "dave", T3_ROLE)))
    current, full = await service.ticket_here(channel)
    assert current["assignee_id"] == dave

    service.cache.get_member.return_value = None
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    await service.close(current, full, dave, "done")
    handlers = {h["user_id"]: h["credited"] for h in await service.repo.list_handlers(ticket["id"])}
    assert handlers == {BOB: True, CAROL: True, dave: True}


@pytest.mark.asyncio
async def test_a_tier_cant_summon_a_tier_it_isnt_allowed_to(service: TicketService):
    support = await ladder(service)
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    current, full = await service.ticket_here(ticket["channel_id"])

    with pytest.raises(TicketError, match=r"T1\*\* can't summon \*\*T3"):
        await service.escalate(current, full, BOB, "T3", "skip the queue")
    with pytest.raises(TicketError, match="no tier called"):
        await service.escalate(current, full, BOB, "Nope", None)
    moved = await service.escalate(current, full, BOB, "developers", None)  # sideways, allowed
    assert moved["tier_id"] == full["tiers"][3]["id"]

    current, full = await service.ticket_here(ticket["channel_id"])
    with pytest.raises(TicketError, match="already the top tier"):
        await service.escalate(current, full, BOB, None, None)


@pytest.mark.asyncio
async def test_deescalate_reping_and_reassign(service: TicketService, monkeypatch):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.NONE)
    support = await ladder(service)
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    channel = ticket["channel_id"]
    current, full = await service.ticket_here(channel)
    with pytest.raises(TicketError, match="hasn't been escalated"):
        await service.deescalate(current, full, BOB, "back")
    assert not service.can_reassign(staff(BOB, "bob"), current, full)

    escalated = await service.escalate(current, full, BOB, None, None)
    assert service.can_reassign(in_role(CAROL, "carol", T2_ROLE), escalated, full)
    assert not service.can_reassign(staff(BOB, "bob"), escalated, full)

    # Unanswered: re-pinged once 30 minutes have passed, then not again for another 30.
    await service.reping_tiers(escalated["tier_pinged_at"] + 29 * 60)
    pings = service.rest.create_message.await_count
    await service.reping_tiers(escalated["tier_pinged_at"] + 31 * 60)
    assert service.rest.create_message.await_count == pings + 1
    assert "Nobody from **T2**" in service.rest.create_message.call_args.args[1]
    await service.reping_tiers(escalated["tier_pinged_at"] + 32 * 60)
    assert service.rest.create_message.await_count == pings + 1

    current, full = await service.ticket_here(channel)
    back = await service.deescalate(current, full, CAROL, "just a password reset")
    assert back["tier_id"] == full["tiers"][0]["id"] and back["tier_pinged_at"] is None
    assert "back to **T1**" in service.rest.create_message.call_args.args[1]
    with pytest.raises(TicketError, match="hasn't been escalated"):
        await service.deescalate(back, full, BOB, "again")


@pytest.mark.asyncio
async def test_claiming_or_closing_ends_the_re_pings_and_button_mode_never_claims_on_reply(
    service: TicketService, monkeypatch
):
    from serenity.services import ticket_service

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.NONE)
    support = await ladder(service)
    await service.repo.update_type(GUILD, support["id"], claim_mode="button")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    current, full = await service.ticket_here(ticket["channel_id"])
    await service.claim(current, BOB, announce=False)
    escalated = await service.escalate(current, full, BOB, None, None)

    await service.on_message(message(ticket["channel_id"], in_role(CAROL, "carol", T2_ROLE)))
    after = await service.repo.get_ticket_by_id(ticket["id"])
    assert after["assignee_id"] == BOB and after["tier_pinged_at"] is not None  # type: ignore[index]

    await service.claim(after, CAROL, by=CAROL, announce=False)  # the tier takes it with /ticket assign
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["tier_pinged_at"] is None  # type: ignore[index]

    await service.repo.update_ticket(ticket["id"], tier_pinged_at=escalated["tier_pinged_at"])
    await service.repo.close_ticket(ticket["id"], BOB, None)
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["tier_pinged_at"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_tier_schema_constraints(service: TicketService):
    import asyncpg

    support = await ladder(service)
    tid = support["id"]
    with pytest.raises(asyncpg.UniqueViolationError) as raced:  # one tier per rung
        await service.repo.create_tier(tid, name="Other", position=2)
    ctx = MagicMock(respond=AsyncMock())  # a lost race reads as one, not as a duplicate type
    from serenity.extensions.tickets import on_error

    await on_error(ctx, raced.value)
    assert "tier name or position" in ctx.respond.call_args.args[0]
    with pytest.raises(asyncpg.UniqueViolationError):  # names are unique case-insensitively
        await service.repo.create_tier(tid, name="t2", position=5)
    for minutes in (0, -5):  # 0 would re-ping every sweep
        with pytest.raises(asyncpg.CheckViolationError):
            await service.repo.create_tier(tid, name="Zero", position=6, reping_minutes=minutes)

    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    current, full = await service.ticket_here(ticket["channel_id"])
    escalated = await service.escalate(current, full, BOB, None, None)
    await service.repo.delete_tier(escalated["tier_id"])
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["tier_id"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_invalid_ign_creates_nothing_and_identity_links_tickets_across_types(
    service: TicketService,
):
    from serenity.services.ticket_service import set_field

    ign = set_field([], "IGN", "pattern", options=r"[A-Za-z0-9_]{3,16}", identity=True)
    appeal = await service.repo.create_type(GUILD, "Appeal", fields=ign)
    report = await service.repo.create_type(
        GUILD, "Report", fields=set_field(ign, "Verdict", "short", staff_only=True)
    )

    with pytest.raises(TicketError, match="IGN"):
        await service.open_ticket(GUILD, member(ALICE, "alice"), appeal, {"ign": "no spaces!"})
    assert service.rest.create_guild_text_channel.await_count == 0
    assert await service.repo.get_ticket(GUILD, 1) is None

    first = await service.open_ticket(GUILD, member(ALICE, "alice"), appeal, {"ign": "Notch_2"})
    second = await service.open_ticket(GUILD, member(BOB, "bob"), report, {"ign": "notch_2"})
    other = await service.open_ticket(GUILD, member(CAROL, "carol"), report, {"ign": "Jeb_"})
    linked = await service.repo.linked_tickets(second["id"])
    assert [t["id"] for t in linked] == [first["id"]] and linked[0]["linked_by"] == ["ign"]
    welcome = service.rest.create_message.call_args_list[-1].kwargs["embed"]
    assert [f.name for f in welcome.fields] == ["IGN"]  # the staff-only Verdict isn't shown

    # Staff fix the IGN: the links follow, and the staff-only field is saved.
    edited = await service.edit_fields(other, report, {"ign": "NOTCH_2", "verdict": "banned"}, BOB)
    assert edited["fields"] == {"ign": "NOTCH_2", "verdict": "banned"}
    assert {t["id"] for t in await service.repo.linked_tickets(first["id"])} == {second["id"], other["id"]}
    with pytest.raises(TicketError, match="IGN"):
        await service.edit_fields(other, report, {"ign": "x"}, BOB)

    # Verdict is removed from the type: its answer stays out of the opener's transcript.
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))
    doc = await service.build_transcript(edited, {**report, "fields": ign})
    assert doc["fields"] == {"IGN": "NOTCH_2"} and doc["staff_fields"] == {"verdict": "banned"}


@pytest.mark.asyncio
async def test_mod_action_links_the_case_and_holds_the_transcript(
    service: TicketService, monkeypatch: pytest.MonkeyPatch
):
    from serenity.database.moderation_repository import ModerationRepository
    from serenity.services import ticket_service
    from serenity.services.moderation_service import ModerationService
    from serenity.services.ticket_service import set_field

    mod_repo = ModerationRepository(service.repo.pool)
    mod = ModerationService(mod_repo, AsyncMock(), service.rest)
    alice, bob = member(ALICE, "alice"), staff(BOB, "bob")
    service.cache.get_member.side_effect = lambda _, user_id: {ALICE: alice}.get(user_id)
    service.cache.get_roles_view_for_guild.return_value = {STAFF_ROLE: MagicMock(position=5)}
    service.rest.fetch_messages = MagicMock(return_value=FakeHistory([]))

    fields = set_field([], "Who are you reporting?", "user")
    report = await service.repo.create_type(GUILD, "Report", staff_role_ids=[STAFF_ROLE], fields=fields)
    earlier = await service.open_ticket(GUILD, alice, report, {"who_are_you_reporting": str(CAROL)})
    await service.close(earlier, report, BOB, None)  # type: ignore[arg-type]
    ticket = await service.open_ticket(GUILD, alice, report, {"who_are_you_reporting": f"<@{CAROL}>"})
    await mod_repo.create_case(GUILD, CAROL, BOB, "kick", "raid")
    await service.repo.add_note(ticket["id"], BOB, "seen this before")

    context = await service.staff_context(ticket, report, mod_repo)
    text = {f.name: f.value for f in context.fields}
    assert "#1 Report · closed" in text["Their other tickets"]
    assert "seen this before" in text["Notes"]
    records = [f.value for f in context.fields if f.name == "Moderation record"]
    assert records[0].startswith(f"<@{ALICE}>\n0 active warnings")
    assert records[1].startswith(f"<@{CAROL}>") and "kick" in records[1]  # the reported member
    assert "Moderation record" not in {f.name for f in (await service.staff_context(ticket, report)).fields}

    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.NONE)
    with pytest.raises(TicketError, match="Moderate Members"):  # staff, but not a moderator
        await service.mod_action(ticket, report, bob, ALICE, "warn", "spam", None, mod, mod_repo)
    monkeypatch.setattr(ticket_service, "member_permissions", lambda _: hikari.Permissions.MODERATE_MEMBERS)
    case = await service.mod_action(ticket, report, bob, ALICE, "warn", "spam", None, mod, mod_repo)

    linked = await mod_repo.get_case(GUILD, case)
    assert linked is not None and linked["source_ticket_number"] == ticket["number"]
    assert await mod_repo.get_warning_count(GUILD, ALICE) == 1
    events = [e["kind"] for e in await service.repo.list_events(ticket["id"])]
    assert "mod_action" in events

    # The ticket had no transcript yet: closing it saves one already held, so it never expires.
    await service.close(ticket, report, BOB, None)
    transcript = await service.repo.get_transcript(ticket["id"])
    assert transcript is not None and transcript["held_by_case_number"] == case
    assert (await service.repo.get_transcript(earlier["id"]))["held_by_case_number"] is None  # type: ignore[index]
    await service.repo.purge_expired_transcripts(now=transcript["expires_at"] + 1)
    assert await service.repo.get_transcript(earlier["id"]) is None
    assert await service.repo.get_transcript(ticket["id"]) is not None


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


SALES_STAGES = [
    {"name": "Inquiry", "color": None, "terminal": False, "auto_close": True},
    {"name": "Quoted", "color": 0x57F287, "terminal": False, "auto_close": True},
    {"name": "Paid", "color": None, "terminal": True, "auto_close": False},
]


@pytest.mark.asyncio
async def test_stages_start_first_move_with_history_and_reset_on_transfer(service: TicketService):
    service.cache.get_member.return_value = None
    sales = await service.repo.create_type(GUILD, "Sales", staff_role_ids=[STAFF_ROLE], stages=SALES_STAGES)
    support = await service.repo.create_type(GUILD, "Support")
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), sales, {})
    assert ticket["stage"] == "Inquiry"
    assert "Inquiry" in service.rest.create_guild_text_channel.call_args.kwargs["topic"]

    with pytest.raises(TicketError, match="no stage called"):
        await service.set_stage(ticket, sales, "Shipped", BOB)
    with pytest.raises(TicketError, match="already at"):
        await service.set_stage(ticket, sales, "inquiry", BOB)

    quoted = await service.set_stage(ticket, sales, "quoted", BOB)
    assert quoted["stage"] == "Quoted"
    assert not service.rest.create_message.call_args.kwargs["components"]  # not terminal: no close offer
    paid = await service.set_stage(quoted, sales, "Paid", BOB)
    [row] = service.rest.create_message.call_args.kwargs["components"]
    assert row.components[0].custom_id == f"ticket:closeas:{ticket['id']}"

    history = [e["data"] for e in await service.repo.list_events(ticket["id"]) if e["kind"] == "stage"]
    assert history == [{"from": "Inquiry", "to": "Quoted"}, {"from": "Quoted", "to": "Paid"}]

    service.cache.get_guild_channel.return_value = MagicMock(permission_overwrites={})
    moved = await service.transfer(paid, sales, support, BOB)
    assert moved["stage"] is None  # Support has no workflow


@pytest.mark.asyncio
async def test_waiting_on_flips_edit_the_topic_at_most_once_per_interval(service: TicketService):
    service.cache.get_member.return_value = None
    support = await service.repo.create_type(GUILD, "Support", staff_role_ids=[STAFF_ROLE])
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    channel = ticket["channel_id"]

    await service.on_message(message(channel, staff(BOB, "bob")))  # now waiting on the opener
    for _ in range(200):  # no edit yet, so it runs straight away; wait for it, up to 2s
        if service.rest.edit_channel.await_count:
            break
        await asyncio.sleep(0.01)
    service.rest.edit_channel.assert_awaited_once_with(channel, topic="Ticket #1 · Support · waiting on member")

    await service.on_message(message(channel, member(ALICE, "alice")))  # back to staff
    await service.on_message(message(channel, staff(BOB, "bob")))
    await asyncio.sleep(0.01)
    assert service.rest.edit_channel.await_count == 1  # the next edit waits out the interval
    assert list(service._topic_tasks) == [channel]  # one pending edit for the whole burst


def warnings_sent(service: TicketService) -> int:
    return sum("closes automatically" in str(c.args) for c in service.rest.create_message.call_args_list)


@pytest.mark.asyncio
async def test_auto_close_warns_then_closes_and_never_while_waiting_on_staff(service: TicketService):
    support = await service.repo.create_type(
        GUILD, "Support", staff_role_ids=[STAFF_ROLE], auto_close_hours=24, transcripts=False
    )
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), support, {})
    await service.auto_close(ticket["last_activity_at"] + 1000 * 3600)  # waiting on staff: never
    assert warnings_sent(service) == 0

    await service.on_message(message(ticket["channel_id"], staff(BOB, "bob")))
    quiet = (await service.repo.get_ticket_by_id(ticket["id"]))["last_activity_at"]  # type: ignore[index]
    await service.auto_close(quiet + 23 * 3600)
    assert warnings_sent(service) == 0
    await service.auto_close(quiet + 24 * 3600 + 1)
    await service.auto_close(quiet + 24 * 3600 + 2)
    assert warnings_sent(service) == 1  # once

    await service.auto_close(quiet + 35 * 3600)
    assert (await service.repo.get_ticket_by_id(ticket["id"]))["status"] == "open"  # type: ignore[index]
    await service.auto_close(quiet + 36 * 3600 + 2)
    closed = await service.repo.get_ticket_by_id(ticket["id"])
    assert closed["status"] == "closed" and closed["close_reason"].startswith("Auto-closed")  # type: ignore[index]
    assert "auto_closed" in [e["kind"] for e in await service.repo.list_events(ticket["id"])]


@pytest.mark.asyncio
async def test_auto_close_spreads_a_due_backlog_over_sweeps(service: TicketService, monkeypatch):
    import serenity.services.ticket_service as ticket_service

    monkeypatch.setattr(ticket_service, "AUTO_CLOSES_PER_SWEEP", 1)
    support = await service.repo.create_type(
        GUILD, "Support", staff_role_ids=[STAFF_ROLE], auto_close_hours=1, transcripts=False, open_limit=2
    )
    tickets = [await service.open_ticket(GUILD, member(ALICE, "alice"), support, {}) for _ in range(2)]
    for t in tickets:
        await service.on_message(message(t["channel_id"], staff(BOB, "bob")))
    quiet = max([(await service.repo.get_ticket_by_id(t["id"]))["last_activity_at"] for t in tickets])  # type: ignore[index]
    await service.auto_close(quiet + 3600 + 1)  # both warned
    await service.auto_close(quiet + 13 * 3600 + 2)  # both due, one closed

    async def open_count() -> int:
        return await service.repo.count_open(GUILD, ALICE, support["id"])

    assert await open_count() == 1
    await service.auto_close(quiet + 13 * 3600 + 3)
    assert await open_count() == 0


@pytest.mark.asyncio
async def test_auto_close_skips_stages_with_it_off_and_pauses_outside_support_hours(service: TicketService):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from serenity.services.ticket_hours import parse_schedule

    def london(*args: int) -> int:
        return int(datetime(*args, tzinfo=ZoneInfo("Europe/London")).timestamp())

    await service.modules.get_module_config(GUILD, ModuleType.TICKETS)
    await service.modules.update_module_settings(
        GUILD, ModuleType.TICKETS,
        {"schedule": parse_schedule("mon-fri 09:00-17:00"), "timezone": "Europe/London"},
    )
    sales = await service.repo.create_type(
        GUILD, "Sales", auto_close_hours=2, transcripts=False, stages=SALES_STAGES
    )
    ticket = await service.open_ticket(GUILD, member(ALICE, "alice"), sales, {})
    friday = london(2026, 10, 23, 16)  # an hour before closing time
    await service.repo.update_ticket(ticket["id"], waiting_on="opener", last_activity_at=friday)

    await service.auto_close(london(2026, 10, 26, 8))  # Monday 08:00, after the DST change: 1h counted
    assert warnings_sent(service) == 0
    await service.auto_close(london(2026, 10, 26, 10))  # 2h counted
    assert warnings_sent(service) == 1

    await service.repo.update_ticket(ticket["id"], stage="Paid", close_warned_at=None)
    await service.auto_close(london(2026, 11, 30, 10))
    assert warnings_sent(service) == 1  # Paid has auto-close off
