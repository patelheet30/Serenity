-- Cases for moderation actions Serenity didn't take (#99), read from Discord's audit log.
-- external_source is NULL for Serenity's own cases, else who acted: a bot's name, or 'Discord'
-- for a person using the Discord client.
ALTER TABLE mod_cases ADD COLUMN external_source TEXT;
