"""Unit tests for the TypeSafe-backed automod.

Nothing here touches the network: the routing thresholds, the input guards, the
per-guild budget, and the response mapping are all exercised directly.
"""

import pytest
from typesafe_sdk import NoulAnswer, ScoreAnswer, SystemOneResponse, Usage

from serenity.core.constants import CATEGORY_POLICIES, AutomodConfig, CategoryPolicy
from serenity.core.types import (
    AutomodAssessment,
    AutomodCategory,
    AutomodVerdict,
    ContextMessage,
)
from serenity.extensions.automod import build_embed
from serenity.services.automod_questions import (
    CATEGORY_BLURBS,
    CATEGORY_LABELS,
    MESSAGE_BATTERY,
    SEVERITY_KEY,
)
from serenity.services.automod_service import (
    AutomodService,
    _RequestBudget,
    build_state,
    evaluate,
    route,
)

CONFIG = AutomodConfig()


def _scores(**overrides: float) -> dict[AutomodCategory, float]:
    """A clean scoreboard with the named categories raised."""
    scores = {category: 0.01 for category in AutomodCategory}
    for name, value in overrides.items():
        scores[AutomodCategory[name.upper()]] = value
    return scores


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_clean_message_is_allowed():
    assert route(_scores(), severity=0.0) is AutomodVerdict.ALLOW


def test_empty_scores_are_allowed():
    assert route({}, severity=0.0) is AutomodVerdict.ALLOW


def test_confident_but_harmless_profanity_is_allowed():
    """ "life is a bitch" — 96% profanity, severity 0.86. Swearing at nobody."""
    verdict, reason = evaluate(_scores(profanity=0.96), severity=0.86)
    assert verdict is AutomodVerdict.ALLOW
    assert "severity 0.86" in reason and "floor" in reason


def test_profanity_never_blocks_on_its_own():
    """However certain, swearing alone is a moderator's call, not a deletion."""
    assert route(_scores(profanity=1.0), severity=3.0) is not AutomodVerdict.BLOCK


def test_harmful_profanity_still_reaches_review():
    assert route(_scores(profanity=0.96), severity=1.5) is AutomodVerdict.REVIEW


def test_hate_acts_well_below_the_profanity_bar():
    """The same probability blocks for hate and does nothing for swearing."""
    assert route(_scores(hate=0.86), severity=2.5) is AutomodVerdict.BLOCK
    assert route(_scores(profanity=0.86), severity=2.5) is AutomodVerdict.ALLOW


def test_severity_floor_holds_back_every_gated_category():
    for category, policy in CATEGORY_POLICIES.items():
        if policy.severity_floor <= 0.0:
            continue
        scores = {category: 1.0}
        below = policy.severity_floor - 0.01
        assert route(scores, severity=below) is AutomodVerdict.ALLOW, category


def test_self_harm_alerts_but_never_blocks():
    """Someone in trouble needs a moderator, not a deleted message."""
    assert route(_scores(self_harm=1.0), severity=3.0) is AutomodVerdict.REVIEW


def test_spam_blocks_without_needing_severity():
    """A scam link scores low on harm but still deserves removing."""
    assert route(_scores(spam=0.96), severity=0.1) is AutomodVerdict.BLOCK


def test_the_most_serious_category_wins():
    scores = _scores(profanity=0.96, hate=0.90)
    assert route(scores, severity=2.5) is AutomodVerdict.BLOCK


def test_reason_names_the_category_that_fired():
    verdict, reason = evaluate(_scores(hate=0.90), severity=2.5)
    assert verdict is AutomodVerdict.BLOCK
    assert "Hate" in reason and "0.90" in reason


def test_reason_when_nothing_fires():
    _, reason = evaluate(_scores(), severity=0.0)
    assert reason == "nothing crossed its threshold"


def test_policies_can_be_overridden():
    strict = {AutomodCategory.PROFANITY: CategoryPolicy(0.5, 0.8, 0.0)}
    assert route(_scores(profanity=0.96), severity=0.0, policies=strict) is AutomodVerdict.BLOCK


def test_every_category_has_a_policy():
    for category in AutomodCategory:
        assert category in CATEGORY_POLICIES


# ---------------------------------------------------------------------------
# Input guards
# ---------------------------------------------------------------------------


@pytest.fixture
def service() -> AutomodService:
    """A service with no API key, so it can never make a request."""
    return AutomodService(api_key=None, config=CONFIG)


def test_service_without_a_key_is_unavailable(service: AutomodService):
    assert service.is_available is False


@pytest.mark.asyncio
async def test_assess_without_a_key_returns_none(service: AutomodService):
    assert await service.assess("something awful", guild_id=1) is None


def test_prepare_rejects_empty_and_whitespace(service: AutomodService):
    assert service.prepare("") is None
    assert service.prepare("   \n  ") is None


def test_prepare_rejects_content_below_the_minimum(service: AutomodService):
    assert service.prepare("a") is None


def test_prepare_truncates_long_content(service: AutomodService):
    prepared = service.prepare("x" * (CONFIG.MAX_CONTENT_LENGTH + 500))
    assert prepared is not None
    assert len(prepared) == CONFIG.MAX_CONTENT_LENGTH


def test_prepare_strips_surrounding_whitespace(service: AutomodService):
    assert service.prepare("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# Per-guild budget
# ---------------------------------------------------------------------------


def _budget(**overrides: int) -> _RequestBudget:
    settings = {
        "global_per_minute": 1000,
        "min_guild_per_minute": 5,
        "max_guild_per_minute": 30,
    }
    settings.update(overrides)
    return _RequestBudget(**settings)  # type: ignore[arg-type]


def test_a_guild_may_spend_up_to_its_allowance():
    budget = _budget(max_guild_per_minute=3)
    assert [budget.try_consume(1) for _ in range(3)] == [None, None, None]
    assert budget.try_consume(1) == "rate_limited"


def test_each_guild_has_its_own_allowance():
    budget = _budget(max_guild_per_minute=1, min_guild_per_minute=1)
    assert budget.try_consume(1) is None
    assert budget.try_consume(1) == "rate_limited"
    assert budget.try_consume(2) is None


def test_the_global_ceiling_stops_everyone():
    """A hundred guilds each under their own cap must still not exceed the account limit."""
    budget = _budget(global_per_minute=10, max_guild_per_minute=5, min_guild_per_minute=5)
    spent = sum(budget.try_consume(guild) is None for guild in range(100))
    assert spent == 10
    assert budget.try_consume(999) == "global_rate_limited"


def test_a_blocked_request_does_not_burn_the_guild_allowance():
    """Refused by the global ceiling, the guild's own budget must be untouched."""
    budget = _budget(global_per_minute=1, max_guild_per_minute=5, min_guild_per_minute=5)
    assert budget.try_consume(1) is None
    assert budget.try_consume(2) == "global_rate_limited"
    assert len(budget._windows[2]) == 0


def test_the_allowance_shrinks_as_more_guilds_get_busy():
    """The global budget is divided between whoever is actually talking."""
    budget = _budget(global_per_minute=100, min_guild_per_minute=1, max_guild_per_minute=50)
    budget.try_consume(1)
    assert budget.guild_allowance() == 50  # one guild, capped by its own maximum
    for guild in range(2, 11):
        budget.try_consume(guild)
    assert budget.guild_allowance() == 10  # ten guilds, 100/10 each


def test_a_quiet_guild_keeps_a_minimum_allowance():
    """However crowded it gets, every server keeps a floor of its own."""
    budget = _budget(global_per_minute=10, min_guild_per_minute=5, max_guild_per_minute=30)
    for guild in range(100):
        budget.try_consume(guild)
    assert budget.guild_allowance() == 5


def test_budget_forgets_quiet_guilds():
    budget = _budget(max_guild_per_minute=1, max_tracked_guilds=2)
    for guild_id in range(10):
        budget.try_consume(guild_id)
    assert len(budget._windows) <= 10  # pruning keeps the map bounded


def test_guild_zero_does_not_collide_with_the_global_window():
    """A sentinel key sharing the guild map would double-count every request."""
    budget = _budget(global_per_minute=10, min_guild_per_minute=5, max_guild_per_minute=5)
    assert budget.try_consume(0) is None
    assert len(budget._global_window) == 1


# ---------------------------------------------------------------------------
# Response mapping
# ---------------------------------------------------------------------------


def _response(**noul_scores: float) -> SystemOneResponse:
    answers = {
        category.value: NoulAnswer(noul=noul_scores.get(category.value, 0.01))
        for category in AutomodCategory
    }
    answers[SEVERITY_KEY] = ScoreAnswer(
        score=noul_scores.get("severity", 0.0),
        confidence=0.9,
        legend={0: "none"},
        probabilities={0: 1.0},
    )
    return SystemOneResponse(
        model="jev-1.13.0",
        usage=Usage(input_tokens=866, output_tokens=134),
        answers=answers,  # type: ignore[arg-type]
    )


def test_response_maps_onto_an_assessment(service: AutomodService):
    assessment = service._to_assessment(_response(hate=0.99, severity=2.1), latency_ms=179)

    assert assessment.scores[AutomodCategory.HATE] == pytest.approx(0.99)
    assert assessment.severity == pytest.approx(2.1)
    assert assessment.verdict is AutomodVerdict.BLOCK
    assert assessment.top_category is AutomodCategory.HATE
    assert assessment.model == "jev-1.13.0"
    assert assessment.latency_ms == 179
    assert assessment.input_tokens == 866
    assert assessment.output_tokens == 134


def test_a_missing_request_id_does_not_raise(service: AutomodService):
    """The SDK raises when a response carries no request id; we must not."""
    assert service._to_assessment(_response(), latency_ms=10).request_id is None


def test_ranked_orders_categories_by_probability(service: AutomodService):
    assessment = service._to_assessment(
        _response(profanity=0.40, hate=0.99, violence=0.10), latency_ms=10
    )
    ranked = [category for category, _ in assessment.ranked]
    assert ranked[:3] == [AutomodCategory.HATE, AutomodCategory.PROFANITY, AutomodCategory.VIOLENCE]


# ---------------------------------------------------------------------------
# Battery and rendering
# ---------------------------------------------------------------------------


def test_every_category_has_a_question_and_labels():
    for category in AutomodCategory:
        assert category.value in MESSAGE_BATTERY
        assert category in CATEGORY_LABELS
        assert category in CATEGORY_BLURBS
    assert SEVERITY_KEY in MESSAGE_BATTERY


@pytest.mark.parametrize("verdict", list(AutomodVerdict))
def test_embed_renders_for_every_verdict(verdict: AutomodVerdict):
    assessment = AutomodAssessment(
        scores=_scores(hate=0.99),
        severity=2.1,
        severity_confidence=0.9,
        verdict=verdict,
        model="jev-1.13.0",
        latency_ms=179,
        input_tokens=866,
        output_tokens=134,
        request_id="req_123",
    )
    embed = build_embed(assessment)

    assert embed.description is not None
    assert "Hate" in embed.description
    assert "99.0%" in embed.description
    assert embed.footer is not None and embed.footer.text is not None
    assert "179ms" in embed.footer.text


# ---------------------------------------------------------------------------
# State and context
# ---------------------------------------------------------------------------


def test_state_names_the_message_under_review():
    state = build_state("the n word", author_id=1, config=CONFIG)
    assert state["message_under_review"]["text"] == "the n word"
    assert state["recent_messages"] == []


def test_state_carries_preceding_messages_in_order():
    context = [
        ContextMessage(author_id=1, content="dont say"),
        ContextMessage(author_id=2, content="say what"),
    ]
    state = build_state("the n word", author_id=1, context=context, config=CONFIG)
    assert [m["text"] for m in state["recent_messages"]] == ["dont say", "say what"]


def test_same_author_keeps_the_same_speaker_label():
    """A split-up sentence is only recognisable if both halves share a speaker."""
    context = [ContextMessage(author_id=1, content="dont say")]
    state = build_state("the n word", author_id=1, context=context, config=CONFIG)
    assert state["recent_messages"][0]["speaker"] == state["message_under_review"]["speaker"]


def test_different_authors_get_different_speaker_labels():
    context = [ContextMessage(author_id=99, content="dont say")]
    state = build_state("the n word", author_id=1, context=context, config=CONFIG)
    assert state["recent_messages"][0]["speaker"] != state["message_under_review"]["speaker"]


def test_state_sends_no_user_ids():
    """Speaker labels are per-request; real ids must never leave the process."""
    context = [ContextMessage(author_id=123456789, content="hello")]
    state = build_state("world", author_id=987654321, context=context, config=CONFIG)
    assert "123456789" not in str(state)
    assert "987654321" not in str(state)
    assert state["message_under_review"]["speaker"].startswith("user_")


def test_state_keeps_only_the_most_recent_context():
    context = [ContextMessage(author_id=1, content=str(i)) for i in range(20)]
    state = build_state("now", author_id=1, context=context, config=CONFIG)
    assert len(state["recent_messages"]) == CONFIG.CONTEXT_WINDOW
    assert state["recent_messages"][-1]["text"] == "19"


def test_context_messages_are_truncated():
    context = [ContextMessage(author_id=1, content="x" * 5000)]
    state = build_state("now", author_id=1, context=context, config=CONFIG)
    assert len(state["recent_messages"][0]["text"]) == CONFIG.CONTEXT_MAX_LENGTH
