-- The dashboard's case table filters by moderator, and by "active" (#47).
CREATE INDEX idx_mod_cases_moderator ON mod_cases (guild_id, moderator_id);
CREATE INDEX idx_mod_cases_active ON mod_cases (guild_id, expires_at)
    WHERE action IN ('warn', 'timeout') AND is_active;
