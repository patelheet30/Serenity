-- Typed ticket fields (#60). Field definitions stay in ticket_types.fields:
-- [{key, label, type, required, max_length, options?, pattern?, identity?, staff_only?}]

-- Answers to identity fields (an IGN, an order ID), lowercased, so a member's tickets about the
-- same thing are linked across types. Fields with the same key match.
CREATE TABLE ticket_identity (
    ticket_id BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    guild_id  BIGINT NOT NULL,
    field_key TEXT NOT NULL,
    value     TEXT NOT NULL,
    PRIMARY KEY (ticket_id, field_key)
);

CREATE INDEX idx_ticket_identity_value ON ticket_identity (guild_id, field_key, value);
