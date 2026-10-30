"""Server logs say who made each change, from the matching audit log entry (#151)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

from serenity.database.logging_repository import LoggingRepository
from serenity.services.audit_actors import AuditActors
from serenity.services.logging_service import LoggingService

GUILD, MOD, ROLE = 1, 20, 50
ROLE_UPDATE = hikari.AuditLogEventType.ROLE_UPDATE


def entry(action=ROLE_UPDATE, target=ROLE, reason=None) -> hikari.AuditLogEntry:
    return hikari.AuditLogEntry(
        app=MagicMock(),
        id=hikari.Snowflake(1 << 40),
        guild_id=hikari.Snowflake(GUILD),
        target_id=hikari.Snowflake(target),
        changes=[],
        user_id=hikari.Snowflake(MOD),
        action_type=action,
        options=None,
        reason=reason,
    )


@pytest.mark.asyncio
async def test_entry_before_the_log():
    actors = AuditActors(lambda _: True)
    e = entry()
    actors.feed(e)
    assert await actors.find(GUILD, [ROLE_UPDATE], ROLE, timeout=0.1) is e
    assert await actors.find(GUILD, [ROLE_UPDATE], ROLE, timeout=0.05) is None  # claimed once


@pytest.mark.asyncio
async def test_entry_after_the_log():
    actors = AuditActors(lambda _: True)
    e = entry()
    waiting = asyncio.create_task(actors.find(GUILD, [ROLE_UPDATE], ROLE, timeout=1))
    await asyncio.sleep(0)
    actors.feed(e)
    assert await waiting is e


@pytest.mark.asyncio
async def test_other_entries_and_timeouts():
    actors = AuditActors(lambda _: True)
    actors.feed(entry(target=999))
    actors.feed(entry(action=hikari.AuditLogEventType.ROLE_DELETE))
    assert await actors.find(GUILD, [ROLE_UPDATE], ROLE, timeout=0.05) is None


@pytest.mark.asyncio
async def test_no_wait_without_view_audit_log():
    actors = AuditActors(lambda _: False)
    actors.feed(entry())
    assert await actors.find(GUILD, [ROLE_UPDATE], ROLE, timeout=5) is None


@pytest.mark.asyncio
async def test_role_update_log_says_who():
    actors = AuditActors(lambda _: True)
    actors.feed(entry(reason="rebrand"))
    repo = AsyncMock(spec=LoggingRepository)
    repo.should_log_event.return_value = (True, 555)
    rest = AsyncMock()
    svc = LoggingService(repo=repo, rest=rest, audit=actors)

    old = MagicMock(name="old", color=hikari.Color(0), is_mentionable=False, is_hoisted=False)
    old.name = "Mods"
    new = MagicMock(id=ROLE, color=hikari.Color(0), is_mentionable=False, is_hoisted=False, mention="@Staff")
    new.name = "Staff"
    await svc.log_role_update(MagicMock(guild_id=GUILD, old_role=old, role=new))

    embed = rest.create_message.call_args.kwargs["embed"]
    by = next(f for f in embed.fields if f.name == "By")
    assert by.value == f"<@{MOD}>\n*rebrand*"
