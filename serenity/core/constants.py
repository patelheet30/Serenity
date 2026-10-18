from dataclasses import dataclass
from typing import Dict, Tuple

from serenity.core.types import AutomodCategory


@dataclass(frozen=True)
class SlowmodeConfig:
    """Configuration for slowmode settings."""

    MIN_SLOWMODE: int = 0
    MAX_SLOWMODE: int = 21600
    DEFAULT_THRESHOLD: int = 10

    ANALYSIS_WINDOWS: Tuple[int, ...] = (60, 300, 900, 3600)

    SLOWMODE_CHECK_INTERVAL: int = 60
    ANALYTICS_AGGREGATION_INTERVAL: int = 300

    CURRENT_RATE_WEIGHT: float = 0.4
    HISTORICAL_WEIGHT: float = 0.3
    VELOCITY_WEIGHT: float = 0.2
    EFFECTIVENESS_WEIGHT: float = 0.1

    HYSTERESIS_THRESHOLD: int = 5
    MAX_CHANGE_PER_UPDATE: int = 30

    MESSAGE_ACTIVITY_RETENTION_HOURS: int = 24
    ANALYTICS_RETENTION_DAYS: int = 30
    PATTERN_UPDATE_MIN_SAMPLES: int = 100


@dataclass(frozen=True)
class DatabaseConfig:
    """Database configuration settings."""

    DEFAULT_PATH: str = "data/serenity.db"
    POOL_SIZE: int = 5
    TIMEOUT: int = 30


@dataclass(frozen=True)
class CategoryPolicy:
    """When one hazard category is allowed to act.

    `severity_floor` gates both outcomes: however sure the model is that a
    message belongs to this category, nothing fires unless it also judges the
    message capable of doing that much harm. That is what keeps confident-but-
    harmless flags — "life is a bitch" is 96% profanity — out of the mod queue.
    """

    review_at: float
    action_at: float
    severity_floor: float


# A threshold per category, because one number cannot serve all seven. Casual
# swearing is ordinary on Discord and should never delete a message on its own,
# while a slur aimed at someone should act well below the profanity bar. Set
# `action_at` above 1.0 to make a category incapable of blocking.
CATEGORY_POLICIES: Dict["AutomodCategory", CategoryPolicy] = {
    # Never blocks alone; only surfaces when it is also judged harmful.
    AutomodCategory.PROFANITY: CategoryPolicy(
        review_at=0.90, action_at=1.01, severity_floor=1.0
    ),
    AutomodCategory.HATE: CategoryPolicy(review_at=0.50, action_at=0.85, severity_floor=1.0),
    AutomodCategory.VIOLENCE: CategoryPolicy(review_at=0.60, action_at=0.90, severity_floor=1.0),
    AutomodCategory.SEXUAL: CategoryPolicy(review_at=0.70, action_at=0.95, severity_floor=1.0),
    AutomodCategory.ILLEGAL: CategoryPolicy(review_at=0.70, action_at=0.95, severity_floor=1.0),
    # Scams do real damage while scoring low on harm, so no severity floor.
    AutomodCategory.SPAM: CategoryPolicy(review_at=0.75, action_at=0.95, severity_floor=0.0),
    # Never blocks: someone in trouble needs a moderator, not a deleted message.
    AutomodCategory.SELF_HARM: CategoryPolicy(
        review_at=0.60, action_at=1.01, severity_floor=0.0
    ),
}


@dataclass(frozen=True)
class AutomodConfig:
    """Configuration for the TypeSafe-backed automod."""

    MODEL: str = "jev-latest"

    # Severity is a 0-3 rubric score. Kept for display; the per-category
    # `severity_floor` above is what actually gates a verdict.
    SEVERITY_SCALE_MAX: float = 3.0

    # Never send more than this many characters of a message to the API.
    MAX_CONTENT_LENGTH: int = 1500
    MIN_CONTENT_LENGTH: int = 2

    # Preceding messages sent alongside, so a thought split across several
    # messages is read as one. Kept short: accuracy falls as the state fills
    # with detail unrelated to the decision.
    CONTEXT_WINDOW: int = 5
    CONTEXT_MAX_AGE: float = 300.0
    CONTEXT_MAX_LENGTH: int = 300

    REQUEST_TIMEOUT: float = 10.0
    MAX_RETRIES: int = 2

    # Ceiling on in-flight requests, shared across every guild.
    MAX_CONCURRENT_REQUESTS: int = 4

    # Jev allows 1,200 requests/minute account-wide. Staying under it is our
    # job: past the ceiling every call 429s and the SDK's backoff turns a busy
    # minute into a growing queue. The headroom absorbs retries.
    GLOBAL_REQUESTS_PER_MINUTE: int = 1000

    # The global budget is split fairly between the guilds currently sending
    # traffic, clamped to this range — so one busy server cannot starve the
    # rest, and a quiet one always keeps a small allowance of its own.
    MIN_GUILD_REQUESTS_PER_MINUTE: int = 5
    MAX_GUILD_REQUESTS_PER_MINUTE: int = 30


SLOWMODE_CONFIG = SlowmodeConfig()
DATABASE_CONFIG = DatabaseConfig()
AUTOMOD_CONFIG = AutomodConfig()
