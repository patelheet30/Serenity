"""SQLite -> Postgres import (issue #16), against Postgres via the pg_repo fixture."""

import asyncio
import sqlite3
from contextlib import closing

import pytest

from serenity.database.import_sqlite import TABLES, cutover_if_needed, import_sqlite
from serenity.database.repository import Repository

GUILD = 1_358_559_883_535_843_348
CHANNEL = 1_495_150_853_122_953_379
USER = 818_538_625_498_480_720


def _old_sqlite_db(path) -> None:
    """A minimal copy of the production SQLite layout with its loose typing."""
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            """
            CREATE TABLE guild_config (guild_id INTEGER PRIMARY KEY, is_enabled INTEGER,
                default_threshold INTEGER, update_interval INTEGER);
            CREATE TABLE channel_config (channel_id INTEGER PRIMARY KEY, guild_id INTEGER,
                is_enabled INTEGER, threshold INTEGER);
            CREATE TABLE message_activity (channel_id INTEGER, timestamp INTEGER, message_count INTEGER);
            CREATE TABLE channel_patterns (channel_id INTEGER, day_of_week INTEGER, hour INTEGER,
                avg_message_rate REAL, stddev_message_rate REAL, sample_count INTEGER, last_updated INTEGER);
            CREATE TABLE slowmode_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER,
                old_value INTEGER, new_value INTEGER, reason TEXT, message_rate REAL, confidence REAL,
                timestamp INTEGER);
            CREATE TABLE channel_analytics (channel_id INTEGER, hour_timestamp INTEGER,
                total_messages INTEGER, unique_users INTEGER, avg_slowmode INTEGER, max_slowmode INTEGER);
            CREATE TABLE slowmode_effectiveness (id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER,
                applied_at INTEGER, slowmode_value INTEGER, message_rate_before REAL,
                message_rate_after REAL, duration_seconds INTEGER, was_effective BOOLEAN);
            CREATE TABLE module_config (guild_id INTEGER, module_type TEXT, is_enabled INTEGER,
                settings TEXT, updated_at INTEGER);
            CREATE TABLE mod_cases (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
                case_number INTEGER, target_user_id INTEGER, moderator_id INTEGER, action TEXT,
                reason TEXT, duration_sections TEXT, created_at INTEGER, expires_at INTEGER,
                is_active INTEGER);
            CREATE TABLE warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER,
                user_id INTEGER, moderator_id INTEGER, reason TEXT, case_id INTEGER,
                created_at INTEGER, is_active INTEGER);
            CREATE TABLE log_channels (guild_id INTEGER, log_type TEXT, channel_id INTEGER,
                is_enabled INTEGER, ignored_channels TEXT, ignored_users TEXT);
            CREATE TABLE log_ignores (guild_id INTEGER, target_type TEXT, target_id INTEGER,
                log_type TEXT, created_at INTEGER);
            """
        )
        db.execute("INSERT INTO guild_config VALUES (?, 0, 25, 60)", (GUILD,))
        db.execute("INSERT INTO channel_config VALUES (?, ?, 1, NULL)", (CHANNEL, GUILD))
        db.execute("INSERT INTO message_activity VALUES (?, 1792231869, 3)", (CHANNEL,))
        db.execute(
            "INSERT INTO channel_patterns VALUES (?, 6, 23, 22.08, 0.0, 1, 1792321851)", (CHANNEL,)
        )
        db.execute(
            "INSERT INTO slowmode_changes VALUES (3975, ?, 0, 12, 'busy', 21.0, 0.54, 1792258468)",
            (CHANNEL,),
        )
        db.execute(
            "INSERT INTO channel_analytics VALUES (?, 1792321200, 1339, 0, 0, 0)", (CHANNEL,)
        )
        db.execute(
            "INSERT INTO module_config VALUES (?, 'slowmode', 1, '{}', 1790525765)", (GUILD,)
        )
        db.execute(
            """INSERT INTO mod_cases VALUES
               (2, ?, 2, ?, 577180230956089384, 'timeout', 'spam', '180', 1785887392, 1785887572, 1)""",
            (GUILD, USER),
        )
        db.execute(
            "INSERT INTO warnings VALUES (1, ?, ?, 577180230956089384, 'spam', 2, 1785887393, 0)",
            (GUILD, USER),
        )
        db.execute(
            "INSERT INTO log_channels VALUES (?, 'message', ?, 1, '[]', '[]')", (GUILD, CHANNEL)
        )
        db.execute(
            "INSERT INTO log_ignores VALUES (?, 'channel', ?, 'message', 1787486280)",
            (GUILD, CHANNEL),
        )
        db.commit()


@pytest.mark.asyncio
async def test_import_copies_and_coerces(pg_repo: Repository, tmp_path):
    assert pg_repo.pool is not None
    path = tmp_path / "old.db"
    _old_sqlite_db(path)

    copied = await import_sqlite(str(path), pg_repo.dsn)

    assert copied == {
        **{t: 1 for t in TABLES},
        "slowmode_effectiveness": 0,
    }

    case = await pg_repo.pool.fetchrow("SELECT * FROM mod_cases")
    assert case["duration_sections"] == 180  # TEXT '180' in SQLite
    assert case["is_active"] is True
    assert case["target_user_id"] == USER

    config = await pg_repo.get_guild_config(GUILD)
    assert (config.is_enabled, config.default_threshold) == (False, 25)

    # Identity sequences continue after the imported ids
    await pg_repo.record_slowmode_change(CHANNEL, 12, 0, "quiet", 1.0, 0.5)
    assert await pg_repo.pool.fetchval("SELECT MAX(id) FROM slowmode_changes") == 3976


@pytest.mark.asyncio
async def test_import_refuses_non_empty_db(pg_repo: Repository, tmp_path):
    path = tmp_path / "old.db"
    _old_sqlite_db(path)
    await pg_repo.get_guild_config(1)  # creates a row

    with pytest.raises(RuntimeError, match="already has rows"):
        await import_sqlite(str(path), pg_repo.dsn)


def test_cutover_is_a_noop_without_the_old_file(tmp_path):
    assert cutover_if_needed(None) is False
    assert cutover_if_needed(str(tmp_path / "missing.db")) is False


@pytest.mark.asyncio
async def test_cutover_imports_once_and_keeps_rollback_copy(pg_repo: Repository, tmp_path):
    assert pg_repo.pool is not None
    path = tmp_path / "auto_slowmode.db"
    _old_sqlite_db(path)

    # cutover_if_needed calls asyncio.run, so run it off the test's event loop
    assert await asyncio.to_thread(cutover_if_needed, str(path), pg_repo.dsn) is True
    assert not path.exists()
    assert (tmp_path / "auto_slowmode.db.imported").exists()
    assert await pg_repo.pool.fetchval("SELECT COUNT(*) FROM guild_config") == 1

    assert await asyncio.to_thread(cutover_if_needed, str(path), pg_repo.dsn) is False


@pytest.mark.asyncio
async def test_cutover_finishes_after_crash_before_rename(pg_repo: Repository, tmp_path):
    """Import committed but the process died before renaming: next start just renames."""
    assert pg_repo.pool is not None
    path = tmp_path / "auto_slowmode.db"
    _old_sqlite_db(path)
    await import_sqlite(str(path), pg_repo.dsn)  # committed, file not renamed
    assert path.exists()

    assert await asyncio.to_thread(cutover_if_needed, str(path), pg_repo.dsn) is False
    assert not path.exists()
    assert (tmp_path / "auto_slowmode.db.imported").exists()
    assert await pg_repo.pool.fetchval("SELECT COUNT(*) FROM guild_config") == 1  # not doubled
    assert await import_sqlite(str(tmp_path / "auto_slowmode.db.imported"), pg_repo.dsn) is None
