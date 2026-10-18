"""Screens Discord messages with TypeSafe's Jev model.

This layer is deliberately defensive. It is on the hot path of every message in
a watched channel, it talks to a paid third-party API, and it handles user text
that is actively trying to be nasty:

* message content is only ever sent as `state`, never spliced into instructions,
  so a message cannot rewrite the questions being asked about it;
* content is truncated before it leaves the process, capping tokens per call;
* concurrency is bounded globally and request volume is bounded per guild;
* every failure path returns ``None`` rather than raising into the gateway's
  event loop — a dead API means no assessment, never a crash or a stuck bot;
* nothing logs message content, only ids, timings, and verdicts.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Sequence

from typesafe_sdk import (
    AsyncTypeSafeClient,
    RetryPolicy,
    SystemOneResponse,
    TypeSafeAPIError,
    TypeSafeError,
)

from serenity.core.constants import AUTOMOD_CONFIG, CATEGORY_POLICIES, AutomodConfig, CategoryPolicy
from serenity.core.types import (
    AutomodAssessment,
    AutomodCategory,
    AutomodVerdict,
    ContextMessage,
)
from serenity.services.automod_questions import (
    CATEGORY_LABELS,
    MESSAGE_BATTERY,
    SEVERITY_KEY,
)
from serenity.services.metrics import (
    AUTOMOD_ASSESSMENTS,
    AUTOMOD_INPUT_TOKENS,
    AUTOMOD_LATENCY,
    AUTOMOD_SKIPPED,
)
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

API_KEY_ENV = "TYPESAFE_API_KEY"


# Highest precedence wins when several categories fire at once.
_PRECEDENCE = [AutomodVerdict.BLOCK, AutomodVerdict.REVIEW, AutomodVerdict.ALLOW]


def evaluate(
    scores: Dict[AutomodCategory, float],
    severity: float,
    policies: Dict[AutomodCategory, CategoryPolicy] = CATEGORY_POLICIES,
) -> tuple[AutomodVerdict, str]:
    """Turn one assessment into a verdict and a line explaining it.

    TypeSafe supplies the probabilities; the policy that decides what to do with
    them lives here, where it can be read and tuned. Each category is judged
    against its own thresholds and its own severity floor, then the most serious
    outcome wins. The explanation names the category and the number it crossed,
    which is the only way to tell a threshold that needs moving from a question
    that needs rewording.
    """
    triggered: list[tuple[AutomodVerdict, AutomodCategory, float, float]] = []
    held_back: list[tuple[AutomodCategory, float, CategoryPolicy]] = []

    for category, probability in scores.items():
        policy = policies.get(category)
        if policy is None or probability < policy.review_at:
            continue
        # However sure the model is, a message it judges harmless stays put.
        if severity < policy.severity_floor:
            held_back.append((category, probability, policy))
        elif probability >= policy.action_at:
            triggered.append((AutomodVerdict.BLOCK, category, probability, policy.action_at))
        else:
            triggered.append((AutomodVerdict.REVIEW, category, probability, policy.review_at))

    verdict = next(
        (v for v in _PRECEDENCE if any(t[0] is v for t in triggered)), AutomodVerdict.ALLOW
    )

    if triggered:
        _, category, probability, threshold = max(
            (t for t in triggered if t[0] is verdict), key=lambda t: t[2]
        )
        bar = "block" if verdict is AutomodVerdict.BLOCK else "review"
        reason = f"{CATEGORY_LABELS[category]} {probability:.2f} ≥ {bar} {threshold:.2f}"
    elif held_back:
        category, probability, policy = max(held_back, key=lambda h: h[1])
        reason = (
            f"{CATEGORY_LABELS[category]} {probability:.2f} ≥ review {policy.review_at:.2f}, "
            f"but severity {severity:.2f} < floor {policy.severity_floor:.2f}"
        )
    else:
        reason = "nothing crossed its threshold"

    return verdict, reason


def route(
    scores: Dict[AutomodCategory, float],
    severity: float,
    policies: Dict[AutomodCategory, CategoryPolicy] = CATEGORY_POLICIES,
) -> AutomodVerdict:
    """The verdict alone, for callers that do not need the explanation."""
    return evaluate(scores, severity, policies)[0]


def build_state(
    content: str,
    *,
    author_id: int,
    context: Sequence[ContextMessage] = (),
    config: AutomodConfig = AUTOMOD_CONFIG,
) -> Dict[str, Any]:
    """Assemble the state for one message.

    The message under review is named separately from the chat around it, so
    every question can point at it directly instead of reasoning about which
    part of the state it is meant to judge.

    Speakers are labelled `user_1`, `user_2`, ... per request. That keeps what
    matters — whether two messages came from the same person, which is how a
    split-up sentence is recognised as one thought — while sending no user ids
    or names to the API. The labels mean nothing outside this one request.
    """
    labels: Dict[int, str] = {}

    def speaker(user_id: int) -> str:
        if user_id not in labels:
            labels[user_id] = f"user_{len(labels) + 1}"
        return labels[user_id]

    recent = [
        {"speaker": speaker(message.author_id), "text": message.content[: config.CONTEXT_MAX_LENGTH]}
        for message in context[-config.CONTEXT_WINDOW :]
    ]

    return {
        "recent_messages": recent,
        "message_under_review": {"speaker": speaker(author_id), "text": content},
    }


class _RequestBudget:
    """Sliding-window request budgets: one per guild, plus one account-wide.

    Jev's rate limit is account-wide, so a per-guild cap alone cannot keep us
    under it — a hundred servers each politely under their own limit still add
    up to a 429 storm. The global window is the real ceiling; the per-guild
    allowance is how the remaining budget is divided fairly between whoever is
    currently talking, rather than first-come-first-served.
    """

    def __init__(
        self,
        *,
        global_per_minute: int,
        min_guild_per_minute: int,
        max_guild_per_minute: int,
        max_tracked_guilds: int = 500,
    ) -> None:
        self._global_per_minute = global_per_minute
        self._min_guild = min_guild_per_minute
        self._max_guild = max_guild_per_minute
        self._max_tracked = max_tracked_guilds
        # Kept apart from the per-guild map: a sentinel key inside it could
        # collide with a real guild id and double-count the global window.
        self._global_window: Deque[float] = deque()
        self._windows: Dict[int, Deque[float]] = {}

    @staticmethod
    def _expire(window: Deque[float], now: float) -> Deque[float]:
        """Drop entries that have aged out of the one-minute window."""
        while window and now - window[0] >= 60.0:
            window.popleft()
        return window

    def active_guilds(self, now: Optional[float] = None) -> int:
        """How many guilds have sent traffic in the last minute."""
        now = time.monotonic() if now is None else now
        return sum(
            1 for window in self._windows.values() if window and now - window[-1] < 60.0
        )

    def guild_allowance(self, now: Optional[float] = None) -> int:
        """Each active guild's share of the global budget, within its bounds."""
        share = self._global_per_minute // max(1, self.active_guilds(now))
        return min(self._max_guild, max(self._min_guild, share))

    def try_consume(self, guild_id: int) -> Optional[str]:
        """Take one request from both budgets, or name the one that refused.

        Both are checked before either is spent, so a request blocked by the
        global ceiling does not also burn the guild's allowance.
        """
        now = time.monotonic()
        global_window = self._expire(self._global_window, now)
        guild_window = self._expire(self._windows.setdefault(guild_id, deque()), now)

        if len(global_window) >= self._global_per_minute:
            return "global_rate_limited"
        if len(guild_window) >= self.guild_allowance(now):
            return "rate_limited"

        global_window.append(now)
        guild_window.append(now)
        self._prune(now)
        return None

    def _prune(self, now: float) -> None:
        """Drop guilds that have gone quiet, so the map cannot grow forever."""
        if len(self._windows) <= self._max_tracked:
            return
        stale = [
            key
            for key, window in self._windows.items()
            if not window or now - window[-1] >= 60.0
        ]
        for key in stale:
            del self._windows[key]


class AutomodService:
    """Scores message content against the automod question battery."""

    def __init__(
        self,
        *,
        api_key: Optional[str],
        config: AutomodConfig = AUTOMOD_CONFIG,
    ) -> None:
        self._config = config
        self._api_key = api_key or None
        self._client: Optional[AsyncTypeSafeClient] = None
        self._client_lock = asyncio.Lock()
        self._budget = _RequestBudget(
            global_per_minute=config.GLOBAL_REQUESTS_PER_MINUTE,
            min_guild_per_minute=config.MIN_GUILD_REQUESTS_PER_MINUTE,
            max_guild_per_minute=config.MAX_GUILD_REQUESTS_PER_MINUTE,
        )
        self._semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)

        if not self._api_key:
            logger.warning(
                f"{API_KEY_ENV} is not set; automod will accept commands but return "
                "no assessments."
            )

    async def _get_client(self) -> Optional[AsyncTypeSafeClient]:
        """The HTTP client, built on first use inside the running event loop."""
        if self._api_key is None:
            return None
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = AsyncTypeSafeClient(
                        api_key=self._api_key,
                        model=self._config.MODEL,
                        timeout=self._config.REQUEST_TIMEOUT,
                        retry=RetryPolicy(
                            max_retries=self._config.MAX_RETRIES,
                            timeout=self._config.REQUEST_TIMEOUT,
                        ),
                    )
        return self._client

    @classmethod
    def from_env(cls, config: AutomodConfig = AUTOMOD_CONFIG) -> AutomodService:
        """Build a service from `TYPESAFE_API_KEY`, disabled if it is absent."""
        return cls(api_key=os.getenv(API_KEY_ENV), config=config)

    @property
    def is_available(self) -> bool:
        """Whether this service has the credentials to reach the API."""
        return self._api_key is not None

    @property
    def config(self) -> AutomodConfig:
        return self._config

    @property
    def budget(self) -> _RequestBudget:
        """The live request budget, for reporting in `/automod status`."""
        return self._budget

    def prepare(self, content: str) -> Optional[str]:
        """Normalise message content, or reject it as not worth a request."""
        text = content.strip()
        if len(text) < self._config.MIN_CONTENT_LENGTH:
            return None
        return text[: self._config.MAX_CONTENT_LENGTH]

    async def assess(
        self,
        content: str,
        *,
        guild_id: int,
        author_id: int = 0,
        context: Sequence[ContextMessage] = (),
    ) -> Optional[AutomodAssessment]:
        """Score one message in the context of the ones before it.

        Never raises: a missing key, an over-budget guild, an API outage, and a
        malformed response all come back as None.
        """
        client = await self._get_client()
        if client is None:
            AUTOMOD_SKIPPED.labels(reason="unavailable").inc()
            return None

        text = self.prepare(content)
        if text is None:
            AUTOMOD_SKIPPED.labels(reason="too_short").inc()
            return None

        refused = self._budget.try_consume(guild_id)
        if refused is not None:
            AUTOMOD_SKIPPED.labels(reason=refused).inc()
            logger.warning(
                f"Automod over budget for guild {guild_id} ({refused}); skipping "
                f"assessment. Guild allowance is {self._budget.guild_allowance()}/min "
                f"across {self._budget.active_guilds()} active guilds."
            )
            return None

        started = time.perf_counter()
        try:
            async with self._semaphore:
                state = build_state(
                    text, author_id=author_id, context=context, config=self._config
                )
                response = await client.system_one(state, MESSAGE_BATTERY)
        except TypeSafeAPIError as exc:
            AUTOMOD_SKIPPED.labels(reason="api_error").inc()
            logger.error(
                f"TypeSafe API error for guild {guild_id}: status={exc.status} "
                f"request_id={exc.request_id}"
            )
            return None
        except TypeSafeError as exc:
            AUTOMOD_SKIPPED.labels(reason="api_error").inc()
            logger.error(f"TypeSafe client error for guild {guild_id}: {exc!r}")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the gateway must not die for this
            AUTOMOD_SKIPPED.labels(reason="api_error").inc()
            logger.exception(f"Unexpected automod failure for guild {guild_id}: {exc!r}")
            return None

        elapsed = time.perf_counter() - started
        AUTOMOD_LATENCY.observe(elapsed)

        try:
            assessment = self._to_assessment(
                response, int(elapsed * 1000), context_size=len(context)
            )
        except (KeyError, ValueError) as exc:
            AUTOMOD_SKIPPED.labels(reason="api_error").inc()
            logger.error(f"Malformed TypeSafe response for guild {guild_id}: {exc!r}")
            return None

        AUTOMOD_ASSESSMENTS.labels(verdict=assessment.verdict.value).inc()
        AUTOMOD_INPUT_TOKENS.inc(assessment.input_tokens)
        logger.info(
            f"Automod assessed a message in guild {guild_id}: "
            f"verdict={assessment.verdict.value} "
            f"top={assessment.top_category.value if assessment.top_category else 'none'} "
            f"({assessment.top_score:.2f}) severity={assessment.severity:.2f} "
            f"reason={assessment.reason!r} context={assessment.context_size} "
            f"latency={assessment.latency_ms}ms request_id={assessment.request_id}"
        )
        return assessment

    @staticmethod
    def _request_id(response: SystemOneResponse) -> Optional[str]:
        """The request id, which the SDK raises for rather than omitting."""
        try:
            return response.request_id
        except TypeSafeError:
            return None

    def _to_assessment(
        self, response: SystemOneResponse, latency_ms: int, *, context_size: int = 0
    ) -> AutomodAssessment:
        """Map a raw System One response onto our own type."""
        nouls = response.nouls
        scores = {
            category: nouls[category.value].noul
            for category in AutomodCategory
            if category.value in nouls
        }
        severity = response.scores[SEVERITY_KEY]
        verdict, reason = evaluate(scores, severity.score)

        return AutomodAssessment(
            scores=scores,
            severity=severity.score,
            severity_confidence=severity.confidence,
            verdict=verdict,
            reason=reason,
            model=response.model,
            latency_ms=latency_ms,
            # The API omits usage counts on some responses; report 0 rather than None.
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            request_id=self._request_id(response),
            context_size=context_size,
        )

    async def close(self) -> None:
        """Release the HTTP client, if one was ever built."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
