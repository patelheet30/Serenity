"""Moderation/logging repositories and task queries against Postgres, with realistic snowflakes."""

import asyncio
import time

import asyncpg
import pytest

from serenity.database.logging_repository import LoggingRepository
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.repository import Repository
from serenity.extensions import tasks

GUILD = 1_103_276_413_850_812_416
OTHER_GUILD = 1_103_276_413_850_812_417
CHANNEL = 1_241_890_335_472_164_904
LOG_CHANNEL = 1_241_890_335_472_164_999
USER = 1_029_384_756_102_938_475
MOD = 1_029_384_756_102_938_476


@pytest.fixture
def mod_repo(pg_repo: Repository) -> ModerationRepository:
    assert pg_repo.pool is not None
    return ModerationRepository(pg_repo.pool)


@pytest.fixture
def log_repo(pg_repo: Repository) -> LoggingRepository:
    assert pg_repo.pool is not None
    return LoggingRepository(pg_repo.pool)


@pytest.mark.asyncio
async def test_case_numbers_are_per_guild(mod_repo: ModerationRepository):
    assert await mod_repo.create_case(GUILD, USER, MOD, "warn", "spam") == 1
    assert await mod_repo.create_case(GUILD, USER, MOD, "ban", "raid", 3600, None) == 2
    assert await mod_repo.create_case(OTHER_GUILD, USER, MOD, "kick") == 1

    case = await mod_repo.get_case(GUILD, 2)
    assert case is not None
    assert case["target_user_id"] == USER
    assert case["moderator_id"] == MOD
    assert case["duration_sections"] == 3600
    assert case["is_active"] is True


@pytest.mark.asyncio
async def test_concurrent_cases_get_consecutive_numbers(mod_repo: ModerationRepository):
    numbers = await asyncio.gather(
        *(mod_repo.create_case(GUILD, USER, MOD, "warn") for _ in range(10))
    )
    assert sorted(numbers) == list(range(1, 11))


@pytest.mark.asyncio
async def test_case_updates_and_queries(mod_repo: ModerationRepository):
    now = int(time.time())
    await mod_repo.create_case(GUILD, USER, MOD, "timeout", "a", 600, now + 600)
    await mod_repo.create_case(GUILD, USER, MOD, "timeout", "b", 60, now - 60)

    await mod_repo.update_case_reason(GUILD, 1, "edited")
    case = await mod_repo.get_case(GUILD, 1)
    assert case is not None and case["reason"] == "edited"

    assert [c["case_number"] for c in await mod_repo.get_active_timeouts(GUILD)] == [1]
    assert [c["case_number"] for c in await mod_repo.get_all_expired_timeouts()] == [2]
    assert len(await mod_repo.get_user_cases(GUILD, USER, limit=1)) == 1

    await mod_repo.deactivate_case(GUILD, 2)
    assert await mod_repo.get_all_expired_timeouts() == []

    stats = await mod_repo.get_moderation_stats(GUILD)
    assert stats == {"timeout": 2, "warnings": 0}


@pytest.mark.asyncio
async def test_warnings(mod_repo: ModerationRepository):
    case_number = await mod_repo.create_case(GUILD, USER, MOD, "warn", "spam")
    warning_id = await mod_repo.add_warning(GUILD, USER, MOD, "spam", case_id=case_number)
    await mod_repo.add_warning(GUILD, USER, MOD, "no case")

    assert isinstance(warning_id, int)
    assert await mod_repo.get_warning_count(GUILD, USER) == 2
    assert len(await mod_repo.get_active_warnings(GUILD, USER)) == 2

    # Only warnings linked to a case are counted when any exist (existing behaviour)
    assert await mod_repo.clear_warnings(GUILD, USER) == 1
    assert await mod_repo.get_warning_count(GUILD, USER) == 0
    case = await mod_repo.get_case(GUILD, case_number)
    assert case is not None and case["is_active"] is False


@pytest.mark.asyncio
async def test_clear_warnings_without_cases(mod_repo: ModerationRepository):
    await mod_repo.add_warning(GUILD, USER, MOD, "one")
    await mod_repo.add_warning(GUILD, USER, MOD, "two")

    assert await mod_repo.clear_warnings(GUILD, USER) == 2
    assert await mod_repo.clear_warnings(GUILD, USER) == 0


@pytest.mark.asyncio
async def test_log_channels(log_repo: LoggingRepository):
    assert await log_repo.get_log_channel(GUILD, "message") is None

    await log_repo.set_log_channel(GUILD, "message", LOG_CHANNEL)
    await log_repo.set_log_channel(GUILD, "message", LOG_CHANNEL + 1, is_enabled=False)

    config = await log_repo.get_log_channel(GUILD, "message")
    assert config is not None
    assert config["channel_id"] == LOG_CHANNEL + 1
    assert config["is_enabled"] is False
    assert config["ignored_channels"] == []

    await log_repo.enable_log_channel(GUILD, "message")
    assert await log_repo.should_log_event(GUILD, "message") == (True, LOG_CHANNEL + 1)

    await log_repo.disable_log_channel(GUILD, "message")
    assert await log_repo.should_log_event(GUILD, "message") == (False, None)

    assert len(await log_repo.get_all_log_channels(GUILD)) == 1
    await log_repo.delete_all_log_configs(GUILD)
    assert await log_repo.get_all_log_channels(GUILD) == []


@pytest.mark.asyncio
async def test_log_ignores(log_repo: LoggingRepository):
    await log_repo.set_log_channel(GUILD, "message", LOG_CHANNEL)

    assert await log_repo.add_ignore(GUILD, "user", USER, "message") is True
    assert await log_repo.add_ignore(GUILD, "user", USER, "message") is False
    assert await log_repo.should_log_event(GUILD, "message", user_id=USER) == (False, None)

    # Global ignore replaces the per-type row
    assert await log_repo.add_ignore(GUILD, "channel", CHANNEL, "voice") is True
    assert await log_repo.add_ignore(GUILD, "channel", CHANNEL) is True
    assert await log_repo.list_ignores(GUILD) == [
        {"target_type": "channel", "target_id": CHANNEL, "log_type": "*"},
        {"target_type": "user", "target_id": USER, "log_type": "message"},
    ]
    assert await log_repo.should_log_event(GUILD, "message", channel_id=CHANNEL) == (False, None)

    assert await log_repo.remove_ignore(GUILD, "user", USER, "voice") == 0
    assert await log_repo.remove_ignore(GUILD, "user", USER, "message") == 1
    assert await log_repo.remove_ignore(GUILD, "channel", CHANNEL) == 1
    assert await log_repo.list_ignores(GUILD) == []


@pytest.mark.asyncio
async def test_log_type_check_rejects_unknown_values(log_repo: LoggingRepository):
    """The extension must translate the "all" choice to "*"; the schema rejects it."""
    with pytest.raises(asyncpg.CheckViolationError):
        await log_repo.add_ignore(GUILD, "user", USER, "all")


@pytest.mark.asyncio
async def test_task_queries(pg_repo: Repository):
    now = int(time.time())
    for i in range(5):
        await pg_repo.record_message_activity(CHANNEL, now - 100 + i)

    assert await tasks._get_active_channels(pg_repo, now - 200, now) == [CHANNEL]
    assert await tasks._get_message_count(pg_repo, CHANNEL, now - 200, now) == 5
    assert await tasks._get_message_count(pg_repo, CHANNEL, 0, 1) == 0

    assert await tasks._update_pattern_stats(pg_repo, CHANNEL, 1, 14, 4.0) == (4.0, 0.0, 1)
    await pg_repo.update_channel_pattern(CHANNEL, 1, 14, 4.0, 0.0, 1)
    avg, _, count = await tasks._update_pattern_stats(pg_repo, CHANNEL, 1, 14, 6.0)
    assert (avg, count) == (5.0, 2)


@pytest.mark.asyncio
async def test_pattern_samples_accumulate(pg_repo: Repository):
    """Regression: every hourly sample used to reset sample_count to 1."""
    assert pg_repo.pool is not None
    for rate in (2.0, 4.0, 6.0):
        await tasks._record_pattern_sample(pg_repo, CHANNEL, 3, 18, rate)

    row = await pg_repo.pool.fetchrow(
        "SELECT avg_message_rate, stddev_message_rate, sample_count FROM channel_patterns"
    )
    assert row["sample_count"] == 3
    assert row["avg_message_rate"] == 4.0
    assert row["stddev_message_rate"] == pytest.approx((8 / 3) ** 0.5)  # population stddev

    # Engine only trusts patterns from 10 samples on
    assert await pg_repo.get_expected_activity(CHANNEL, 3, 18) is None
    for _ in range(7):
        await tasks._record_pattern_sample(pg_repo, CHANNEL, 3, 18, 4.0)
    assert await pg_repo.get_expected_activity(CHANNEL, 3, 18) == 4.0
