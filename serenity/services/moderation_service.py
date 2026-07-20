from __future__ import annotations

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
    ) -> int:
        case_number = await self._mod_repo.create_case(
            guild_id=guild_id,
            action=action,
            target_user_id=target.id,
            moderator_id=moderator.id,
            reason = reason or "No reason provided",
            duration_seconds=duration_seconds,
            expires_at=expires_at
        )

        await self._logging_svc.log_mod_action(
            guild_id=guild_id,
            action=action,
            target=target,
            moderator=moderator,
            reason=reason,
            case_number=case_number,
            duration=duration_seconds
        )

        return case_number

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
