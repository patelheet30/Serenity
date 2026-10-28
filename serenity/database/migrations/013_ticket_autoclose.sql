-- Auto-close on inactivity (#65).
ALTER TABLE ticket_types ADD COLUMN auto_close_hours INTEGER;  -- NULL: never
-- When the opener was warned; a warning older than last_activity_at no longer counts.
ALTER TABLE tickets ADD COLUMN close_warned_at BIGINT;

-- Auto-close's sweep: open tickets waiting on their opener, oldest activity first.
CREATE INDEX idx_tickets_awaiting_opener ON tickets (last_activity_at)
    WHERE status = 'open' AND waiting_on = 'opener';
