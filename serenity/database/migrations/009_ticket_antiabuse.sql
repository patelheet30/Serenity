-- Ticket anti-abuse (#66). Its settings live in module_config.settings; these back its lookups.
-- The open cooldown: a member's latest ticket in a guild, open or closed.
CREATE INDEX idx_tickets_opener_opened ON tickets (guild_id, opener_id, opened_at);
-- The raid brake: tickets opened in a guild within the window.
CREATE INDEX idx_tickets_opened ON tickets (guild_id, opened_at);
