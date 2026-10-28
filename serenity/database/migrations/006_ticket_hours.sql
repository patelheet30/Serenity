-- Support hours (#64). The server's schedule, timezone, away mode and auto-reply live in
-- module_config.settings; a type can have its own schedule and refuse tickets outside it.
ALTER TABLE ticket_types ADD COLUMN schedule JSONB;  -- [[[start, end] minutes, ...] × 7], Monday first; NULL: the server's
ALTER TABLE ticket_types ADD COLUMN open_off_hours BOOLEAN NOT NULL DEFAULT TRUE;
