from __future__ import annotations

from datetime import datetime
from typing import Optional

import hikari

from serenity.database.moderation_repository import ModerationRepository
from serenity.services.logging_service import LoggingService
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

def _audit_reason(moderator: hikari.User, case_number: int, reason: Optional[str]) -> str:
    """Format a reason string for Discord's audit log."""
    base = f"[Case #{case_number}] {moderator} ({moderator.id})"
    if reason:
        return f"{base}: {reason}"
    return base

# Audit log entries that become external cases. Timeouts arrive as MEMBER_UPDATE with a
# communication_disabled_until change, which record_external checks itself.
_EXTERNAL_ACTIONS: dict[object, str] = {
    hikari.AuditLogEventType.MEMBER_BAN_ADD: "ban",
    hikari.AuditLogEventType.MEMBER_BAN_REMOVE: "unban",
    hikari.AuditLogEventType.MEMBER_KICK: "kick",
}
# Audit log types record_external can turn into a case (MEMBER_UPDATE only for timeouts).
EXTERNAL_TYPES = frozenset({*_EXTERNAL_ACTIONS, hikari.AuditLogEventType.MEMBER_UPDATE})


class ModerationService:
    """Executes moderation actions and handles the case/log pipeline."""

    def __init__(
        self,
        mod_repo: ModerationRepository,
        logging_svc: LoggingService,
        rest: hikari.api.RESTClient
    ) -> None:
        self._mod_repo = mod_repo
        self._logging_svc = logging_svc
        self._rest = rest

    async def _create_case_and_log(
        self,
        *,
        guild_id: int,
        action: str,
        target: hikari.User,
        moderator: hikari.User,
        reason: Optional[str],
        duration_seconds: Optional[int] = None,
        expires_at: Optional[int] = None,
        external_source: Optional[str] = None,
    ) -> int:
        case_number = await self._mod_repo.create_case(
            guild_id=guild_id,
            action=action,
            target_user_id=target.id,
            moderator_id=moderator.id,
            reason = reason or "No reason provided",
            duration_seconds=duration_seconds,
            expires_at=expires_at,
            external_source=external_source,
        )

        await self._logging_svc.log_mod_action(
            guild_id=guild_id,
            action=action,
            target=target,
            moderator=moderator,
            reason=reason,
            case_number=case_number,
            duration=duration_seconds,
            external_source=external_source,
        )

        return case_number

    async def record_external(self, entry: hikari.AuditLogEntry, *, me: int) -> Optional[int]:
        """Make a case for a ban, unban, kick or timeout Serenity didn't take (#99).

        Returns the case number, or None when the entry isn't one of those or Serenity made it
        (its own commands already made the case).
        """
        if entry.user_id is None or entry.user_id == me or entry.target_id is None:
            return None

        action = _EXTERNAL_ACTIONS.get(entry.action_type)
        duration = expires_at = None
        if entry.action_type == hikari.AuditLogEventType.MEMBER_UPDATE:
            change = next(
                (c for c in entry.changes if c.key == hikari.AuditLogChangeKey.COMMUNICATION_DISABLED_UNTIL),
                None,
            )
            if change is None:
                return None
            until: Optional[datetime] = change.new_value
            if until is None:
                action = "untimeout"
            else:
                action = "timeout"
                duration = max(0, int((until - entry.id.created_at).total_seconds()))
                expires_at = int(until.timestamp())
        if action is None:
            return None

        async def user(user_id: hikari.Snowflake) -> hikari.User:
            cache = getattr(entry.app, "cache", None)
            return (cache and cache.get_user(user_id)) or await self._rest.fetch_user(user_id)

        try:
            target = await user(entry.target_id)
            moderator = await user(entry.user_id)
        except hikari.HTTPError:
            logger.warning(f"Couldn't fetch users for audit log entry {entry.id}", exc_info=True)
            return None

        return await self._create_case_and_log(
            guild_id=entry.guild_id,
            action=action,
            target=target,
            moderator=moderator,
            reason=entry.reason,
            duration_seconds=duration,
            expires_at=expires_at,
            external_source=moderator.username if moderator.is_bot else "Discord",
        )

    async def ban(
        self,
        *,
        guild_id: int,
        target: hikari.User,
        moderator: hikari.User,
        reason: Optional[str] = None,
        delete_message_seconds: int = 0
    ) -> int:
        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            target=target,
            moderator=moderator,
            reason=reason,
            action="ban"
        )

        try:
            await self._rest.ban_user(
                guild_id,
                target.id,
                delete_message_seconds=delete_message_seconds,
                reason=_audit_reason(moderator, case_number, reason)
            )
        except hikari.ForbiddenError:
            raise SerenityPermissionError(
                "I don't have permission to ban this user. "
                "Ensure my role is above theirs and I have the **Ban Members** permission."
            )

        return case_number


    async def softban(
        self,
        *,
        guild_id: int,
        target: hikari.User,
        moderator: hikari.User,
        reason: Optional[str] = None,
    ) -> int:
        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            action="softban",
            target=target,
            moderator=moderator,
            reason=reason,
        )

        try:
            await self._rest.ban_user(
                guild_id,
                target.id,
                delete_message_seconds=3600,  # 1 hour
                reason=_audit_reason(moderator, case_number, reason),
            )
            await self._rest.unban_user(
                guild_id,
                target.id,
                reason=f"Softban - automatic unban (Case #{case_number})",
            )
        except hikari.ForbiddenError:
            raise SerenityPermissionError(
                "I don't have permission to softban this user."
            )

        return case_number

    async def unban(
        self,
        *,
        guild_id: int,
        target: hikari.User,
        moderator: hikari.User,
        reason: Optional[str] = None,
    ) -> int:
        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            action="unban",
            target=target,
            moderator=moderator,
            reason=reason,
        )

        try:
            await self._rest.unban_user(
                guild_id,
                target.id,
                reason=_audit_reason(moderator, case_number, reason),
            )
        except hikari.NotFoundError:
            raise SerenityPermissionError("This user is not currently banned.")
        except hikari.ForbiddenError:
            raise SerenityPermissionError(
                "I don't have permission to unban this user."
            )

        return case_number

    async def kick(
        self,
        *,
        guild_id: int,
        target: hikari.Member,
        moderator: hikari.User,
        reason: Optional[str] = None,
    ) -> int:
        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            action="kick",
            target=target,
            moderator=moderator,
            reason=reason,
        )

        try:
            await self._rest.kick_user(
                guild_id,
                target.id,
                reason=_audit_reason(moderator, case_number, reason),
            )
        except hikari.ForbiddenError:
            raise SerenityPermissionError(
                "I don't have permission to kick this user. "
                "Ensure my role is above theirs and I have the **Kick Members** permission."
            )

        return case_number

    async def timeout(
        self,
        *,
        guild_id: int,
        target: hikari.Member,
        moderator: hikari.User,
        duration_seconds: int,
        reason: Optional[str] = None,
    ) -> int:
        """
        Timeout a member for a given duration. Returns the case number.

        Parameters
        ----------
        duration_seconds:
            Length of the timeout. Max 2419200 (28 days, Discord's limit).
        """
        from datetime import datetime, timedelta, timezone

        duration_seconds = min(duration_seconds, 2_419_200)
        expires_dt = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
        expires_ts = int(expires_dt.timestamp())

        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            action="timeout",
            target=target,
            moderator=moderator,
            reason=reason,
            duration_seconds=duration_seconds,
            expires_at=expires_ts,
        )

        try:
            await self._rest.edit_member(
                guild_id,
                target.id,
                communication_disabled_until=expires_dt,
                reason=_audit_reason(moderator, case_number, reason),
            )
        except hikari.ForbiddenError:
            raise SerenityPermissionError(
                "I don't have permission to timeout this user. "
                "Ensure my role is above theirs and I have the **Moderate Members** permission."
            )

        return case_number

    async def untimeout(
        self,
        *,
        guild_id: int,
        target: hikari.Member,
        moderator: hikari.User,
        reason: Optional[str] = None,
    ) -> int:
        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            action="untimeout",
            target=target,
            moderator=moderator,
            reason=reason,
        )

        try:
            await self._rest.edit_member(
                guild_id,
                target.id,
                communication_disabled_until=None,
                reason=_audit_reason(moderator, case_number, reason),
            )
        except hikari.ForbiddenError:
            raise SerenityPermissionError(
                "I don't have permission to remove this user's timeout."
            )

        return case_number

    async def warn(
        self,
        *,
        guild_id: int,
        target: hikari.Member,
        moderator: hikari.User,
        reason: Optional[str] = None,
    ) -> tuple[int, int]:
        """
        Issue a warning to a member.

        Returns
        -------
        (case_number, total_active_warnings)
        """
        case_number = await self._create_case_and_log(
            guild_id=guild_id,
            action="warn",
            target=target,
            moderator=moderator,
            reason=reason,
        )

        await self._mod_repo.add_warning(
            guild_id=guild_id,
            user_id=target.id,
            moderator_id=moderator.id,
            reason=reason or "No reason provided",
            case_id=case_number,
        )

        total_warnings = await self._mod_repo.get_warning_count(guild_id, target.id)

        return case_number, total_warnings
