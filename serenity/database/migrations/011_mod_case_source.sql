-- Cases made with a ticket's Mod action (#67) link back to it; closing that ticket holds its
-- transcript as the case's evidence (ticket_transcripts.held_by_case_number).
-- The SQLite import copies only the columns SQLite has, so this one stays NULL there.
ALTER TABLE mod_cases ADD COLUMN source_ticket_id BIGINT REFERENCES tickets (id) ON DELETE SET NULL;

CREATE INDEX idx_mod_cases_source_ticket ON mod_cases (source_ticket_id)
    WHERE source_ticket_id IS NOT NULL;
