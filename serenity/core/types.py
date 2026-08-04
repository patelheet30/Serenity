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
