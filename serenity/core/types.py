from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional


@dataclass
class SlowmodeDecision:
    """Decision about slowmode setting"""

    slowmode_seconds: int
    confidence: float
    reasoning: str
    factors: Dict[str, float]
    should_notify: bool


@dataclass
class ChannelStats:
    """Statistics for a channel"""

    channel_id: int
    total_messages: int
    unique_users: int
    avg_message_rate: float
    peak_hour: int
    effectiveness_score: float
    last_updated: datetime


@dataclass
class SlowmodeContext:
    """Context needed for slowmode calculation"""

    channel_id: int
    guild_id: int
    current_rate: float
    threshold: int
    current_slowmode: int
    historical_rates: Optional[float] = None


@dataclass
class ChannelConfig:
    """Channel configuration"""

    channel_id: int
    guild_id: int
    is_enabled: bool
    threshold: Optional[int]


@dataclass
class GuildConfig:
    """Guild configuration"""

    guild_id: int
    is_enabled: bool
    default_threshold: int
    update_interval: int


@dataclass
class MessageActivity:
    """Message activity data point"""

    channel_id: int
    timestamp: int
    message_count: int


class ModAction(Enum):
    """Moderation action types"""

    WARN = "warn"
    KICK = "kick"
    BAN = "ban"
    UNBAN = "unban"
    TIMEOUT = "timeout"
    UNTIMEOUT = "untimeout"


@dataclass
class ModCase:
    """Moderation case data"""

    id: int
    guild_id: int
    case_number: int
    target_user_id: int
    moderator_id: int
    action: ModAction
    reason: str
    duration_seconds: Optional[int]
    created_at: int
    expired_at: Optional[int]
    is_active: bool


class AutomodCategory(Enum):
    """Content hazards the automod battery asks about."""

    HATE = "hate"
    PROFANITY = "profanity"
    VIOLENCE = "violence"
    SPAM = "spam"
    ILLEGAL = "illegal"
    SEXUAL = "sexual"
    SELF_HARM = "self_harm"


class AutomodVerdict(Enum):
    """What the thresholds say should happen to a message."""

    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


@dataclass(frozen=True)
class ContextMessage:
    """An earlier message from the same channel, kept as context."""

    author_id: int
    content: str


@dataclass(frozen=True)
class AutomodAssessment:
    """One message scored by the automod model.

    Probabilities are independent per category, so they do not sum to 1.
    """

    scores: Dict[AutomodCategory, float]
    severity: float
    severity_confidence: float
    verdict: AutomodVerdict
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    request_id: Optional[str] = None
    context_size: int = 0
    reason: str = ""

    @property
    def ranked(self) -> list[tuple[AutomodCategory, float]]:
        """Categories from most to least likely."""
        return sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)

    @property
    def top_category(self) -> Optional[AutomodCategory]:
        """The highest-scoring category, or None when nothing was scored."""
        ranked = self.ranked
        return ranked[0][0] if ranked else None

    @property
    def top_score(self) -> float:
        """The highest category probability, 0.0 when nothing was scored."""
        ranked = self.ranked
        return ranked[0][1] if ranked else 0.0
