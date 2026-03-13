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
