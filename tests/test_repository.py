"""Tests for database repository (run against Postgres; see the pg_repo fixture)"""

import os
import time

import asyncpg
import pytest

from serenity.core.modules import ModuleType
from serenity.database.repository import Repository
from serenity.utils.errors import DatabaseError

# Realistic snowflakes: well past the 32-bit INTEGER range.
GUILD = 1_103_276_413_850_812_416
CHANNEL = 1_241_890_335_472_164_904
OTHER_CHANNELS = (1_241_890_335_472_164_905, 1_241_890_335_472_164_906, 1_241_890_335_472_164_907)


@pytest.fixture
def repo(pg_repo: Repository) -> Repository:
    return pg_repo


@pytest.mark.asyncio
async def test_init_creates_tables(repo: Repository):
    """Test that initialization creates all required tables"""
    assert repo.pool is not None

    rows = await repo.pool.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    tables = {row["table_name"] for row in rows}

    expected_tables = {
        "guild_config",
        "channel_config",
        "message_activity",
        "channel_patterns",
        "slowmode_changes",
        "channel_analytics",
        "slowmode_effectiveness",
        "module_config",
        "mod_cases",
        "warnings",
        "log_channels",
        "log_ignores",
        "schema_version",
    }

    assert expected_tables.issubset(tables)


@pytest.mark.asyncio
async def test_migrations_are_idempotent(repo: Repository):
    """Running migrations again on an up-to-date database is a no-op"""
    from serenity.database.migrations import run_migrations

    assert repo.pool is not None
    await run_migrations(repo.pool)

    assert await repo.pool.fetchval("SELECT COUNT(*) FROM schema_version") == 1


@pytest.mark.asyncio
async def test_get_guild_config_creates_default(repo: Repository):
    """Test that getting non-existent guild creates default config"""
    config = await repo.get_guild_config(GUILD)

    assert config.guild_id == GUILD
    assert config.is_enabled is True
    assert config.default_threshold == 10
    assert config.update_interval == 30

    # The default row was persisted
    config = await repo.get_guild_config(GUILD)
    assert config.guild_id == GUILD
    assert config.is_enabled is True


@pytest.mark.asyncio
async def test_update_guild_config(repo: Repository):
    """Test updating guild configuration"""
    await repo.get_guild_config(GUILD)

    await repo.update_guild_config(
        GUILD, is_enabled=False, default_threshold=20, update_interval=60
    )

    config = await repo.get_guild_config(GUILD)
    assert config.is_enabled is False
    assert config.default_threshold == 20
    assert config.update_interval == 60


@pytest.mark.asyncio
async def test_get_channel_config_creates_default(repo: Repository):
    """Test that getting non-existent channel creates default config"""
    config = await repo.get_channel_config(CHANNEL, GUILD)

    assert config.channel_id == CHANNEL
    assert config.guild_id == GUILD
    assert config.is_enabled is True
    assert config.threshold is None


@pytest.mark.asyncio
async def test_update_channel_config(repo: Repository):
    """Test updating channel configuration"""
    await repo.get_channel_config(CHANNEL, GUILD)

    await repo.update_channel_config(CHANNEL, is_enabled=False, threshold=15)

    config = await repo.get_channel_config(CHANNEL, GUILD)
    assert config.is_enabled is False
    assert config.threshold == 15


@pytest.mark.asyncio
async def test_get_enabled_channels(repo: Repository):
    """Test getting enabled channels for a guild"""
    a, b, c = OTHER_CHANNELS
    for channel_id in OTHER_CHANNELS:
        await repo.get_channel_config(channel_id, GUILD)

    await repo.update_channel_config(b, is_enabled=False)

    enabled = await repo.get_enabled_channels(GUILD)

    assert a in enabled
    assert b not in enabled
    assert c in enabled


@pytest.mark.asyncio
async def test_record_message_activity(repo: Repository):
    """Messages are buffered, then written as one batched upsert"""
    assert repo.pool is not None
    timestamp = int(time.time())
    query = "SELECT message_count FROM message_activity WHERE channel_id = $1 AND timestamp = $2"

    for _ in range(3):
        await repo.record_message_activity(CHANNEL, timestamp)

    # No per-message DB write
    assert await repo.pool.fetchval(query, CHANNEL, timestamp) is None

    await repo.flush_message_activity()
    assert await repo.pool.fetchval(query, CHANNEL, timestamp) == 3

    # A later batch adds to the existing row
    await repo.record_message_activity(CHANNEL, timestamp)
    await repo.flush_message_activity()
    assert await repo.pool.fetchval(query, CHANNEL, timestamp) == 4


@pytest.mark.asyncio
async def test_reads_see_buffered_activity(repo: Repository):
    """Slowmode decisions and analytics see messages that haven't been flushed yet"""
    now = int(time.time())
    for i in range(30):
        await repo.record_message_activity(CHANNEL, now - i)

    assert await repo.get_message_rate(CHANNEL, window_seconds=60) == 30


@pytest.mark.asyncio
async def test_failed_flush_keeps_counts(repo: Repository):
    """A flush that fails puts its counts back for the next attempt"""
    assert repo.pool is not None
    timestamp = int(time.time())
    await repo.record_message_activity(CHANNEL, timestamp)

    pool = repo.pool
    repo.pool = _FailingPool()  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        await repo.flush_message_activity()
    await repo.record_message_activity(CHANNEL, timestamp)
    repo.pool = pool

    await repo.flush_message_activity()
    count = await repo.pool.fetchval(
        "SELECT message_count FROM message_activity WHERE channel_id = $1", CHANNEL
    )
    assert count == 2


@pytest.mark.asyncio
async def test_close_flushes_buffer():
    """Buffered counts are written on shutdown"""
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")

    repo = Repository(dsn)
    await repo.init()
    await repo.record_message_activity(CHANNEL, 1_700_000_000)
    await repo.close()

    conn = await asyncpg.connect(dsn)
    try:
        count = await conn.fetchval(
            "SELECT message_count FROM message_activity WHERE timestamp = 1700000000"
        )
    finally:
        await conn.execute("DELETE FROM message_activity WHERE timestamp = 1700000000")
        await conn.close()
    assert count == 1


class _FailingPool:
    async def execute(self, *args, **kwargs):
        raise RuntimeError("database down")


@pytest.mark.asyncio
async def test_get_message_rate(repo: Repository):
    """Test calculating message rate"""
    current_time = int(time.time())

    for i in range(60):
        await repo.record_message_activity(CHANNEL, current_time - i)

    rate = await repo.get_message_rate(CHANNEL, window_seconds=60)

    assert 59 <= rate <= 61  # Allow small rounding differences


@pytest.mark.asyncio
async def test_get_message_rate_no_data(repo: Repository):
    assert await repo.get_message_rate(CHANNEL) == 0


@pytest.mark.asyncio
async def test_cleanup_old_message_activity(repo: Repository):
    """Test cleanup of old message activity"""
    assert repo.pool is not None
    current_time = int(time.time())

    await repo.record_message_activity(CHANNEL, current_time - (25 * 3600))
    await repo.record_message_activity(CHANNEL, current_time - (1 * 3600))

    await repo.cleanup_old_message_activity(hours=24)

    count = await repo.pool.fetchval(
        "SELECT COUNT(*) FROM message_activity WHERE channel_id = $1", CHANNEL
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_slowmode_change(repo: Repository):
    """Test recording slowmode changes"""
    assert repo.pool is not None

    await repo.record_slowmode_change(
        channel_id=CHANNEL,
        old_value=0,
        new_value=30,
        reason="High activity detected",
        message_rate=25.5,
        confidence=0.85,
    )

    row = await repo.pool.fetchrow("SELECT * FROM slowmode_changes WHERE channel_id = $1", CHANNEL)

    assert row is not None
    assert row["old_value"] == 0
    assert row["new_value"] == 30
    assert row["reason"] == "High activity detected"
    assert row["message_rate"] == 25.5
    assert row["confidence"] == 0.85


@pytest.mark.asyncio
async def test_expected_activity_no_data(repo: Repository):
    """Test getting expected activity with no historical data"""
    result = await repo.get_expected_activity(channel_id=CHANNEL, day_of_week=1, hour=14)

    assert result is None


@pytest.mark.asyncio
async def test_update_and_get_channel_pattern(repo: Repository):
    """Test updating and retrieving channel patterns, including the upsert path"""
    await repo.update_channel_pattern(
        CHANNEL, 1, 14, avg_rate=15.5, stddev_rate=3.2, sample_count=50
    )
    assert await repo.get_expected_activity(CHANNEL, 1, 14) == 15.5

    await repo.update_channel_pattern(
        CHANNEL, 1, 14, avg_rate=17.25, stddev_rate=3.0, sample_count=51
    )
    assert await repo.get_expected_activity(CHANNEL, 1, 14) == 17.25


@pytest.mark.asyncio
async def test_record_slowmode_effectiveness(repo: Repository):
    """Test recording slowmode effectiveness"""
    assert repo.pool is not None

    await repo.record_slowmode_effectiveness(
        channel_id=CHANNEL, slowmode_value=30, rate_before=25.0, rate_after=10.0, duration=300
    )

    row = await repo.pool.fetchrow(
        "SELECT * FROM slowmode_effectiveness WHERE channel_id = $1", CHANNEL
    )

    assert row is not None
    assert row["slowmode_value"] == 30
    assert row["message_rate_before"] == 25.0
    assert row["message_rate_after"] == 10.0
    assert row["was_effective"] is True  # 60% reduction


@pytest.mark.asyncio
async def test_get_effectiveness_score(repo: Repository):
    """Test calculating effectiveness score"""
    await repo.record_slowmode_effectiveness(CHANNEL, 30, 25.0, 10.0, 300)  # Effective
    await repo.record_slowmode_effectiveness(CHANNEL, 30, 25.0, 23.0, 300)  # Not effective
    await repo.record_slowmode_effectiveness(CHANNEL, 30, 25.0, 8.0, 300)  # Effective

    score = await repo.get_effectiveness_score(CHANNEL)

    assert isinstance(score, float)
    assert 0.65 <= score <= 0.68  # 2/3


@pytest.mark.asyncio
async def test_get_effectiveness_score_no_data(repo: Repository):
    assert await repo.get_effectiveness_score(CHANNEL) == 0.0


@pytest.mark.asyncio
async def test_aggregate_hourly_analytics(repo: Repository):
    """Test aggregating hourly analytics, including the upsert path"""
    last_hour = (int(time.time()) // 3600) * 3600 - 3600

    for i in range(10):
        await repo.record_message_activity(CHANNEL, last_hour + (i * 60))

    await repo.aggregate_hourly_analytics(CHANNEL)

    analytics = await repo.get_channel_analytics(CHANNEL, hours_back=2)
    assert len(analytics) == 1
    assert analytics[0]["total_messages"] == 10

    await repo.record_message_activity(CHANNEL, last_hour + 700)
    await repo.aggregate_hourly_analytics(CHANNEL)

    analytics = await repo.get_channel_analytics(CHANNEL, hours_back=2)
    assert len(analytics) == 1
    assert analytics[0]["total_messages"] == 11


@pytest.mark.asyncio
async def test_cleanup_old_analytics(repo: Repository):
    """Test cleanup of old analytics data"""
    assert repo.pool is not None
    old_timestamp = int(time.time()) - (31 * 86400)

    await repo.pool.execute(
        """INSERT INTO channel_analytics
           (channel_id, hour_timestamp, total_messages, unique_users, avg_slowmode, max_slowmode)
           VALUES ($1, $2, 100, 10, 30, 60)""",
        CHANNEL,
        old_timestamp,
    )

    await repo.cleanup_old_analytics(days=30)

    analytics = await repo.get_channel_analytics(CHANNEL, hours_back=32 * 24)
    assert len(analytics) == 0


@pytest.mark.asyncio
async def test_module_config_roundtrip(repo: Repository):
    """Default creation, enable/disable upsert, settings JSON, enabled list"""
    config = await repo.get_module_config(GUILD, ModuleType.SLOWMODE)
    assert config.guild_id == GUILD
    assert config.is_enabled is False
    assert config.settings == {}

    await repo.set_module_enabled(GUILD, ModuleType.SLOWMODE, True)
    assert await repo.get_enabled_modules(GUILD) == [ModuleType.SLOWMODE]

    await repo.update_module_settings(GUILD, ModuleType.SLOWMODE, {"threshold": 12})
    config = await repo.get_module_config(GUILD, ModuleType.SLOWMODE)
    assert config.is_enabled is True
    assert config.settings == {"threshold": 12}

    await repo.set_module_enabled(GUILD, ModuleType.SLOWMODE, False)
    assert await repo.get_enabled_modules(GUILD) == []


@pytest.mark.asyncio
async def test_database_not_initialized_error():
    """Test that operations fail if database not initialised"""
    repo = Repository()

    with pytest.raises(DatabaseError, match="Database connection is not initialised"):
        await repo.get_guild_config(123)
