"""Shared test fixtures and configuration"""

import os
from unittest.mock import AsyncMock

import asyncpg
import pytest
import pytest_asyncio

from serenity.core.types import ChannelConfig, GuildConfig
from serenity.database.repository import Repository


@pytest.fixture
def mock_repo():
    """Mock repository with sensible defaults"""
    repo = AsyncMock(spec=Repository)

    # Default guild config
    repo.get_guild_config.return_value = GuildConfig(
        guild_id=1, is_enabled=True, default_threshold=10, update_interval=30
    )

    # Default channel config
    repo.get_channel_config.return_value = ChannelConfig(
        channel_id=123, guild_id=1, is_enabled=True, threshold=None
    )

    # Default activity - using correct method names
    repo.get_message_rate.return_value = 5.0
    repo.get_expected_activity.return_value = None
    repo.get_effectiveness_score.return_value = 0.0

    return repo


@pytest_asyncio.fixture
async def pg_repo():
    """Repository on a real, wiped Postgres database.

    Set TEST_DATABASE_URL (e.g. postgresql:///serenity_test) to run these tests.
    The public schema of that database is dropped before every test.
    """
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    await conn.close()

    repo = Repository(dsn)
    await repo.init()

    yield repo

    await repo.close()


@pytest.fixture
def sample_guild_config():
    """Sample guild configuration"""
    return GuildConfig(
        guild_id=123456789, is_enabled=True, default_threshold=10, update_interval=30
    )


@pytest.fixture
def sample_channel_config():
    """Sample channel configuration"""
    return ChannelConfig(channel_id=987654321, guild_id=123456789, is_enabled=True, threshold=15)


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line("markers", "unit: unit tests")
    config.addinivalue_line("markers", "integration: integration tests")
    config.addinivalue_line("markers", "slow: slow running tests")
