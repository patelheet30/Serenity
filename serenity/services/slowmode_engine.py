import math
import time

from serenity.core.constants import SLOWMODE_CONFIG
from serenity.core.types import SlowmodeContext, SlowmodeDecision
from serenity.database.repository import Repository
from serenity.utils.logging import get_logger

logger = get_logger(__name__)


class SlowmodeEngine:
    """Intelligent slowmode calculation engine."""

    def __init__(self, repository: Repository):
        self.repo = repository
        self.config = SLOWMODE_CONFIG

    async def _build_context(self, channel_id: int, guild_id: int) -> SlowmodeContext:
        """Build context for calculation"""
        channel_config = await self.repo.get_channel_config(channel_id, guild_id)
        guild_config = await self.repo.get_guild_config(guild_id)

        current_rate = await self.repo.get_message_rate(channel_id, 60)

        threshold = channel_config.threshold or guild_config.default_threshold

        now = time.localtime()
        historical_rate = await self.repo.get_expected_activity(
            channel_id, now.tm_wday, now.tm_hour
        )

        return SlowmodeContext(
            channel_id=channel_id,
            guild_id=guild_id,
            current_rate=current_rate,
            threshold=threshold,
            current_slowmode=0,
            historical_rates=historical_rate,
        )

    def _normalise(self, value: float, max_value: float = 5.0) -> float:
        """Normalise a value to a 0.0 - 1.0 scale"""
        return min(value / max_value, 1.0)

    def _calculate_rate_score(self, context: SlowmodeContext) -> float:
        """Calculate score based on current rate vs threshold"""
        if context.threshold == 0:
            return 0.0

        ratio = context.current_rate / context.threshold
        return self._normalise(ratio)

    async def _calculate_historical_score(self, context: SlowmodeContext) -> float:
        """Calculate score based on deviation from historical norm"""
        if context.historical_rates is None or context.historical_rates == 0:
            return 0.0

        deviation = context.current_rate - context.historical_rates
        return self._normalise(max(0, deviation - 1.0), max_value=3.0)

    async def _calculate_velocity_score(self, channel_id: int) -> float:
        """Calculate score based on rate of change (acceleration)"""
        rate_1m = await self.repo.get_message_rate(channel_id, 60)
        rate_5m = await self.repo.get_message_rate(channel_id, 300) / 5

        if rate_5m == 0:
            return 0.0

        velocity = (rate_1m - rate_5m) / 5
        return self._normalise(max(0, velocity), max_value=2.0)

    async def _calculate_effectiveness_score(self, channel_id: int) -> float:
        """Calculate score based on past effectiveness"""
        score = await self.repo.get_effectiveness_score(channel_id)

        return 1.0 - score if score > 0 else 0.5

    def _map_to_slowmode(self, urgency_score: float, threshold: int) -> int:
        """Map urgency score to slowmode duration"""
        if urgency_score <= 0.2:
            return 0

        base_slowmode = math.exp(urgency_score * 4) - 1

        scale_factor = threshold / 10.0
        slowmode = int(base_slowmode * scale_factor * 3)

        return max(self.config.MIN_SLOWMODE, min(self.config.MAX_SLOWMODE, slowmode))

    def _apply_hysteresis(self, target: int, current: int) -> int:
        """Apply hysteresis to prevent rapid fluctuations."""
        diff = abs(target - current)

        if diff < self.config.HYSTERESIS_THRESHOLD:
            return current

        if target > current:
            return min(target, current + self.config.MAX_CHANGE_PER_UPDATE)
        else:
            return max(target, current - self.config.MAX_CHANGE_PER_UPDATE)

    def _calculate_confidence(self, urgency_score: float, rate_score: float) -> float:
        """Calculate confidence in the slowmode decision."""
        urgency_clarity = abs(urgency_score - 0.5) * 2
        rate_clarity = min(rate_score, 1.0)

        confidence = (urgency_clarity * 0.6) + (rate_clarity * 0.4)
        return min(max(confidence, 0.1), 1.0)

    def _build_reasoning(
        self, context: SlowmodeContext, urgency_score: float, final_slowmode: int
    ) -> str:
        """Build human-readable reasoning for the decision."""
        if final_slowmode == 0:
            return f"Activity ({context.current_rate:.1f} msg/min) is below threshold ({context.threshold})"

        reasoning_parts = [
            f"Current Rate: {context.current_rate:.1f} msg/min (Threshold: {context.threshold})",
        ]

        if context.historical_rates:
            deviation = (context.current_rate / context.historical_rates - 1) * 100
            if abs(deviation) >= 20:
                reasoning_parts.append(
                    f"{'Above' if deviation > 0 else 'Below'} normal by {abs(deviation):.0f}"
                )

        reasoning_parts.append(f"Urgency Score: {urgency_score:.2f}")

        return " | ".join(reasoning_parts)

    async def calculate(self, channel_id: int, guild_id: int) -> SlowmodeDecision:
        """Calculate optimal slowmode for a channel."""
        context = await self._build_context(channel_id, guild_id)

        rate_score = self._calculate_rate_score(context)
        historical_score = await self._calculate_historical_score(context)
        velocity_score = await self._calculate_velocity_score(channel_id)
        effectiveness_score = await self._calculate_effectiveness_score(channel_id)

        urgency_score = (
            self.config.CURRENT_RATE_WEIGHT * rate_score
            + self.config.HISTORICAL_WEIGHT * historical_score
            + self.config.VELOCITY_WEIGHT * velocity_score
            + self.config.EFFECTIVENESS_WEIGHT * effectiveness_score
        )

        target_slowmode = self._map_to_slowmode(urgency_score, context.threshold)

        final_slowmode = self._apply_hysteresis(target_slowmode, context.current_slowmode)

        return SlowmodeDecision(
            slowmode_seconds=final_slowmode,
            confidence=self._calculate_confidence(urgency_score, rate_score),
            reasoning=self._build_reasoning(context, urgency_score, final_slowmode),
            factors={
                "rate_score": rate_score,
                "historical_score": historical_score,
                "velocity_score": velocity_score,
                "effectiveness_score": effectiveness_score,
                "urgency_score": urgency_score,
            },
            should_notify=abs(final_slowmode - context.current_slowmode) >= 15,
        )

    async def calculate_with_current(
        self, channel_id: int, guild_id: int, current_slowmode: int
    ) -> SlowmodeDecision:
        """Calculate slowmode with known current slowmode."""
        decision = await self.calculate(channel_id, guild_id)

        context = await self._build_context(channel_id, guild_id)
        context.current_slowmode = current_slowmode

        final_slowmode = self._apply_hysteresis(decision.slowmode_seconds, current_slowmode)

        return SlowmodeDecision(
            slowmode_seconds=final_slowmode,
            confidence=decision.confidence,
            reasoning=self._build_reasoning(
                context, decision.factors["urgency_score"], final_slowmode
            ),
            factors=decision.factors,
            should_notify=abs(final_slowmode - current_slowmode) >= 15,
        )
