-- Escalation tiers (#62): a ladder per ticket type. A ticket starts on its type's lowest tier
-- (tickets.tier_id NULL); escalating adds a tier's roles to the channel and pings them.
CREATE TABLE ticket_tiers (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_id         BIGINT NOT NULL REFERENCES ticket_types (id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    position        INTEGER NOT NULL,                    -- rung on the ladder: "up" is the next one
    role_ids        BIGINT[] NOT NULL DEFAULT '{}',
    can_summon      BIGINT[] NOT NULL DEFAULT '{}',      -- tiers it may jump to besides the next rung
    requires_reason BOOLEAN NOT NULL DEFAULT FALSE,
    reping_minutes  INTEGER,                             -- NULL: never re-ping
    created_at      BIGINT NOT NULL,
    UNIQUE (type_id, position),
    CHECK (reping_minutes IS NULL OR reping_minutes > 0)
);

CREATE UNIQUE INDEX idx_ticket_tiers_name ON ticket_tiers (type_id, lower(name));

ALTER TABLE tickets
    ADD COLUMN tier_id        BIGINT REFERENCES ticket_tiers (id) ON DELETE SET NULL,
    ADD COLUMN tier_pinged_at BIGINT;                    -- last ping of the tier; NULL once picked up

-- The re-ping sweep: escalations nobody from the tier has answered yet.
CREATE INDEX idx_tickets_tier_pending ON tickets (tier_pinged_at)
    WHERE status = 'open' AND tier_pinged_at IS NOT NULL;
