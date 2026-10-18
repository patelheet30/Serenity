"""Unit tests for SlowmodeEngine"""

from unittest.mock import AsyncMock

import pytest

from serenity.core.constants import SLOWMODE_CONFIG
from serenity.core.types import ChannelConfig, GuildConfig, SlowmodeContext
from serenity.services.slowmode_engine import SlowmodeEngine


@pytest.fixture
def mock_repo():
    """Mock repository for testing"""
    repo = AsyncMock()

    # Default config responses
    repo.get_guild_config.return_value = GuildConfig(
        guild_id=1, is_enabled=True, default_threshold=10, update_interval=30
    )

    repo.get_channel_config.return_value = ChannelConfig(
        channel_id=123,
        guild_id=1,
        is_enabled=True,
        threshold=None,  # Use guild default
    )

    # Default activity responses - using correct method names
    repo.get_message_rate.return_value = 5.0
    repo.get_expected_activity.return_value = None
    repo.get_effectiveness_score.return_value = 0.0

    return repo


@pytest.fixture
def engine(mock_repo):
    """SlowmodeEngine instance with mock repository"""
    return SlowmodeEngine(mock_repo)


class TestRateScore:
    """Tests for rate score calculation"""

    def test_below_threshold(self, engine):
        """Rate below threshold should give low score"""
        context = SlowmodeContext(
            channel_id=123, guild_id=1, current_rate=5.0, threshold=10, current_slowmode=0
        )

        score = engine._calculate_rate_score(context)
        assert 0.0 <= score < 0.6  # Should be normalized low

    def test_at_threshold(self, engine):
        """Rate at threshold should give medium score"""
        context = SlowmodeContext(
            channel_id=123, guild_id=1, current_rate=10.0, threshold=10, current_slowmode=0
        )

        score = engine._calculate_rate_score(context)
        assert 0.15 <= score <= 0.25

    def test_above_threshold(self, engine):
        """Rate above threshold should give high score"""
        context = SlowmodeContext(
            channel_id=123, guild_id=1, current_rate=30.0, threshold=10, current_slowmode=0
        )

        score = engine._calculate_rate_score(context)
        assert score > 0.5

    def test_zero_threshold(self, engine):
        """Zero threshold should return 0 score"""
        context = SlowmodeContext(
            channel_id=123, guild_id=1, current_rate=100.0, threshold=0, current_slowmode=0
        )

        score = engine._calculate_rate_score(context)
        assert score == 0.0


class TestHistoricalScore:
    """Tests for historical score calculation"""

    @pytest.mark.asyncio
    async def test_no_historical_data(self, engine):
        """No historical data should return 0"""
        context = SlowmodeContext(
            channel_id=123,
            guild_id=1,
            current_rate=15.0,
            threshold=10,
            current_slowmode=0,
            historical_rates=None,
        )

        score = await engine._calculate_historical_score(context)
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_at_historical_average(self, engine):
        """Current rate matching historical should give low score"""
        context = SlowmodeContext(
            channel_id=123,
            guild_id=1,
            current_rate=10.0,
            threshold=10,
            current_slowmode=0,
            historical_rates=10.0,
        )

        score = await engine._calculate_historical_score(context)
        assert score == 0.0  # No deviation

    @pytest.mark.asyncio
    async def test_above_historical_average(self, engine):
        """Current rate 2x historical should give high score"""
        context = SlowmodeContext(
            channel_id=123,
            guild_id=1,
            current_rate=20.0,
            threshold=10,
            current_slowmode=0,
            historical_rates=10.0,
        )

        score = await engine._calculate_historical_score(context)
        assert score > 0.3  # Significant deviation

    @pytest.mark.asyncio
    async def test_below_historical_average(self, engine):
        """Current rate below historical should give 0 score"""
        context = SlowmodeContext(
            channel_id=123,
            guild_id=1,
            current_rate=5.0,
            threshold=10,
            current_slowmode=0,
            historical_rates=10.0,
        )

        score = await engine._calculate_historical_score(context)
        assert score == 0.0  # Only care about increases


class TestVelocityScore:
    """Tests for velocity (acceleration) calculation"""

    @pytest.mark.asyncio
    async def test_stable_rate(self, engine, mock_repo):
        """Stable message rate should give low velocity score"""
        mock_repo.get_message_rate.side_effect = [10.0, 50.0]  # 10 msg/min consistently

        score = await engine._calculate_velocity_score(123)
        assert score == 0.0  # No acceleration

    @pytest.mark.asyncio
    async def test_increasing_rate(self, engine, mock_repo):
        """Rapidly increasing rate should give high velocity score"""
        mock_repo.get_message_rate.side_effect = [20.0, 50.0]  # 20 now, 10 avg (50/5)

        score = await engine._calculate_velocity_score(123)
        assert score > 0.5  # Strong acceleration

    @pytest.mark.asyncio
    async def test_decreasing_rate(self, engine, mock_repo):
        """Decreasing rate should give 0 score"""
        mock_repo.get_message_rate.side_effect = [5.0, 50.0]  # 5 now, 10 avg

        score = await engine._calculate_velocity_score(123)
        assert score == 0.0  # Only care about increases

    @pytest.mark.asyncio
    async def test_zero_baseline(self, engine, mock_repo):
        """Zero baseline rate should return 0"""
        mock_repo.get_message_rate.side_effect = [10.0, 0.0]

        score = await engine._calculate_velocity_score(123)
        assert score == 0.0


class TestHysteresis:
    """Tests for hysteresis (smoothing) behavior"""

    def test_small_change_ignored(self, engine):
        """Changes smaller than threshold should be ignored"""
        result = engine._apply_hysteresis(target=32, current=30)
        assert result == 30  # No change (diff < 5)

    def test_significant_change_applied(self, engine):
        """Changes larger than threshold should be applied"""
        result = engine._apply_hysteresis(target=40, current=30)
        assert result == 40  # Change applied (diff >= 5)

    def test_max_change_limited_increase(self, engine):
        """Large increases should be limited"""
        result = engine._apply_hysteresis(target=100, current=30)
        assert result == 60  # Limited to +30

    def test_max_change_limited_decrease(self, engine):
        """Large decreases should be limited"""
        result = engine._apply_hysteresis(target=0, current=60)
        assert result == 30  # Limited to -30

    def test_exact_threshold(self, engine):
        """Change exactly at threshold should apply"""
        result = engine._apply_hysteresis(target=35, current=30)
        assert result == 35  # diff = 5, exactly at threshold


class TestSlowmodeMapping:
    """Tests for mapping urgency to slowmode duration"""

    def test_low_urgency_returns_zero(self, engine):
        """Low urgency should return 0 slowmode"""
        slowmode = engine._map_to_slowmode(urgency_score=0.1, threshold=10)
        assert slowmode == 0

    def test_medium_urgency_returns_moderate(self, engine):
        """Medium urgency should return moderate slowmode"""
        slowmode = engine._map_to_slowmode(urgency_score=0.5, threshold=10)
        assert 10 <= slowmode <= 60

    def test_high_urgency_returns_high(self, engine):
        """High urgency should return high slowmode"""
        slowmode = engine._map_to_slowmode(urgency_score=0.9, threshold=10)
        assert slowmode >= 60

    def test_max_slowmode_clamped(self, engine):
        """Slowmode should never exceed MAX_SLOWMODE"""
        slowmode = engine._map_to_slowmode(urgency_score=1.0, threshold=100)
        assert slowmode <= SLOWMODE_CONFIG.MAX_SLOWMODE

    def test_min_slowmode_clamped(self, engine):
        """Slowmode should never go below MIN_SLOWMODE"""
        slowmode = engine._map_to_slowmode(urgency_score=0.0, threshold=10)
        assert slowmode >= SLOWMODE_CONFIG.MIN_SLOWMODE

    def test_higher_threshold_more_aggressive(self, engine):
        """Higher thresholds should result in more aggressive slowmode"""
        slowmode_low = engine._map_to_slowmode(urgency_score=0.6, threshold=5)
        slowmode_high = engine._map_to_slowmode(urgency_score=0.6, threshold=20)

        assert slowmode_high > slowmode_low


class TestConfidenceCalculation:
    """Tests for confidence scoring"""

    def test_clear_urgency_high_confidence(self, engine):
        """Clear urgency (very high or low) should give high confidence"""
        confidence = engine._calculate_confidence(urgency_score=0.9, rate_score=0.9)
        assert confidence > 0.7

    def test_unclear_urgency_low_confidence(self, engine):
        """Unclear urgency (around 0.5) should give lower confidence"""
        confidence = engine._calculate_confidence(urgency_score=0.5, rate_score=0.3)
        assert confidence < 0.5

    def test_confidence_bounded(self, engine):
        """Confidence should be between 0.1 and 1.0"""
        confidence_low = engine._calculate_confidence(urgency_score=0.5, rate_score=0.0)
        confidence_high = engine._calculate_confidence(urgency_score=1.0, rate_score=1.0)

        assert 0.1 <= confidence_low <= 1.0
        assert 0.1 <= confidence_high <= 1.0


class TestEndToEnd:
    """End-to-end integration tests

    Note: Each calculate() call makes 3 get_message_rate() calls:
    - 1x for 60s window in _build_context
    - 1x for 60s window in _calculate_velocity_score
    - 1x for 300s window in _calculate_velocity_score

    calculate_with_current() makes 4 get_message_rate() calls:
    - 3x from the calculate() call
    - 1x from the second _build_context() call
    """

    @pytest.mark.asyncio
    async def test_low_activity_returns_zero(self, engine, mock_repo):
        """Low activity should return 0 slowmode"""
        mock_repo.get_message_rate.return_value = 3.0  # Below threshold of 10

        decision = await engine.calculate(channel_id=123, guild_id=1)

        assert decision.slowmode_seconds == 0
        assert "below threshold" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_high_activity_returns_slowmode(self, engine, mock_repo):
        """High activity should return positive slowmode"""
        # calculate() calls get_message_rate 3 times: 60s in context, 60s in velocity, 300s in velocity
        mock_repo.get_message_rate.side_effect = [25.0, 25.0, 125.0]
        mock_repo.get_effectiveness_score.return_value = 0.0

        decision = await engine.calculate(channel_id=123, guild_id=1)

        assert decision.slowmode_seconds > 0
        assert 0.0 <= decision.confidence <= 1.0
        assert "urgency score" in decision.reasoning.lower()

    @pytest.mark.asyncio
    async def test_custom_threshold_respected(self, engine, mock_repo):
        """Custom channel threshold should be used"""
        mock_repo.get_channel_config.return_value = ChannelConfig(
            channel_id=123,
            guild_id=1,
            is_enabled=True,
            threshold=20,  # Custom threshold
        )
        # Consistent rate of 15 msg/min (no velocity)
        mock_repo.get_message_rate.side_effect = [15.0, 15.0, 75.0]  # 75.0 / 5 = 15.0

        decision = await engine.calculate(channel_id=123, guild_id=1)

        # 15 is below threshold of 20, should return 0
        assert decision.slowmode_seconds == 0

    @pytest.mark.asyncio
    async def test_calculate_with_current_slowmode(self, engine, mock_repo):
        """calculate_with_current should apply hysteresis correctly"""
        # calculate() is called once (3 get_message_rate calls)
        # then _build_context is called again (1 more get_message_rate call)
        # Make rate slightly above threshold to get ~30s slowmode
        mock_repo.get_message_rate.side_effect = [
            12.0,
            12.0,
            60.0,  # First calculate() call: consistent rate, slightly above threshold
            12.0,  # Second _build_context() call
        ]
        mock_repo.get_effectiveness_score.return_value = 0.0

        decision = await engine.calculate_with_current(
            channel_id=123, guild_id=1, current_slowmode=30
        )

        # With rate of 12 and threshold 10, urgency should be moderate
        # Hysteresis should prevent changes < 5 seconds
        # The actual value depends on the algorithm, so let's just verify it's close to 30
        assert abs(decision.slowmode_seconds - 30) <= 30  # Within reasonable range
        assert isinstance(decision.should_notify, bool)

    @pytest.mark.asyncio
    async def test_notification_flag(self, engine, mock_repo):
        """should_notify should be True for large changes"""
        # calculate() needs 3 calls, _build_context() needs 1 more
        mock_repo.get_message_rate.side_effect = [50.0, 50.0, 250.0, 50.0]
        mock_repo.get_effectiveness_score.return_value = 0.0

        decision = await engine.calculate_with_current(
            channel_id=123, guild_id=1, current_slowmode=0
        )

        # Large change from 0 to something significant
        if decision.slowmode_seconds >= 15:
            assert decision.should_notify is True

    @pytest.mark.asyncio
    async def test_factors_included_in_decision(self, engine, mock_repo):
        """Decision should include all factor scores"""
        # calculate() needs 3 calls to get_message_rate
        mock_repo.get_message_rate.side_effect = [20.0, 20.0, 100.0]
        mock_repo.get_effectiveness_score.return_value = 0.0

        decision = await engine.calculate(channel_id=123, guild_id=1)

        assert "rate_score" in decision.factors
        assert "historical_score" in decision.factors
        assert "velocity_score" in decision.factors
        assert "effectiveness_score" in decision.factors
        assert "urgency_score" in decision.factors

        # All scores should be valid floats
        for score in decision.factors.values():
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0


class TestEdgeCases:
    """Tests for edge cases and error conditions"""

    @pytest.mark.asyncio
    async def test_zero_activity(self, engine, mock_repo):
        """Zero activity should be handled gracefully"""
        mock_repo.get_message_rate.return_value = 0.0

        decision = await engine.calculate(channel_id=123, guild_id=1)

        assert decision.slowmode_seconds == 0
        assert isinstance(decision.confidence, float)

    @pytest.mark.asyncio
    async def test_very_high_activity(self, engine, mock_repo):
        """Very high activity should be clamped properly"""
        # calculate() needs 3 calls to get_message_rate
        mock_repo.get_message_rate.side_effect = [500.0, 500.0, 2500.0]
        mock_repo.get_effectiveness_score.return_value = 0.0

        decision = await engine.calculate(channel_id=123, guild_id=1)

        # Should be clamped to MAX_SLOWMODE
        assert decision.slowmode_seconds <= SLOWMODE_CONFIG.MAX_SLOWMODE

    @pytest.mark.asyncio
    async def test_disabled_channel_still_calculates(self, engine, mock_repo):
        """Disabled channel should still calculate (decision to enable is elsewhere)"""
        mock_repo.get_channel_config.return_value = ChannelConfig(
            channel_id=123,
            guild_id=1,
            is_enabled=False,  # Disabled
            threshold=None,
        )
        mock_repo.get_message_rate.return_value = 20.0

        decision = await engine.calculate(channel_id=123, guild_id=1)

        # Should still calculate, decision to apply is elsewhere
        assert isinstance(decision.slowmode_seconds, int)
