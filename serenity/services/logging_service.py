from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import hikari

from serenity.database.logging_repository import LoggingRepository
from serenity.services.metrics import LOG_EVENTS_SENT
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

_GREEN = hikari.Color(0x57F287)
_RED = hikari.Color(0xED4245)
_YELLOW = hikari.Color(0xFEE75C)
_BLUE = hikari.Color(0x5865F2)
_ORANGE = hikari.Color(0xE67E22)
_GREY = hikari.Color(0x99AAB5)

LOG_TYPES = ["member", "message", "voice", "server", "mod", "ticket"]
CHANNEL_NAMES: dict[str, str] = {t: f"{t}-log" for t in LOG_TYPES}
LOGS_CATEGORY = "📋 Serenity Logs"


async def setup_log_channels(
    rest: hikari.api.RESTClient,
    cache: hikari.api.Cache,
    repo: LoggingRepository,
    guild_id: int,
    bot_id: Optional[int],
    tickets_enabled: bool = False,
) -> tuple[dict[str, hikari.GuildChannel], list[str]]:
    """Give every log type a channel under the Serenity Logs category (/logging setup).

    The ticket log only gets one in servers using the Tickets module.

    Idempotent: log types whose channel still exists are left alone, and the category and
    channels are reused by name before anything new is created. Returns each log type's channel
    and the names that failed. Raises hikari.ForbiddenError without Manage Channels.
    """
    channels = cache.get_guild_channels_view_for_guild(guild_id)
    configured = {
        c["log_type"]: channels.get(c["channel_id"])
        for c in await repo.get_all_log_channels(guild_id)
        if c["channel_id"]
    }
    result = {t: ch for t, ch in configured.items() if ch is not None}
    missing = [
        t for t in LOG_TYPES if t not in result and (t != "ticket" or tickets_enabled)
    ]
    if not missing:
        return result, []

    # @everyone can't see or post; the bot can.
    overwrites = [
        hikari.PermissionOverwrite(
            id=hikari.Snowflake(guild_id),  # @everyone
            type=hikari.PermissionOverwriteType.ROLE,
            deny=hikari.Permissions.SEND_MESSAGES | hikari.Permissions.VIEW_CHANNEL,
        ),
    ]
    if bot_id:
        overwrites.append(
            hikari.PermissionOverwrite(
                id=hikari.Snowflake(bot_id),
                type=hikari.PermissionOverwriteType.MEMBER,
                allow=hikari.Permissions.SEND_MESSAGES
                | hikari.Permissions.VIEW_CHANNEL
                | hikari.Permissions.READ_MESSAGE_HISTORY
                | hikari.Permissions.EMBED_LINKS,
            )
        )

    category = next(
        (
            c
            for c in channels.values()
            if c.type == hikari.ChannelType.GUILD_CATEGORY and c.name == LOGS_CATEGORY
        ),
        None,
    ) or await rest.create_guild_category(guild_id, name=LOGS_CATEGORY)

    failed: list[str] = []
    for log_type in missing:
        name = CHANNEL_NAMES[log_type]
        try:
            channel = next(
                (c for c in channels.values() if c.parent_id == category.id and c.name == name),
                None,
            ) or await rest.create_guild_text_channel(
                guild_id, name=name, category=category, permission_overwrites=overwrites
            )
            await repo.set_log_channel(guild_id, log_type, channel.id)
            result[log_type] = channel
        except Exception as exc:
            logger.error(f"Failed to create {name} in guild {guild_id}: {exc}", exc_info=True)
            failed.append(name)
    return result, failed


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)

def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    parts = [f"{h}h"]
    if m:
        parts.append(f"{m}m")
    if s:
        parts.append(f"{s}s")
    return " ".join(parts)

def _truncate(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."

def _channel_type_label(channel: hikari.GuildChannel) -> str:
    mapping = {
        hikari.ChannelType.GUILD_TEXT: "Text",
        hikari.ChannelType.GUILD_VOICE: "Voice",
        hikari.ChannelType.GUILD_CATEGORY: "Category",
        hikari.ChannelType.GUILD_NEWS: "Announcement",
        hikari.ChannelType.GUILD_STAGE: "Stage",
        hikari.ChannelType.GUILD_FORUM: "Forum",
    }
    return mapping.get(channel.type, "Channel") # type: ignore

class LoggingService:
    """Sends formatted audit-log embeds to the channels configured via LoggingRepository."""

    def __init__(self, repo: LoggingRepository, rest: hikari.api.RESTClient) -> None:
        self._repo = repo
        self._rest = rest

    async def _send(
        self,
        guild_id: int,
        log_type: str,
        log_event: str,
        embed: hikari.Embed,
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None
    ) -> None:
        """
        Check the repository to see if this log type is configured, then send the embed.
        Errors are caught and logged so they never bubble up to the event handler.
        """

        try:
            should_log, target_channel_id = await self._repo.should_log_event(
                guild_id=guild_id,
                log_type=log_type,
                channel_id=channel_id,
                user_id=user_id
            )
            if not should_log or target_channel_id is None:
                return

            await self._rest.create_message(target_channel_id, embed=embed)
            LOG_EVENTS_SENT.labels(guild_id=str(guild_id), event=log_event).inc()
        except hikari.ForbiddenError:
            logger.warning(f"Missing permissions to send {log_type} log in guild {guild_id}")
        except hikari.NotFoundError:
            logger.warning(
                f"Log channel not found for {log_type} in guild {guild_id} - "
                "consider reconfiguring with /logging"
            )
        except Exception as exc:
            logger.error(
                f"Failed to send {log_type} log for guild {guild_id}: {exc}", exc_info=True
            )

    async def log_message_delete(self, event: hikari.MessageDeleteEvent) -> None:
        """Log a single message deletion using Hikari's cached message."""
        cached = event.old_message

        if cached is None:
            logger.debug(f"MessageDeleteEvent for message {event.message_id} has no cached message.")
            return

        if cached.guild_id is None:
            logger.debug(f"MessageDeleteEvent for message {event.message_id} has no guild ID.")
            return

        embed = hikari.Embed(title="🗑️ Message Deleted", color=_RED, timestamp=_now())
        if cached is not None:
            embed.add_field(
                "Author", f"<@{cached.author.id}> (`{cached.author}`)", inline=True
            )
            embed.add_field("Channel", f"<#{event.channel_id}>", inline=True)
            if cached.content:
                embed.add_field("Content", _truncate(cached.content), inline=False)
            if cached.attachments:
                embed.add_field(
                    "Attachments",
                    "\n".join(str(a.url) for a in cached.attachments[:5]),
                    inline=False,
                )
        else:
            embed.description = "*Message content not in Serenity's cache*"
            embed.add_field("Channel", f"<#{event.channel_id}>", inline=True)

        embed.set_footer(text=f"Message ID: {event.message_id}")
        await self._send(
            log_event="message_delete",
            guild_id=cached.guild_id,
            log_type="message",
            embed=embed,
            channel_id=event.channel_id,
            user_id=cached.author.id
        )

    async def log_message_edit(self, event: hikari.GuildMessageUpdateEvent) -> None:
        """Log a guild message edit showing before and after content."""
        if event.author is None or event.message.guild_id is None:
            logger.debug(f"MessageUpdateEvent for message {event.message_id} has no author or guild ID.")
            return
        if event.is_bot:
            return
        # Discord also sends MESSAGE_UPDATE for non-edits (embed/media unfurls, CDN URL refreshes).
        # Never-edited messages have no edited_timestamp; like Discord does for large bots,
        # ignore updates to messages older than 5 weeks.
        if not isinstance(event.message.edited_timestamp, datetime):
            return
        if _now() - event.message_id.created_at > timedelta(weeks=5):
            return
        before_content = event.old_message.content if event.old_message else None
        after_content = event.content

        if not before_content and not after_content:
            return
        if before_content == after_content:
            return

        embed = hikari.Embed(title="✏️ Message Edited", color=_YELLOW, timestamp=_now())
        embed.add_field("Author", f"<@{event.author.id}> (`{event.author}`)", inline=True) # type: ignore
        embed.add_field("Channel", f"<#{event.channel_id}>", inline=True)

        if before_content:
            embed.add_field("Before", _truncate(before_content, 512), inline=False)
        if after_content:
            embed.add_field("After", _truncate(after_content, 512), inline=False)

        embed.add_field(
            "Jump to Message",
            f"[Click Here](https://discord.com/channels/{event.guild_id}/{event.channel_id}/{event.message_id})",
            inline=False
        )
        embed.set_footer(text=f"Message ID: {event.message_id}")
        await self._send(
            log_event="message_edit",
            guild_id=event.guild_id,
            log_type="message",
            embed=embed,
            channel_id=event.channel_id,
            user_id=event.author.id # type: ignore
        )

    async def log_bulk_message_delete(self, event: hikari.GuildBulkMessageDeleteEvent) -> None:
        """Log a bulk message delete event."""
        embed = hikari.Embed(
            title="🗑️ Bulk Messages Deleted",
            description=f"**{len(event.message_ids)}** messages were deleted.",
            color=_RED,
            timestamp=_now(),
        )
        embed.add_field("Channel", f"<#{event.channel_id}>", inline=True)
        embed.set_footer(text=f"Channel ID: {event.channel_id}")

        await self._send(
            log_event="bulk_message_delete",
            guild_id=event.guild_id,
            log_type="message",
            embed=embed,
            channel_id=event.channel_id,
        )

    async def log_member_join(self, event: hikari.MemberCreateEvent) -> None:
        """Log when a new member joins the guild."""
        member = event.member
        embed = hikari.Embed(
            title="👋 Member Joined",
            color=_GREEN,
            timestamp=_now(),
        )
        embed.set_thumbnail(member.display_avatar_url)
        embed.add_field("User", f"{member.mention} (`{member}`)", inline=True)
        embed.add_field(
            "Account Created", f"<t:{int(member.created_at.timestamp())}:R>", inline=True
        )
        embed.set_footer(text=f"User ID: {member.id}")

        await self._send(
            log_event="member_join",
            guild_id=event.guild_id,
            log_type="member",
            embed=embed,
            user_id=member.id,
        )

    async def log_member_leave(self, event: hikari.MemberDeleteEvent) -> None:
        """Log when a member leaves the guild."""
        user = event.user
        embed = hikari.Embed(
            title="🚪 Member Left",
            color=_RED,
            timestamp=_now(),
        )
        embed.set_thumbnail(user.display_avatar_url)
        embed.add_field("User", f"{user.mention} (`{user}`)", inline=True)
        if event.old_member:
            if event.old_member.joined_at:
                embed.add_field(
                    "Joined", f"<t:{int(event.old_member.joined_at.timestamp())}:R>", inline=True
                )
            role_ids = event.old_member.role_ids or []
            if role_ids:
                embed.add_field(
                    "Roles", " ".join(f"<@&{r}>" for r in role_ids[:10]), inline=False
                )
        embed.set_footer(text=f"User ID: {user.id}")
        await self._send(
            log_event="member_leave",
            guild_id=event.guild_id,
            log_type="member",
            embed=embed,
            user_id=user.id,
        )

    async def log_member_update(self, event: hikari.MemberUpdateEvent) -> None:
        """Log nickname, role, or server avatar changes."""
        old = event.old_member
        new = event.member

        if old is None:
            return

        changes: list[tuple[str, str, str]] = []

        if old.nickname != new.nickname:
            changes.append(("Nickname", old.nickname or "*None*", new.nickname or "*None*"))

        old_roles = set(old.role_ids or [])
        new_roles = set(new.role_ids or [])
        added_roles = new_roles - old_roles
        removed_roles = old_roles - new_roles

        if added_roles:
            changes.append(("Roles Added", "", " ".join(f"<@&{r}>" for r in added_roles)))
        if removed_roles:
            changes.append(("Roles Removed", " ".join(f"<@&{r}>" for r in removed_roles), ""))

        if old.guild_avatar_hash != new.guild_avatar_hash:
            changes.append(("Server Avatar", "*(changed)*", "*(see thumbnail)*"))

        if not changes:
            return

        embed = hikari.Embed(title="📝 Member Updated", color=_BLUE, timestamp=_now())
        embed.set_thumbnail(new.display_avatar_url)
        embed.add_field("User", f"{new.mention} (`{new}`)", inline=False)

        for name, before, after in changes:
            if before and after:
                embed.add_field(name, f"**Before:** {before}\n**After:** {after}", inline=False)
            elif after:
                embed.add_field(name, after, inline=False)
            elif before:
                embed.add_field(name, before, inline=False)

        embed.set_footer(text=f"User ID: {new.id}")
        await self._send(
            log_event="member_update",
            guild_id=event.guild_id,
            log_type="member",
            embed=embed,
            user_id=new.id,
        )

    async def log_voice_state_update(self, event: hikari.VoiceStateUpdateEvent) -> None:
        """Log voice channel joins, leaves, moves, mutes, and deafens."""
        old = event.old_state
        new = event.state
        user_id = new.user_id

        if old is None or old.channel_id is None:
            if new.channel_id is None:
                return
            embed = hikari.Embed(title="🔊 Joined Voice Channel", color=_GREEN, timestamp=_now())
            embed.add_field("User", f"<@{user_id}>", inline=True)
            embed.add_field("Channel", f"<#{new.channel_id}>", inline=True)

        elif new.channel_id is None:
            embed = hikari.Embed(title="🔇 Left Voice Channel", color=_RED, timestamp=_now())
            embed.add_field("User", f"<@{user_id}>", inline=True)
            embed.add_field("Channel", f"<#{old.channel_id}>", inline=True)

        elif old.channel_id != new.channel_id:
            embed = hikari.Embed(title="↔️ Moved Voice Channel", color=_BLUE, timestamp=_now())
            embed.add_field("User", f"<@{user_id}>", inline=False)
            embed.add_field("From", f"<#{old.channel_id}>", inline=True)
            embed.add_field("To", f"<#{new.channel_id}>", inline=True)

        else:
            state_changes: list[str] = []
            if old.is_guild_muted != new.is_guild_muted:
                 state_changes.append(f"Server Mute: **{'On' if new.is_guild_muted else 'Off'}**")
            if old.is_guild_deafened != new.is_guild_deafened:
                state_changes.append(f"Server Deafen: **{'On' if new.is_guild_deafened else 'Off'}**")
            if old.is_streaming != new.is_streaming:
                state_changes.append(f"Streaming: **{'Started' if new.is_streaming else 'Stopped'}**")
            if old.is_video_enabled != new.is_video_enabled:
                state_changes.append(f"Camera: **{'On' if new.is_video_enabled else 'Off'}**")

            if not state_changes:
                return

            embed = hikari.Embed(title="🎙️ Voice State Changed", color=_YELLOW, timestamp=_now())
            embed.add_field("User", f"<@{user_id}>", inline=True)
            embed.add_field("Channel", f"<#{new.channel_id}>", inline=True)
            embed.add_field("Changes", "\n".join(state_changes), inline=False)

        embed.set_footer(text=f"User ID: {user_id}")

        await self._send(
            log_event="voice_state_update",
            guild_id=event.guild_id,
            log_type="voice",
            embed=embed,
            user_id=user_id,
        )

    async def log_channel_create(self, event: hikari.GuildChannelCreateEvent) -> None:
        """Log when a new channel is created in the guild."""
        channel = event.channel
        embed = hikari.Embed(
            title=f"➕ {_channel_type_label(channel)} Channel Created",
            color=_GREEN,
            timestamp=_now(),
        )
        embed.add_field("Name", channel.mention, inline=True)
        embed.add_field("Type", _channel_type_label(channel), inline=True)

        if hasattr(channel, "parent_id") and channel.parent_id:
            embed.add_field("Category", f"<#{channel.parent_id}>", inline=True)
        embed.set_footer(text=f"Channel ID: {channel.id}")

        await self._send(
            log_event="channel_create",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
            channel_id=channel.id,
        )

    async def log_channel_delete(self, event: hikari.GuildChannelDeleteEvent) -> None:
        """Log when a channel is deleted in the guild."""
        channel = event.channel
        embed = hikari.Embed(
            title=f"🗑️ {_channel_type_label(channel)} Channel Deleted",
            color=_RED,
            timestamp=_now(),
        )
        embed.add_field("Name", f"`#{channel.name}`", inline=True)
        embed.add_field("Type", _channel_type_label(channel), inline=True)
        embed.set_footer(text=f"Channel ID: {channel.id}")

        await self._send(
            log_event="channel_delete",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
        )

    async def log_channel_update(self, event: hikari.GuildChannelUpdateEvent) -> None:
        """Log when a channel is updated."""
        old = event.old_channel
        new = event.channel

        if old is None:
            return

        changes: list[str] = []

        if old.name != new.name:
            changes.append(f"**Name:** `#{old.name}` → `#{new.name}`")

        if isinstance(old, hikari.GuildTextChannel) and isinstance(new, hikari.GuildTextChannel):
            if old.topic != new.topic:
                before = old.topic or "*None*"
                after = new.topic or "*None*"
                changes.append(
                    f"**Topic:**\nBefore: {_truncate(before, 256)}\nAfter: {_truncate(after, 256)}"
                )
            if old.rate_limit_per_user != new.rate_limit_per_user:
                changes.append(
                    f"**Slowmode:** {old.rate_limit_per_user}s → {new.rate_limit_per_user}s"
                )
            if old.is_nsfw != new.is_nsfw:
                changes.append(f"**NSFW:** {'On' if new.is_nsfw else 'Off'}")

        if not changes:
            return

        embed = hikari.Embed(
            title=f"✏️ {_channel_type_label(new)} Channel Updated",
            color=_YELLOW,
            timestamp=_now(),
        )
        embed.add_field("Channel", new.mention, inline=True)
        embed.add_field("Changes", "\n".join(changes), inline=False)
        embed.set_footer(text=f"Channel ID: {new.id}")

        await self._send(
            log_event="channel_update",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
            channel_id=new.id,
        )

    async def log_role_create(self, event: hikari.RoleCreateEvent) -> None:
        role = event.role
        embed = hikari.Embed(
            title="➕ Role Created",
            color=hikari.Color(int(role.color)) if role.color else _GREEN,
            timestamp=_now()
        )
        embed.add_field("Name", role.mention, inline=True)
        embed.add_field("Color", str(role.color), inline=True)
        embed.add_field("Mentionable", "Yes" if role.is_mentionable else "No", inline=True)
        embed.add_field("Hoisted", "Yes" if role.is_hoisted else "No", inline=True)
        embed.set_footer(text=f"Role ID: {role.id}")

        await self._send(
            log_event="role_create",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
        )

    async def log_role_delete(self, event: hikari.RoleDeleteEvent) -> None:
        role = event.old_role
        embed = hikari.Embed(title="🗑️ Role Deleted", color=_RED, timestamp=_now())
        if role:
            embed.add_field("Name", f"`@{role.name}`", inline=True)
            embed.add_field("Color", str(role.color), inline=True)
        embed.set_footer(text=f"Role ID: {event.role_id}")

        await self._send(
            log_event="role_delete",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
        )

    async def log_role_update(self, event: hikari.RoleUpdateEvent) -> None:
        old = event.old_role
        new = event.role

        if old is None:
            return

        changes: list[str] = []
        if old.name != new.name:
            changes.append(f"**Name:** `{old.name}` → `{new.name}`")
        if old.color != new.color:
            changes.append(f"**Color:** `{old.color}` → `{new.color}`")
        if old.is_mentionable != new.is_mentionable:
            changes.append(f"**Mentionable:** {'Yes' if new.is_mentionable else 'No'}")
        if old.is_hoisted != new.is_hoisted:
            changes.append(f"**Hoisted:** {'Yes' if new.is_hoisted else 'No'}")

        if not changes:
            return

        embed = hikari.Embed(
            title="✏️ Role Updated",
            color=hikari.Color(int(new.color)) if new.color else _YELLOW,
            timestamp=_now(),
        )
        embed.add_field("Role", new.mention, inline=True)
        embed.add_field("Changes", "\n".join(changes), inline=False)
        embed.set_footer(text=f"Role ID: {new.id}")

        await self._send(
            log_event="role_update",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
        )

    async def log_guild_update(self, event: hikari.GuildUpdateEvent) -> None:
        old = event.old_guild
        new = event.guild

        if old is None:
            return

        changes: list[str] = []
        if old.name != new.name:
            changes.append(f"**Name:** `{old.name}` → `{new.name}`")
        if old.icon_hash != new.icon_hash:
            changes.append("**Icon:** *(changed)*")
        if old.description != new.description:
            before = old.description or "*None*"
            after = new.description or "*None*"
            changes.append(f"**Description:**\nBefore: {before}\nAfter: {after}")
        if old.verification_level != new.verification_level:
            changes.append(
                f"**Verification Level:** `{old.verification_level}` → `{new.verification_level}`"
            )

        if not changes:
            return

        embed = hikari.Embed(title="⚙️ Server Updated", color=_BLUE, timestamp=_now())
        embed.set_thumbnail(new.make_icon_url())
        embed.add_field("Changes", "\n".join(changes), inline=False)
        embed.set_footer(text=f"Guild ID: {new.id}")

        await self._send(log_event="guild_update", guild_id=new.id, log_type="server", embed=embed)

    async def log_emoji_update(self, event: hikari.EmojisUpdateEvent) -> None:
        old_ids = {e.id for e in event.old_emojis} # type: ignore
        new_ids = {e.id for e in event.emojis}

        added = [e for e in event.emojis if e.id not in old_ids]
        removed = [e for e in event.old_emojis if e.id not in new_ids] # type: ignore

        if not added and not removed:
            return

        embed = hikari.Embed(title="😀 Emojis Updated", color=_YELLOW, timestamp=_now())
        if added:
            embed.add_field("Added", " ".join(str(e) for e in added[:20]), inline=False)
        if removed:
            embed.add_field(
                "Removed", " ".join(f"`:{e.name}:`" for e in removed[:20]), inline=False
            )

        await self._send(
            log_event="emoji_update",
            guild_id=event.guild_id,
            log_type="server",
            embed=embed,
        )

    async def log_mod_action(
        self,
        *,
        guild_id: int,
        action: str,
        target: hikari.User,
        moderator: hikari.User,
        reason: Optional[str],
        case_number: int,
        duration: Optional[int] = None,
    ) -> None:
        """
        Send a moderation action to the mod-log channel.

        Parameters
        ----------
        action:
            One of: "ban", "unban", "kick", "timeout", "untimeout", "warn", "softban"
        duration:
            Duration in seconds, only relevant for timeouts.
        """
        action_meta: dict[str, tuple[str, hikari.Color]] = {
            "ban": ("🔨 Ban", _RED),
            "softban": ("🔨 Softban", _RED),
            "unban": ("🔓 Unban", _RED),
            "kick": ("👢 Kick", _RED),
            "timeout": ("⏱️ Timeout", _RED),
            "untimeout": ("✅ Timeout Removed", _RED),
            "warn": ("⚠️ Warning", _RED),
        }
        title_str, color = action_meta.get(action.lower(), (f"🛡️ {action.title()}", _GREY))
        embed = hikari.Embed(
            title=f"{title_str} | Case #{case_number}",
            color=color,
            timestamp=_now(),
        )
        embed.set_thumbnail(target.display_avatar_url)
        embed.add_field("User", f"{target.mention} (`{target}`)", inline=True)
        embed.add_field("Moderator", f"{moderator.mention} (`{moderator}`)", inline=True)

        if duration is not None:
            embed.add_field("Duration", _format_duration(duration), inline=True)

        embed.add_field(
            "Reason", _truncate(reason or "No reason provided", 512), inline=False
        )
        embed.set_footer(text=f"User ID: {target.id} | Case #{case_number}")

        await self._send(
            log_event=f"mod_{action.lower()}",
            guild_id=guild_id, log_type="mod", embed=embed, user_id=target.id
        )

    async def log_mod_case(
        self,
        *,
        guild_id: int,
        action: str,
        target_id: int,
        moderator_id: int,
        reason: Optional[str],
        case_number: int,
        duration: Optional[int] = None,
    ) -> None:
        """
        Variant of log_mod_action that accepts raw IDs instead of User objects.
        Fetches users from the REST API. Prefer log_mod_action when you already
        have User objects to avoid the extra API calls.
        """
        try:
            target = await self._rest.fetch_user(target_id)
            moderator = await self._rest.fetch_user(moderator_id)
        except Exception as exc:
            logger.warning(f"Could not fetch users for mod log: {exc}")
            return

        await self.log_mod_action(
            guild_id=guild_id,
            action=action,
            target=target,
            moderator=moderator,
            reason=reason,
            case_number=case_number,
            duration=duration,
        )

    async def log_ticket_event(
        self,
        *,
        guild_id: int,
        kind: str,
        number: int,
        type_name: str,
        actor_id: Optional[int],
        channel_id: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        """A ticket was opened, claimed, closed, ... posted to the Ticket log."""
        title, color = {
            "opened": ("🎫 Ticket opened", _GREEN),
            "claimed": ("🙋 Ticket claimed", _BLUE),
            "assigned": ("📌 Ticket assigned", _BLUE),
            "unclaimed": ("↩️ Ticket unclaimed", _YELLOW),
            "closed": ("🔒 Ticket closed", _GREY),
            "reopened": ("↩️ Ticket reopened", _GREEN),
            "transferred": ("🔀 Ticket transferred", _BLUE),
            "deletion_requested": ("🗑️ Transcript deletion requested", _ORANGE),
        }.get(kind, (f"🎫 Ticket {kind.replace('_', ' ')}", _GREY))
        embed = hikari.Embed(title=f"{title} | #{number}", color=color, timestamp=_now())
        embed.add_field("Type", _truncate(type_name, 256), inline=True)
        if actor_id:
            embed.add_field("By", f"<@{actor_id}>", inline=True)
        if channel_id:
            embed.add_field("Channel", f"<#{channel_id}>", inline=True)
        if detail:
            embed.add_field("Details", _truncate(detail, 1024), inline=False)
        await self._send(
            log_event=f"ticket_{kind}",
            guild_id=guild_id,
            log_type="ticket",
            embed=embed,
            channel_id=channel_id,
            user_id=actor_id,
        )

    async def log_dashboard_change(
        self, *, guild_id: int, user_id: int, setting: str, old: str, new: str
    ) -> None:
        """A setting changed from the web dashboard, posted to the Server log."""
        embed = hikari.Embed(title="🖥️ Changed from the dashboard", color=_BLUE, timestamp=_now())
        embed.add_field("User", f"<@{user_id}>", inline=True)
        embed.add_field("Setting", _truncate(setting, 256), inline=True)
        embed.add_field("Change", _truncate(f"{old} → {new}", 1024), inline=False)
        embed.set_footer(text=f"User ID: {user_id}")
        await self._send(
            log_event="dashboard_change",
            guild_id=guild_id,
            log_type="server",
            embed=embed,
            user_id=user_id,
        )
