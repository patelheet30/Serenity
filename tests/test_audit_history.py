"""Audit history module: stored audit log entries (#154)."""

from datetime import timedelta
from unittest.mock import MagicMock

import hikari
import pytest

from serenity.core.modules import ModuleType
from serenity.database.audit_repository import AuditRepository

GUILD, OTHER, MOD, USER = 1, 2, 20, 40
DAY = 86400


def entry(entry_id: int, action=hikari.AuditLogEventType.MEMBER_BAN_ADD, guild=GUILD, reason=None, changes=(), options=None):
    return hikari.AuditLogEntry(
        app=MagicMock(),
        id=hikari.Snowflake(entry_id),
        guild_id=hikari.Snowflake(guild),
        target_id=hikari.Snowflake(USER),
        changes=[hikari.AuditLogChange(key=k, old_value=o, new_value=n) for k, o, n in changes],
        user_id=hikari.Snowflake(MOD),
        action_type=action,
        options=options,
        reason=reason,
    )


def snowflake_at(days_ago: float) -> int:
    """An entry id whose timestamp is `days_ago` days before now."""
    import time

    ms = int((time.time() - days_ago * DAY) * 1000) - 1420070400000
    return ms << 22


@pytest.mark.asyncio
async def test_add_search_and_clear(pg_repo):
    repo = AuditRepository(pg_repo.pool)
    old, new = snowflake_at(2), snowflake_at(1)
    slow = [(hikari.AuditLogChangeKey.RATE_LIMIT_PER_USER, timedelta(0), timedelta(seconds=30))]
    await repo.add(entry(old, reason="raid"))
    await repo.add(entry(new, action=hikari.AuditLogEventType.CHANNEL_UPDATE, changes=slow))
    await repo.add(entry(new, action=hikari.AuditLogEventType.CHANNEL_UPDATE, changes=slow))  # duplicate
    await repo.add(entry(new, guild=OTHER))  # the key is (guild, entry), so this one is kept
    await repo.add(entry(new + 1, reason="50%_off", guild=OTHER))

    assert [r["entry_id"] for r in await repo.search(GUILD)] == [new, old]  # newest first
    assert [r["entry_id"] for r in await repo.search(GUILD, actions=[11])] == [new]
    assert [r["reason"] for r in await repo.search(GUILD, q="rai")] == ["raid"]
    assert [r["reason"] for r in await repo.search(OTHER, q="0%_")] == ["50%_off"]  # % and _ are literal
    assert await repo.search(OTHER, q="5_%") == []
    channel = (await repo.search(GUILD, actions=[11]))[0]
    assert channel["changes"] == [{"key": "rate_limit_per_user", "old": 0, "new": 30}]
    assert [r["entry_id"] for r in await repo.search(GUILD, before=new)] == [old]

    assert await repo.clear(GUILD) == 2
    assert len(await repo.search(OTHER)) == 2


@pytest.mark.asyncio
async def test_cleanup_uses_each_servers_retention(pg_repo):
    repo = AuditRepository(pg_repo.pool)
    await pg_repo.get_module_config(OTHER, ModuleType.AUDIT_HISTORY)
    await pg_repo.update_module_settings(OTHER, ModuleType.AUDIT_HISTORY, {"retention_days": 7})
    for guild in (GUILD, OTHER):
        await repo.add(entry(snowflake_at(100) + guild, guild=guild))
        await repo.add(entry(snowflake_at(30) + guild, guild=guild))

    await repo.cleanup()

    assert len(await repo.search(GUILD)) == 1  # default 90 days keeps the 30-day-old one
    assert await repo.search(OTHER) == []  # 7 days drops both


@pytest.mark.asyncio
async def test_options_without_known_fields_store_as_empty_json(pg_repo):
    repo = AuditRepository(pg_repo.pool)
    await repo.add(entry(snowflake_at(1), options=object()))
    assert (await repo.search(GUILD))[0]["options"] == {}


@pytest.mark.asyncio
async def test_changes_defaults_to_an_empty_list(pg_repo):
    await pg_repo.pool.execute(
        "INSERT INTO audit_log_entries (guild_id, entry_id, action_type, created_at) VALUES ($1, 1, 1, 0)", GUILD
    )
    assert await pg_repo.pool.fetchval("SELECT changes FROM audit_log_entries WHERE guild_id = $1", GUILD) == "[]"
