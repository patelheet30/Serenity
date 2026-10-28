-- The ticket log type (#57): opened, claimed, closed, reopened and deletion requests.
ALTER TABLE log_channels DROP CONSTRAINT log_channels_log_type_check;
ALTER TABLE log_channels ADD CONSTRAINT log_channels_log_type_check
    CHECK (log_type IN ('member', 'message', 'voice', 'server', 'mod', 'ticket'));

ALTER TABLE log_ignores DROP CONSTRAINT log_ignores_log_type_check;
ALTER TABLE log_ignores ADD CONSTRAINT log_ignores_log_type_check
    CHECK (log_type IN ('*', 'member', 'message', 'voice', 'server', 'mod', 'ticket'));
