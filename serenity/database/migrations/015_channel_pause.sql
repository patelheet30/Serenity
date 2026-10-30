-- A manual slowmode change on an auto-slowmode channel (#150) pauses auto-slowmode there until
-- this time (unix), so Serenity doesn't overwrite it. Enabling or disabling the channel clears it.
ALTER TABLE channel_config ADD COLUMN paused_until BIGINT;
