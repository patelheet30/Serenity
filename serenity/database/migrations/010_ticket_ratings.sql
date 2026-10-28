-- Ticket ratings (#63): the opener rates a closed ticket 1–5, once.

ALTER TABLE ticket_types ADD COLUMN ratings BOOLEAN NOT NULL DEFAULT TRUE;  -- ask openers to rate

CREATE TABLE ticket_ratings (
    ticket_id   BIGINT PRIMARY KEY REFERENCES tickets (id) ON DELETE CASCADE,  -- one per ticket
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,                       -- the opener
    rating      SMALLINT NOT NULL,
    comment     TEXT,
    handler_ids BIGINT[] NOT NULL DEFAULT '{}',        -- the credited handlers it counts for
    created_at  BIGINT NOT NULL,
    CHECK (rating BETWEEN 1 AND 5)
);

