-- Tickets core (milestone "Tickets core", #50). Workflow tables (tiers, support hours,
-- webhooks, ratings) and mod_cases.source_ticket_id come with the features that use them
-- in "Tickets workflows".
-- Server-wide ticket settings (transcript retention) live in module_config.settings.

-- What a member can open: its form, where its channels go and who staffs it.
CREATE TABLE ticket_types (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id           BIGINT NOT NULL,
    name               TEXT NOT NULL,
    emoji              TEXT,
    description        TEXT,
    category_id        BIGINT,                         -- NULL: channels go at the top level
    staff_role_ids     BIGINT[] NOT NULL DEFAULT '{}',
    fields             JSONB NOT NULL DEFAULT '[]',    -- [{key, label, style, required, max_length}], ≤5
    stages             JSONB NOT NULL DEFAULT '[]',
    claim_mode         TEXT NOT NULL DEFAULT 'reply',
    open_limit         INTEGER NOT NULL DEFAULT 1,     -- open tickets per member of this type
    name_template      TEXT NOT NULL DEFAULT '{type}-{number}',
    transcripts        BOOLEAN NOT NULL DEFAULT TRUE,
    retention_days     INTEGER,                        -- NULL: server default; 0: keep until deleted
    auto_unclaim_hours INTEGER,                        -- NULL: never
    created_at         BIGINT NOT NULL,
    CHECK (claim_mode IN ('reply', 'button', 'both')),
    CHECK (open_limit >= 1),
    CHECK (retention_days IS NULL OR retention_days = 0 OR retention_days BETWEEN 7 AND 365)
);

-- Names are looked up case-insensitively, so they're unique case-insensitively too.
CREATE UNIQUE INDEX idx_ticket_types_name ON ticket_types (guild_id, lower(name));

-- A posted message with one button per type, or a dropdown of them.
CREATE TABLE ticket_panels (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    channel_id  BIGINT NOT NULL,
    message_id  BIGINT,                                -- NULL until posted, or after it was deleted
    title       TEXT NOT NULL,
    description TEXT,
    style       TEXT NOT NULL DEFAULT 'buttons',
    type_ids    BIGINT[] NOT NULL DEFAULT '{}',
    created_at  BIGINT NOT NULL,
    CHECK (style IN ('buttons', 'menu'))
);

CREATE INDEX idx_ticket_panels_guild ON ticket_panels (guild_id);
CREATE INDEX idx_ticket_panels_message ON ticket_panels (message_id);

CREATE TABLE tickets (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id          BIGINT NOT NULL,
    number            INTEGER NOT NULL,
    type_id           BIGINT NOT NULL REFERENCES ticket_types (id),
    channel_id        BIGINT,                          -- NULL while being created and once closed
    opener_id         BIGINT NOT NULL,
    assignee_id       BIGINT,
    priority          TEXT NOT NULL DEFAULT 'normal',
    status            TEXT NOT NULL DEFAULT 'open',
    stage             TEXT,
    waiting_on        TEXT NOT NULL DEFAULT 'staff',   -- who spoke last: 'staff' means the opener did
    fields            JSONB NOT NULL DEFAULT '{}',     -- form answers by field key
    opened_at         BIGINT NOT NULL,
    first_response_at BIGINT,
    last_activity_at  BIGINT NOT NULL,
    closed_at         BIGINT,
    closed_by         BIGINT,
    close_reason      TEXT,
    UNIQUE (guild_id, number),
    CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CHECK (status IN ('open', 'closed')),
    CHECK (waiting_on IN ('staff', 'opener'))
);

CREATE UNIQUE INDEX idx_tickets_channel ON tickets (channel_id) WHERE channel_id IS NOT NULL;
CREATE INDEX idx_tickets_queue ON tickets (guild_id, status);
CREATE INDEX idx_tickets_type_stage ON tickets (guild_id, type_id, stage);
CREATE INDEX idx_tickets_opener ON tickets (guild_id, opener_id, type_id) WHERE status = 'open';

-- Everyone who handled a ticket. `credited` is set on close for handlers who sent a message.
CREATE TABLE ticket_handlers (
    ticket_id     BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL,
    role          TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    joined_at     BIGINT NOT NULL,
    credited      BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (ticket_id, user_id),
    CHECK (role IN ('claimed', 'assisted'))
);

CREATE TABLE ticket_events (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id  BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                          -- opened, claimed, unclaimed, closed, ...
    actor_id   BIGINT,                                 -- NULL for the bot itself
    data       JSONB NOT NULL DEFAULT '{}',
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_ticket_events_ticket ON ticket_events (ticket_id, created_at);

-- Staff-only notes: shown in the dashboard and the transcript's staff section, never to the opener.
CREATE TABLE ticket_notes (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id  BIGINT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    author_id  BIGINT NOT NULL,
    body       TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE INDEX idx_ticket_notes_ticket ON ticket_notes (ticket_id);

-- One readable record per closed ticket: the one place Serenity keeps message content.
CREATE TABLE ticket_transcripts (
    ticket_id             BIGINT PRIMARY KEY REFERENCES tickets (id) ON DELETE CASCADE,
    guild_id              BIGINT NOT NULL,
    document              JSONB NOT NULL,
    created_at            BIGINT NOT NULL,
    expires_at            BIGINT,                      -- NULL: keep until deleted
    held_by_case_number   INTEGER,                     -- evidence for a mod case: never expires
    deletion_requested_at BIGINT
);

CREATE INDEX idx_ticket_transcripts_expiry ON ticket_transcripts (expires_at)
    WHERE expires_at IS NOT NULL AND held_by_case_number IS NULL;
CREATE INDEX idx_ticket_transcripts_deletion_requests ON ticket_transcripts (guild_id)
    WHERE deletion_requested_at IS NOT NULL;

CREATE TABLE ticket_blacklist (
    guild_id     BIGINT NOT NULL,
    user_id      BIGINT NOT NULL,
    moderator_id BIGINT NOT NULL,
    reason       TEXT,
    created_at   BIGINT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

