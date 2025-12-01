-- Core configuration tables
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    is_enabled INTEGER DEFAULT 1,
    default_threshold INTEGER DEFAULT 10,
    update_interval INTEGER DEFAULT 30
);

CREATE TABLE IF NOT EXISTS channel_config (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    is_enabled INTEGER DEFAULT 1,
    threshold INTEGER DEFAULT NULL,
    FOREIGN KEY (guild_id) REFERENCES guild_config(guild_id)
);

CREATE TABLE IF NOT EXISTS message_activity (
    channel_id INTEGER NOT NULL,
    timestamp INTEGER NOT NULL,
    message_count INTEGER DEFAULT 1,
    PRIMARY KEY (channel_id, timestamp),
    FOREIGN KEY (channel_id) REFERENCES channel_config(channel_id)
);

-- Analytics tables
CREATE TABLE IF NOT EXISTS channel_patterns (
    channel_id INTEGER,
    day_of_week INTEGER,
    hour INTEGER,
    avg_message_rate REAL,
    stddev_message_rate REAL,
    sample_count INTEGER,
    last_updated INTEGER,
    PRIMARY KEY (channel_id, day_of_week, hour)
);

CREATE TABLE IF NOT EXISTS slowmode_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER,
    old_value INTEGER,
    new_value INTEGER,
    reason TEXT,
    message_rate REAL,
    confidence REAL,
    timestamp INTEGER,
    FOREIGN KEY (channel_id) REFERENCES channel_config(channel_id)
);

CREATE TABLE IF NOT EXISTS channel_analytics (
    channel_id INTEGER,
    hour_timestamp INTEGER,
    total_messages INTEGER,
    unique_users INTEGER,
    avg_slowmode INTEGER,
    max_slowmode INTEGER,
    PRIMARY KEY (channel_id, hour_timestamp)
);

CREATE TABLE IF NOT EXISTS slowmode_effectiveness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER,
    applied_at INTEGER,
    slowmode_value INTEGER,
    message_rate_before REAL,
    message_rate_after REAL,
    duration_seconds INTEGER,
    was_effective BOOLEAN,
    FOREIGN KEY (channel_id) REFERENCES channel_config(channel_id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_message_activity_channel_time 
ON message_activity(channel_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_channel_config_guild_enabled 
ON channel_config(guild_id, is_enabled);

CREATE INDEX IF NOT EXISTS idx_slowmode_changes_channel_time
ON slowmode_changes(channel_id, timestamp);

-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL
);