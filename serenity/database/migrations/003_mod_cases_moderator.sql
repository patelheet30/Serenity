-- The dashboard's case table filters by moderator (#47).
CREATE INDEX idx_mod_cases_moderator ON mod_cases (guild_id, moderator_id);
