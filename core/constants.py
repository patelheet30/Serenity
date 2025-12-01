from dataclasses import dataclass
from typing import Tuple


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


SLOWMODE_CONFIG = SlowmodeConfig()
DATABASE_CONFIG = DatabaseConfig()
