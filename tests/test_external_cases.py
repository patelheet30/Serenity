"""Audit log entries for actions Serenity didn't take become external cases (#99)."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

from serenity.services.moderation_service import ModerationService

GUILD, ME, MOD, BOT, USER = 1, 10, 20, 30, 40
Change = hikari.AuditLogChangeKey


def entry(action: hikari.AuditLogEventType, actor: int = MOD, changes=(), reason=None) -> hikari.AuditLogEntry:
    return hikari.AuditLogEntry(
        app=MagicMock(**{"cache.get_user.return_value": None}),
        id=hikari.Snowflake(1 << 40),
        guild_id=hikari.Snowflake(GUILD),
        target_id=hikari.Snowflake(USER),
        changes=list(changes),
        user_id=hikari.Snowflake(actor),
        action_type=action,
        options=None,
        reason=reason,
    )


@pytest.fixture
def svc():
    repo = AsyncMock()
    repo.create_case.return_value = 7
    rest = AsyncMock()
    rest.fetch_user.side_effect = lambda uid: MagicMock(id=uid, username=f"user{uid}", is_bot=uid == BOT)
    return ModerationService(repo, AsyncMock(), rest)


@pytest.mark.asyncio
async def test_ban_by_a_person_is_an_external_case(svc):
    assert await svc.record_external(entry(hikari.AuditLogEventType.MEMBER_BAN_ADD, reason="raid"), me=ME) == 7
    kwargs = svc._mod_repo.create_case.call_args.kwargs
    assert kwargs["action"] == "ban"
    assert kwargs["moderator_id"] == MOD and kwargs["target_user_id"] == USER
    assert kwargs["reason"] == "raid"
    assert kwargs["external_source"] == "Discord"
    assert svc._logging_svc.log_mod_action.call_args.kwargs["external_source"] == "Discord"


@pytest.mark.asyncio
async def test_kick_by_another_bot_names_the_bot(svc):
    await svc.record_external(entry(hikari.AuditLogEventType.MEMBER_KICK, actor=BOT), me=ME)
    assert svc._mod_repo.create_case.call_args.kwargs["external_source"] == f"user{BOT}"


@pytest.mark.asyncio
async def test_serenitys_own_actions_are_skipped(svc):
    assert await svc.record_external(entry(hikari.AuditLogEventType.MEMBER_BAN_ADD, actor=ME), me=ME) is None
    svc._mod_repo.create_case.assert_not_called()


@pytest.mark.asyncio
async def test_timeout_added_and_removed(svc):
    e = entry(hikari.AuditLogEventType.MEMBER_UPDATE)
    until = e.id.created_at + timedelta(minutes=10)
    e.changes = [hikari.AuditLogChange(new_value=until, old_value=None, key=Change.COMMUNICATION_DISABLED_UNTIL)]
    await svc.record_external(e, me=ME)
    kwargs = svc._mod_repo.create_case.call_args.kwargs
    assert kwargs["action"] == "timeout"
    assert kwargs["duration_seconds"] == 600
    assert kwargs["expires_at"] == int(until.timestamp())

    e.changes = [hikari.AuditLogChange(new_value=None, old_value=until, key=Change.COMMUNICATION_DISABLED_UNTIL)]
    await svc.record_external(e, me=ME)
    assert svc._mod_repo.create_case.call_args.kwargs["action"] == "untimeout"


@pytest.mark.asyncio
async def test_other_entries_are_ignored(svc):
    nick = hikari.AuditLogChange(new_value="x", old_value="y", key=Change.NICK)
    assert await svc.record_external(entry(hikari.AuditLogEventType.MEMBER_UPDATE, changes=[nick]), me=ME) is None
    assert await svc.record_external(entry(hikari.AuditLogEventType.CHANNEL_DELETE), me=ME) is None
    svc._mod_repo.create_case.assert_not_called()


@pytest.mark.asyncio
async def test_cached_users_skip_rest(svc):
    e = entry(hikari.AuditLogEventType.MEMBER_KICK)
    e.app.cache.get_user.side_effect = lambda uid: MagicMock(id=uid, username=f"user{uid}", is_bot=False)
    await svc.record_external(e, me=ME)
    svc._rest.fetch_user.assert_not_called()
