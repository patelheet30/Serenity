"""AI automod — read-only preview.

Every message in a watched channel is screened by TypeSafe's Jev model and the
result is posted back as an embed. Nothing is deleted, nobody is timed out: the
embed shows the verdict the thresholds *would* produce so the questions and the
cut-offs can be tuned against real traffic before any of it bites.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import arc
import hikari

from serenity.core.constants import AUTOMOD_CONFIG, CATEGORY_POLICIES
from serenity.core.hooks import require_module
from serenity.core.modules import ModuleManager, ModuleNotEnabledError, ModuleType
from serenity.core.types import AutomodAssessment, AutomodVerdict, ContextMessage
from serenity.services.automod_questions import CATEGORY_BLURBS, CATEGORY_LABELS
from serenity.services.automod_service import AutomodService
from serenity.utils.logging import channel_id as ctx_channel_id
from serenity.utils.logging import get_logger
from serenity.utils.logging import guild_id as ctx_guild_id

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("automod")

WATCH_CHANNEL_SETTING = "watch_channel_id"

_VERDICT_STYLE: dict[AutomodVerdict, tuple[str, int]] = {
    AutomodVerdict.ALLOW: ("PASS", 0x57F287),
    AutomodVerdict.REVIEW: ("REVIEW", 0xFEE75C),
    AutomodVerdict.BLOCK: ("BLOCK", 0xED4245),
}

_BAR_WIDTH = 22

# guild_id -> (expires_at, watched_channel_id)
_WATCH_CACHE_TTL = 30.0
_watch_cache: Dict[int, Tuple[float, Optional[int]]] = {}

# channel_id -> recent messages, newest last. Only watched channels are
# recorded, and each deque is capped, so this cannot grow without bound.
_history: Dict[int, Deque[Tuple[float, ContextMessage]]] = {}


@plugin.set_error_handler
async def on_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            "❌ The **Automod** module is not enabled. Use `/module enable automod` first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    raise exc


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _bar(probability: float) -> str:
    """A fixed-width meter for one probability."""
    filled = round(max(0.0, min(1.0, probability)) * _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def build_embed(assessment: AutomodAssessment) -> hikari.Embed:
    """Render an assessment as the breakdown embed."""
    label, color = _VERDICT_STYLE[assessment.verdict]

    embed = hikari.Embed(
        title=f"{label} · severity {assessment.severity:.2f}/3",
        color=hikari.Color(color),
    )

    rows = [
        f"{CATEGORY_LABELS[category]:<17}{probability * 100:5.1f}%  {_bar(probability)}"
        for category, probability in assessment.ranked
    ]
    embed.description = "```\n" + "\n".join(rows) + "\n```"

    embed.add_field(name="Why", value=assessment.reason or "—", inline=False)

    top = assessment.top_category
    if top is not None and assessment.verdict is not AutomodVerdict.ALLOW:
        embed.add_field(
            name="Top signal",
            value=f"**{CATEGORY_LABELS[top]}** — {CATEGORY_BLURBS[top]} "
            f"({assessment.top_score * 100:.1f}%)",
            inline=False,
        )

    context_note = (
        f"{assessment.context_size} msg context"
        if assessment.context_size
        else "no context"
    )
    embed.set_footer(
        f"{assessment.model} · {assessment.latency_ms}ms · "
        f"tokens {assessment.input_tokens}/{assessment.output_tokens} · "
        f"{context_note} · preview only, no action taken"
    )
    return embed


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


async def _watched_channel(manager: ModuleManager, guild_id: int) -> Optional[int]:
    """The channel this guild has pointed the automod at, if any.

    Cached briefly: this runs for every message the bot sees, and the answer is
    almost always "nowhere". `/automod watch` and `/automod unwatch` drop their
    own entry, so only `/module disable automod` waits out the TTL.
    """
    now = time.monotonic()
    cached = _watch_cache.get(guild_id)
    if cached is not None and now < cached[0]:
        return cached[1]

    config = await manager.get_module_config(guild_id, ModuleType.AUTOMOD)
    watched: Optional[int] = None
    if config.is_enabled:
        raw = config.settings.get(WATCH_CHANNEL_SETTING)
        watched = int(raw) if raw is not None else None

    _watch_cache[guild_id] = (now + _WATCH_CACHE_TTL, watched)
    return watched


def _forget_watched_channel(guild_id: int) -> None:
    """Drop a guild's cached watch channel after it changes."""
    _watch_cache.pop(guild_id, None)


def _forget_history(channel_id: int) -> None:
    """Drop a channel's recorded messages when it stops being watched."""
    _history.pop(channel_id, None)


def _recent_context(channel_id: int) -> List[ContextMessage]:
    """The messages before this one, freshest last and stale ones dropped."""
    window = _history.get(channel_id)
    if not window:
        return []
    cutoff = time.monotonic() - AUTOMOD_CONFIG.CONTEXT_MAX_AGE
    return [message for seen_at, message in window if seen_at >= cutoff]


def _remember(channel_id: int, message: ContextMessage) -> None:
    """Record a message so it can be context for the next one."""
    window = _history.get(channel_id)
    if window is None:
        window = deque(maxlen=AUTOMOD_CONFIG.CONTEXT_WINDOW)
        _history[channel_id] = window
    window.append((time.monotonic(), message))


@plugin.listen()
async def on_message_create(event: hikari.MessageCreateEvent) -> None:
    """Screen messages in the watched channel and post the breakdown."""
    if event.is_bot or not event.is_human or not event.message.guild_id:
        return

    content = event.message.content
    if not content:
        return

    guild_id = event.message.guild_id
    manager = plugin.client.get_type_dependency(ModuleManager)

    watched = await _watched_channel(manager, guild_id)
    if watched is None or watched != event.channel_id:
        return

    service = plugin.client.get_type_dependency(AutomodService)
    if not service.is_available:
        return

    ctx_guild_id.set(guild_id)
    ctx_channel_id.set(event.channel_id)

    # Snapshot the context and record this message before awaiting, so a burst
    # of messages cannot interleave and land out of order in the history.
    context = _recent_context(event.channel_id)
    _remember(event.channel_id, ContextMessage(author_id=event.author_id, content=content))

    assessment = await service.assess(
        content, guild_id=guild_id, author_id=event.author_id, context=context
    )
    if assessment is None:
        return

    try:
        await event.message.respond(embed=build_embed(assessment), reply=True)
    except hikari.ForbiddenError:
        logger.warning(f"Cannot post automod result in channel {event.channel_id}.")
    except hikari.NotFoundError:
        # The message was deleted while we were waiting on the API.
        pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

automod = plugin.include_slash_group(
    "automod",
    "AI content screening (preview).",
    default_permissions=hikari.Permissions.MANAGE_GUILD,
)


@automod.include
@arc.with_hook(require_module(ModuleType.AUTOMOD))
@arc.slash_subcommand("watch", "Screen every message sent in a channel.")
async def watch(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to screen messages in"),
    ],
    manager: ModuleManager = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    config = await manager.get_module_config(ctx.guild_id, ModuleType.AUTOMOD)
    settings = dict(config.settings)
    settings[WATCH_CHANNEL_SETTING] = channel.id
    await manager.update_module_settings(ctx.guild_id, ModuleType.AUTOMOD, settings)
    _forget_watched_channel(ctx.guild_id)

    embed = hikari.Embed(
        title="✅ Automod Watching",
        description=(
            f"Every message in {channel.mention} will be screened and the result "
            "posted as a reply.\n\n"
            "**No moderation action is taken** — this is a preview of what the model "
            "sees. Message content is sent to TypeSafe for scoring."
        ),
        color=hikari.Color(0x57F287),
    )
    await ctx.respond(embed=embed)
    logger.info(
        f"Automod watch channel set to {channel.id} in guild {ctx.guild_id} "
        f"by user {ctx.user.id}"
    )


@automod.include
@arc.with_hook(require_module(ModuleType.AUTOMOD))
@arc.slash_subcommand("unwatch", "Stop screening messages.")
async def unwatch(
    ctx: arc.GatewayContext,
    manager: ModuleManager = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    config = await manager.get_module_config(ctx.guild_id, ModuleType.AUTOMOD)
    settings = dict(config.settings)
    watched = settings.pop(WATCH_CHANNEL_SETTING, None)
    await manager.update_module_settings(ctx.guild_id, ModuleType.AUTOMOD, settings)
    _forget_watched_channel(ctx.guild_id)
    if watched is not None:
        _forget_history(int(watched))

    await ctx.respond(
        embed=hikari.Embed(
            title="🛑 Automod Stopped",
            description="Messages are no longer being screened. `/automod check` still works.",
            color=hikari.Color(0x5865F2),
        )
    )


@automod.include
@arc.with_hook(require_module(ModuleType.AUTOMOD))
@arc.slash_subcommand("check", "Score a piece of text without posting it in chat.")
async def check(
    ctx: arc.GatewayContext,
    message: arc.Option[str, arc.StrParams("The text to score")],
    service: AutomodService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    if not service.is_available:
        await ctx.respond(
            "❌ No TypeSafe API key is configured, so nothing can be scored.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await ctx.defer()

    assessment = await service.assess(message, guild_id=ctx.guild_id, author_id=ctx.user.id)
    if assessment is None:
        await ctx.respond(
            "❌ Could not score that — it may be too short, or the API is unavailable. "
            "Check the logs for details.",
        )
        return

    await ctx.respond(embed=build_embed(assessment))


@automod.include
@arc.with_hook(require_module(ModuleType.AUTOMOD))
@arc.slash_subcommand("status", "Show the automod configuration.")
async def status(
    ctx: arc.GatewayContext,
    manager: ModuleManager = arc.inject(),
    service: AutomodService = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    config = await manager.get_module_config(ctx.guild_id, ModuleType.AUTOMOD)
    watched = config.settings.get(WATCH_CHANNEL_SETTING)
    automod_config = service.config

    embed = hikari.Embed(
        title="🤖 Automod Status",
        description="AI screening is **preview only** — it reports, it never acts.",
        color=hikari.Color(0x5865F2),
    )
    embed.add_field(
        name="API",
        value="✅ Connected" if service.is_available else "❌ No API key configured",
        inline=True,
    )
    embed.add_field(name="Model", value=f"`{automod_config.MODEL}`", inline=True)
    embed.add_field(
        name="Watching",
        value=f"<#{watched}>" if watched else "Nothing — use `/automod watch`",
        inline=True,
    )
    rows = ["```", f"{'category':<17}{'review':>7}{'block':>7}{'sev≥':>6}"]
    for category, label in CATEGORY_LABELS.items():
        policy = CATEGORY_POLICIES[category]
        block = "never" if policy.action_at > 1.0 else f"{policy.action_at:.2f}"
        rows.append(
            f"{label:<17}{policy.review_at:>7.2f}{block:>7}{policy.severity_floor:>6.1f}"
        )
    rows.append("```")
    embed.add_field(
        name="Thresholds per category",
        value="\n".join(rows),
        inline=False,
    )
    budget = service.budget
    embed.add_field(
        name="Request budget",
        value=(
            f"**{budget.guild_allowance()}/min** for this server right now — "
            f"{automod_config.GLOBAL_REQUESTS_PER_MINUTE}/min shared across "
            f"{budget.active_guilds()} active server(s), "
            f"between {automod_config.MIN_GUILD_REQUESTS_PER_MINUTE} and "
            f"{automod_config.MAX_GUILD_REQUESTS_PER_MINUTE} each.\n"
            "Messages over budget are skipped, not queued."
        ),
        inline=False,
    )
    embed.add_field(
        name="Limits",
        value=(
            f"first {automod_config.MAX_CONTENT_LENGTH} characters of a message · "
            f"{automod_config.CONTEXT_WINDOW} messages of context · "
            f"{automod_config.REQUEST_TIMEOUT:.0f}s timeout"
        ),
        inline=False,
    )
    await ctx.respond(embed=embed)


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
