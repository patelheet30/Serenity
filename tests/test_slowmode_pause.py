"""A manual slowmode change pauses auto-slowmode on that channel for an hour (#150)."""

import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

from serenity.extensions.logging_events import MANUAL_SLOWMODE_PAUSE, pause_for_manual_slowmode

GUILD, ME, MOD, CHANNEL = 1, 10, 20, 30


def entry(actor: int = MOD, key=hikari.AuditLogChangeKey.RATE_LIMIT_PER_USER) -> hikari.AuditLogEntry:
    return hikari.AuditLogEntry(
        app=MagicMock(),
        id=hikari.Snowflake(1 << 40),
        guild_id=hikari.Snowflake(GUILD),
        target_id=hikari.Snowflake(CHANNEL),
        changes=[hikari.AuditLogChange(new_value=timedelta(seconds=30), old_value=None, key=key)],
        user_id=hikari.Snowflake(actor),
        action_type=hikari.AuditLogEventType.CHANNEL_UPDATE,
        options=None,
        reason=None,
    )


@pytest.mark.asyncio
async def test_manual_change_pauses_and_logs():
    repo, log = AsyncMock(), AsyncMock()
    repo.pause_channel.return_value = True
    await pause_for_manual_slowmode(entry(), me=ME, repo=repo, logging_svc=log)

    channel_id, until = repo.pause_channel.call_args.args
    assert channel_id == CHANNEL
    assert abs(until - (time.time() + MANUAL_SLOWMODE_PAUSE)) < 5
    kwargs = log.log_slowmode_paused.call_args.kwargs
    assert (kwargs["moderator_id"], kwargs["old"], kwargs["new"]) == (MOD, 0, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("e", "managed"),
    [
        (entry(actor=ME), True),  # Serenity's own change
        (entry(key=hikari.AuditLogChangeKey.NAME), True),  # not a slowmode change
        (entry(), False),  # channel isn't under auto-slowmode
    ],
)
async def test_no_pause_or_log(e, managed):
    repo, log = AsyncMock(), AsyncMock()
    repo.pause_channel.return_value = managed
    await pause_for_manual_slowmode(e, me=ME, repo=repo, logging_svc=log)
    log.log_slowmode_paused.assert_not_called()


@pytest.mark.asyncio
async def test_paused_channels_skip_auto_slowmode(pg_repo):
    await pg_repo.get_channel_config(CHANNEL, GUILD)  # creates an enabled row
    assert await pg_repo.get_enabled_channels(GUILD) == [CHANNEL]

    assert await pg_repo.pause_channel(CHANNEL, int(time.time()) + 60)
    assert await pg_repo.get_enabled_channels(GUILD) == []

    await pg_repo.update_channel_config(CHANNEL, is_enabled=True)  # resume now
    assert await pg_repo.get_enabled_channels(GUILD) == [CHANNEL]

    assert not await pg_repo.pause_channel(999, int(time.time()) + 60)
