"""Security module: alert the owner about bursts of destructive actions (#152).

Counts each actor's destructive audit log entries over a rolling minute, in memory. Crossing a
threshold DMs the server owner and posts to the Server log. Alerts only: nothing is undone.
"""

import time
from collections import defaultdict, deque
from typing import Any, Optional

import hikari

from serenity.core.modules import ModuleManager, ModuleType
from serenity.services.logging_service import LoggingService
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

_A = hikari.AuditLogEventType
WINDOW = 60  # seconds
ALERT_COOLDOWN = 600  # one alert per actor per 10 minutes

# kind: (audit log types, default threshold per WINDOW, how the alert describes one)
KINDS: dict[str, tuple[tuple[_A, ...], int, str]] = {
    "removals": ((_A.MEMBER_BAN_ADD, _A.MEMBER_KICK, _A.MEMBER_PRUNE), 5, "members banned or kicked"),
    "channels": ((_A.CHANNEL_DELETE,), 3, "channels deleted"),
    "roles": ((_A.ROLE_DELETE,), 3, "roles deleted"),
    "webhooks": ((_A.WEBHOOK_CREATE,), 3, "webhooks created"),
    "emojis": ((_A.EMOJI_DELETE, _A.STICKER_DELETE), 5, "emojis or stickers deleted"),
}
_KIND_OF = {t: kind for kind, (types, _, _) in KINDS.items() for t in types}

# Settings live in the Security module's settings JSON; missing keys use these.
DEFAULTS: dict[str, Any] = {
    "antinuke": True,
    "thresholds": {kind: n for kind, (_, n, _) in KINDS.items()},
    "trusted_users": [],
    "trusted_roles": [],
}


def settings_of(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        **DEFAULTS,
        **raw,
        "thresholds": {**DEFAULTS["thresholds"], **raw.get("thresholds", {})},
    }


class SecurityService:
    def __init__(
        self,
        modules: ModuleManager,
        rest: hikari.api.RESTClient,
        cache: hikari.api.Cache,
        logging_svc: LoggingService,
    ) -> None:
        self.modules = modules
        self._rest = rest
        self._cache = cache
        self._logging = logging_svc
        self._seen: defaultdict[tuple[int, int, str], deque[float]] = defaultdict(deque)
        self._alerted: dict[tuple[int, int], float] = {}

    async def settings(self, guild_id: int) -> Optional[dict[str, Any]]:
        """The guild's security settings, or None when the module is off."""
        config = await self.modules.get_module_config(guild_id, ModuleType.SECURITY)
        return settings_of(config.settings) if config.is_enabled else None

    def _prune(self, now: float) -> None:
        """Forget actions older than the window, and actors with none left."""
        for key in list(self._seen):
            seen = self._seen[key]
            while seen and now - seen[0] > WINDOW:
                seen.popleft()
            if not seen:
                del self._seen[key]
        self._alerted = {k: t for k, t in self._alerted.items() if now - t < ALERT_COOLDOWN}

    def _trusted(self, guild_id: int, actor: int, settings: dict[str, Any]) -> bool:
        guild = self._cache.get_guild(guild_id)
        if guild is not None and guild.owner_id == actor:
            return True
        if str(actor) in map(str, settings["trusted_users"]):
            return True
        member = self._cache.get_member(guild_id, actor)
        trusted_roles = set(map(str, settings["trusted_roles"]))
        return member is not None and any(str(r) in trusted_roles for r in member.role_ids)

    async def on_entry(self, entry: hikari.AuditLogEntry, *, me: int) -> None:
        kind = _KIND_OF.get(entry.action_type)  # type: ignore[call-overload]
        if kind is None or entry.user_id is None or entry.user_id == me:
            return
        guild_id, actor = int(entry.guild_id), int(entry.user_id)
        settings = await self.settings(guild_id)
        if settings is None or not settings["antinuke"] or self._trusted(guild_id, actor, settings):
            return

        now = time.monotonic()
        seen = self._seen[(guild_id, actor, kind)]
        seen.append(now)
        self._prune(now)
        if len(seen) < settings["thresholds"][kind]:
            return
        if now - self._alerted.get((guild_id, actor), -ALERT_COOLDOWN) < ALERT_COOLDOWN:
            return
        self._alerted[(guild_id, actor)] = now

        counts = {
            k: len(self._seen.get((guild_id, actor, k), ()))
            for k in KINDS
        }
        await self.alert(
            guild_id,
            title="🚨 Possible nuke",
            description=(
                f"<@{actor}> made a burst of destructive changes in the last minute:\n"
                + "\n".join(f"• **{n}** {KINDS[k][2]}" for k, n in counts.items() if n)
            ),
            actor=actor,
            log_event="antinuke_alert",
        )

    async def alert(
        self, guild_id: int, *, title: str, description: str, actor: int, log_event: str, dm: bool = True
    ) -> None:
        """Post a security alert to the Server log and DM the server owner (#152, #153)."""
        embed = hikari.Embed(title=title, description=description, color=hikari.Color(0xED4245))
        embed.add_field("By", f"<@{actor}>", inline=True)
        embed.set_footer(text=f"User ID: {actor}")
        await self._logging.log_security_alert(guild_id=guild_id, embed=embed, log_event=log_event)

        guild = self._cache.get_guild(guild_id)
        if not dm or guild is None or guild.owner_id == actor:
            return
        try:
            channel = await self._rest.create_dm_channel(guild.owner_id)
            embed.description = f"**{guild.name}**\n{description}"
            await self._rest.create_message(channel, embed=embed)
        except hikari.HTTPError:
            logger.info(f"Couldn't DM the owner of guild {guild_id} a security alert")
