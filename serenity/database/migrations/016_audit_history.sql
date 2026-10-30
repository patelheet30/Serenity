-- Audit history module (#154): the audit log entries Serenity receives, kept past Discord's
-- 45 days for servers that turn the module on. Pruned to each server's retention_days.
CREATE TABLE audit_log_entries (
    guild_id    BIGINT NOT NULL,
    entry_id    BIGINT NOT NULL,
    action_type INTEGER NOT NULL,
    actor_id    BIGINT,
    target_id   BIGINT,
    reason      TEXT,
    changes     JSONB NOT NULL DEFAULT '[]',
    options     JSONB,
    created_at  BIGINT NOT NULL,
    PRIMARY KEY (guild_id, entry_id)
);

CREATE INDEX idx_audit_log_entries_created ON audit_log_entries (guild_id, created_at);
CREATE INDEX idx_audit_log_entries_actor ON audit_log_entries (guild_id, actor_id, created_at);
CREATE INDEX idx_audit_log_entries_target ON audit_log_entries (guild_id, target_id, created_at);
