from prometheus_client import Counter, Gauge, Histogram, Info

BOT_INFO = Info("serenity_bot", "Serenity bot Information")

MESSAGES_PROCESSED = Counter(
    "serenity_messages_processed_total",
    "Total number of messages processed by the bot",
    ["guild_id"],
)

SLOWMODE_CHANGES = Counter(
    "serenity_slowmode_changes_total",
    "Total number of slowmode changes made by the bot",
    ["direction"],
)

SLOWMODE_CURRENT = Gauge(
    "serenity_slowmode_current_seconds",
    "Current slowmode duration in seconds for each channel",
    ["channel_id", "guild_id"],
)

ENGINE_CALCULATION_DURATION = Histogram(
    "serenity_engine_calculation_seconds",
    "Time spent calculating slowmode decisions",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

ENGINE_URGENCY_SCORE = Histogram(
    "serenity_engine_urgency_score",
    "Distribution of calculated urgency scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

ENGINE_DECISIONS = Counter(
    "serenity_engine_decisions_total",
    "Total slowmode engine decisions",
    ["outcome"],  # changed, unchanged
)

ENGINE_CONFIDENCE = Histogram(
    "serenity_engine_confidence",
    "Distribution of decision confidence scores",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

MESSAGE_RATE = Gauge(
    "serenity_message_rate_per_minute",
    "Current message rate per minute for a channel",
    ["channel_id", "guild_id"],
)

EFFECTIVENESS_SCORE = Gauge(
    "serenity_effectiveness_score",
    "Slowmode effectiveness score for a channel (0-1)",
    ["channel_id"],
)

ACTIVE_GUILDS = Gauge(
    "serenity_active_guilds",
    "Number of guilds with Serenity enabled",
)

ACTIVE_CHANNELS = Gauge(
    "serenity_active_channels",
    "Number of channels with slowmode enabled",
)

TASK_DURATION = Histogram(
    "serenity_task_duration_seconds",
    "Duration of background task executions",
    ["task_name"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)

TASK_ERRORS = Counter(
    "serenity_task_errors_total",
    "Total background task errors",
    ["task_name"],
)

LOG_EVENTS_SENT = Counter(
    "serenity_log_events_sent_total",
    "Audit-log embeds posted to a server's log channel",
    ["guild_id", "event"],
)

# Every log_event LoggingService can send (mod_* comes from ModerationService actions,
# external_* from the ones Serenity didn't take)
LOG_EVENTS = (
    "message_delete", "message_edit", "bulk_message_delete",
    "member_join", "member_leave", "member_update", "voice_state_update",
    "channel_create", "channel_delete", "channel_update",
    "role_create", "role_delete", "role_update", "guild_update", "emoji_update",
    "dashboard_change", "ticket_raid_brake",
    "mod_ban", "mod_softban", "mod_unban", "mod_kick", "mod_timeout", "mod_untimeout", "mod_warn",
    "external_ban", "external_unban", "external_kick", "external_timeout", "external_untimeout",
)

COMMANDS_USED = Counter(
    "serenity_commands_used_total",
    "Slash command invocations",
    ["command"],
)


def init_guild_log_events(guild_id: int) -> None:
    """Create a guild's log-event series at 0.

    A series that first appears at 1 reads as no increase to increase()/rate(), so without this
    the first log of each event in each guild never shows up in Grafana.
    """
    # ponytail: guilds x len(LOG_EVENTS) series even for guilds without logging; only pre-create
    # for guilds with a log channel configured if the series count ever matters
    for event in LOG_EVENTS:
        LOG_EVENTS_SENT.labels(guild_id=str(guild_id), event=event)
