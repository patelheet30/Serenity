-- Dashboard login sessions (#34). The cookie holds a random token; only its SHA-256 is stored.
CREATE TABLE dashboard_sessions (
    id_hash    BYTEA PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    username   TEXT NOT NULL,
    avatar     TEXT,
    tokens     BYTEA NOT NULL,  -- Fernet-encrypted JSON: Discord access/refresh token + expiry
    created_at BIGINT NOT NULL,
    last_seen  BIGINT NOT NULL
);

CREATE INDEX idx_dashboard_sessions_last_seen ON dashboard_sessions (last_seen);  -- expiry cleanup
