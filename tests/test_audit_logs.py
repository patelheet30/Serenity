"""Invites, threads, stickers and AutoMod in the logs (#100)."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

from serenity.database.logging_repository import LoggingRepository
from serenity.services.logging_service import LoggingService

GUILD, MOD, USER, CHANNEL, THREAD = 1, 20, 40, 30, 60
Key = hikari.AuditLogChangeKey


@pytest.fixture
def svc():
    repo = AsyncMock(spec=LoggingRepository)
    repo.should_log_event.return_value = (True, 555)
    return LoggingService(repo=repo, rest=AsyncMock())


def entry(action, changes, target=None) -> hikari.AuditLogEntry:
    return hikari.AuditLogEntry(
        app=MagicMock(),
        id=hikari.Snowflake(1 << 40),
        guild_id=hikari.Snowflake(GUILD),
        target_id=target and hikari.Snowflake(target),
        changes=[hikari.AuditLogChange(key=k, old_value=o, new_value=n) for k, o, n in changes],
        user_id=hikari.Snowflake(MOD),
        action_type=action,
        options=None,
        reason=None,
    )


def fields(svc) -> dict[str, str]:
    return {f.name: f.value for f in svc._rest.create_message.call_args.kwargs["embed"].fields}


@pytest.mark.asyncio
async def test_invite_create(svc):
    await svc.log_audit_entry(entry(
        hikari.AuditLogEventType.INVITE_CREATE,
        [(Key.INVITE_CODE, None, "abc"), (Key.CHANNEL_ID, None, CHANNEL), (Key.MAX_USES, None, 5)],
    ))
    f = fields(svc)
    assert "**Code:** abc" in f["Details"] and f"<#{CHANNEL}>" in f["Details"]
    assert f["By"] == f"<@{MOD}>"
    kwargs = svc._repo.should_log_event.call_args.kwargs
    assert (kwargs["log_type"], kwargs["channel_id"], kwargs["user_id"]) == ("server", CHANNEL, MOD)


@pytest.mark.asyncio
async def test_thread_updates(svc):
    archive = entry(hikari.AuditLogEventType.THREAD_UPDATE, [(Key.ARCHIVED, False, True)], THREAD)
    await svc.log_audit_entry(archive)
    svc._rest.create_message.assert_not_called()

    rename = entry(hikari.AuditLogEventType.THREAD_UPDATE, [(Key.NAME, "a", "b"), (Key.ARCHIVED, False, True)], THREAD)
    await svc.log_audit_entry(rename)
    assert "**Name:** a → b" in fields(svc)["Details"]


@pytest.mark.asyncio
async def test_unrelated_entries_are_ignored(svc):
    await svc.log_audit_entry(entry(hikari.AuditLogEventType.MEMBER_KICK, [], USER))
    svc._rest.create_message.assert_not_called()


@pytest.mark.asyncio
async def test_automod_actions_collapse_into_one_log(svc):
    def event(action):
        return MagicMock(
            guild_id=GUILD, rule_id=7, user_id=USER, channel_id=CHANNEL, content="bad word",
            matched_keyword="bad", rule_trigger_type=hikari.AutoModTriggerType.KEYWORD, action=action,
        )

    block = hikari.AutoModBlockMessage(type=hikari.AutoModActionType.BLOCK_MESSAGE)
    timeout = hikari.AutoModTimeout(type=hikari.AutoModActionType.TIMEOUT, duration=timedelta(minutes=5))
    await asyncio.gather(svc.log_automod_action(event(block)), svc.log_automod_action(event(timeout)))

    svc._rest.create_message.assert_called_once()
    f = fields(svc)
    assert f["Action"] == "Blocked the message\nTimed out the member for 5m"
    assert f["Matched"] == "`bad`"
