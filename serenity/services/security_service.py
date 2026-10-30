"""Security module: alert the owner about bursts of destructive actions (#152) and about single
changes that make the server less safe (#153).

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
WARNING_DM_COOLDOWN = 60  # warnings are always logged; the owner gets one DM per actor per minute

# kind: (audit log types, default threshold per WINDOW, how the alert describes one)
KINDS: dict[str, tuple[tuple[_A, ...], int, str]] = {
    "removals": ((_A.MEMBER_BAN_ADD, _A.MEMBER_KICK, _A.MEMBER_PRUNE), 5, "members banned or kicked"),
    "channels": ((_A.CHANNEL_DELETE,), 3, "channels deleted"),
    "roles": ((_A.ROLE_DELETE,), 3, "roles deleted"),
    "webhooks": ((_A.WEBHOOK_CREATE,), 3, "webhooks created"),
    "emojis": ((_A.EMOJI_DELETE, _A.STICKER_DELETE), 5, "emojis or stickers deleted"),
}
_KIND_OF = {t: kind for kind, (types, _, _) in KINDS.items() for t in types}

_P = hikari.Permissions
# Permissions that let someone take a server apart (#153).
DANGEROUS = (
    _P.ADMINISTRATOR | _P.MANAGE_GUILD | _P.MANAGE_ROLES | _P.MANAGE_CHANNELS | _P.BAN_MEMBERS
    | _P.KICK_MEMBERS | _P.MANAGE_WEBHOOKS | _P.MENTION_ROLES
)
_K = hikari.AuditLogChangeKey
# log_event names of the #153 warnings (for LOG_EVENTS).
WARNING_EVENTS = (
    "dangerous_permissions", "dangerous_role_given", "bot_added", "webhook_created", "security_lowered",
)
# Server settings where a lower value is less safe.
_LEVELS = {
    _K.VERIFICATION_LEVEL: "Verification level",
    _K.MFA_LEVEL: "2FA requirement for moderators",
    _K.EXPLICIT_CONTENT_FILTER: "Explicit content filter",
}


def _perm_names(perms: hikari.Permissions) -> str:
    return ", ".join(
        p.name.replace("_", " ").title() for p in _P if p in DANGEROUS and p & perms and p.name
    )


# Settings live in the Security module's settings JSON; missing keys use these.
DEFAULTS: dict[str, Any] = {
    "antinuke": True,
    "warnings": True,
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
        self._warned: dict[tuple[int, int], float] = {}

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

    def _dangerous(self, entry: hikari.AuditLogEntry) -> Optional[tuple[str, str, str]]:
        """(title, description, log_event) when this entry makes the server less safe."""
        guild_id, target = int(entry.guild_id), entry.target_id
        changes = {str(c.key): c for c in entry.changes}

        if entry.action_type in (_A.ROLE_CREATE, _A.ROLE_UPDATE) and "permissions" in changes:
            c = changes["permissions"]
            gained = _P(c.new_value or 0) & ~_P(c.old_value or 0) & DANGEROUS
            if gained:
                role = "@everyone" if target == guild_id else f"<@&{target}>"
                return (
                    "⚠️ Dangerous permissions granted",
                    f"{role} gained **{_perm_names(gained)}**.",
                    "dangerous_permissions",
                )

        if entry.action_type == _A.MEMBER_ROLE_UPDATE and "$add" in changes:
            risky = [
                r for r in (changes["$add"].new_value or {}).values()
                if (role := self._cache.get_role(r.id)) is not None and role.permissions & DANGEROUS
            ]
            if risky:
                roles = " ".join(f"<@&{r.id}>" for r in risky)
                perms = _P.NONE
                for r in risky:
                    perms |= self._cache.get_role(r.id).permissions  # type: ignore[union-attr]
                return (
                    "⚠️ Powerful role given",
                    f"<@{target}> was given {roles} (**{_perm_names(perms)}**).",
                    "dangerous_role_given",
                )

        if entry.action_type in (_A.CHANNEL_OVERWRITE_CREATE, _A.CHANNEL_OVERWRITE_UPDATE) and "allow" in changes:
            c = changes["allow"]
            gained = _P(c.new_value or 0) & ~_P(c.old_value or 0) & DANGEROUS
            if gained:
                return (
                    "⚠️ Dangerous channel permissions",
                    f"An override in <#{target}> now allows **{_perm_names(gained)}**.",
                    "dangerous_permissions",
                )

        if entry.action_type == _A.BOT_ADD:
            return "🤖 Bot added", f"<@{target}> was added to the server.", "bot_added"

        if entry.action_type == _A.WEBHOOK_CREATE:
            channel = changes.get("channel_id")
            where = f" in <#{channel.new_value}>" if channel else ""
            name = changes.get("name")
            return (
                "🪝 Webhook created",
                f"Webhook **{name.new_value if name else 'unnamed'}** was created{where}. Webhooks can "
                "post as anyone, so check it's expected.",
                "webhook_created",
            )

        if entry.action_type == _A.GUILD_UPDATE:
            lowered = [
                f"**{label}:** {getattr(c.old_value, 'name', c.old_value)} → {getattr(c.new_value, 'name', c.new_value)}"
                for key, label in _LEVELS.items()
                if (c := changes.get(str(key))) and int(c.new_value or 0) < int(c.old_value or 0)
            ]
            if (c := changes.get("vanity_url_code")) and c.old_value:  # removed or replaced, not gained
                lowered.append(f"**Vanity URL:** {c.old_value or 'none'} → {c.new_value or 'none'}")
            if lowered:
                return "⚠️ Server security lowered", "\n".join(lowered), "security_lowered"
        return None

    async def check_dangerous(self, entry: hikari.AuditLogEntry, *, me: int) -> None:
        """Warn about one change that makes the server less safe, whoever made it (#153)."""
        if entry.user_id is None or entry.user_id == me:
            return
        found = self._dangerous(entry)
        if found is None:
            return
        settings = await self.settings(int(entry.guild_id))
        if settings is None or not settings["warnings"]:
            return
        title, description, log_event = found
        guild_id, actor, now = int(entry.guild_id), int(entry.user_id), time.monotonic()
        self._warned = {k: t for k, t in self._warned.items() if now - t < WARNING_DM_COOLDOWN}
        dm = (guild_id, actor) not in self._warned
        self._warned[(guild_id, actor)] = now
        await self.alert(
            guild_id,
            title=title,
            description=description + (f"\n*Reason: {entry.reason}*" if entry.reason else ""),
            actor=actor,
            log_event=log_event,
            dm=dm,
        )

    async def alert(
        self, guild_id: int, *, title: str, description: str, actor: int, log_event: str, dm: bool = True
    ) -> None:
        """Post a security alert to the Server log and DM the server owner (#152, #153)."""
        def embed(text: str) -> hikari.Embed:
            e = hikari.Embed(title=title, description=text, color=hikari.Color(0xED4245))
            e.add_field("By", f"<@{actor}>", inline=True)
            e.set_footer(text=f"User ID: {actor}")
            return e

        await self._logging.log_security_alert(
            guild_id=guild_id, embed=embed(description), log_event=log_event
        )

        guild = self._cache.get_guild(guild_id)
        if not dm or guild is None or guild.owner_id == actor:
            return
        try:
            channel = await self._rest.create_dm_channel(guild.owner_id)
            await self._rest.create_message(channel, embed=embed(f"**{guild.name}**\n{description}"))
        except hikari.HTTPError:
            logger.info(f"Couldn't DM the owner of guild {guild_id} a security alert")
