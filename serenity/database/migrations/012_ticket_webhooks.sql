-- Outgoing ticket webhooks (#68): signed POSTs of ticket events to a server's own endpoint.
CREATE TABLE ticket_webhooks (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id   BIGINT NOT NULL,
    url        TEXT NOT NULL,                          -- https only; checked against private IPs
    secret     TEXT NOT NULL,                          -- HMAC-SHA256 key, shown once when added
    events     TEXT[] NOT NULL DEFAULT '{}',           -- e.g. {ticket.opened}; empty: every event
    type_id    BIGINT REFERENCES ticket_types (id) ON DELETE CASCADE,  -- NULL: every type
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_ticket_webhooks_guild ON ticket_webhooks (guild_id, id);

-- One row per event sent to a webhook, updated after each attempt; kept 7 days.
CREATE TABLE ticket_webhook_deliveries (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    webhook_id BIGINT NOT NULL REFERENCES ticket_webhooks (id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    status     INTEGER,                                -- last HTTP status; NULL: no response
    latency_ms INTEGER,
    error      TEXT,                                   -- NULL once delivered
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_ticket_webhook_deliveries_webhook ON ticket_webhook_deliveries (webhook_id, id);
CREATE INDEX idx_ticket_webhook_deliveries_created ON ticket_webhook_deliveries (created_at);
