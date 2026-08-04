from __future__ import annotations

import arc
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleNotEnabledError, ModuleType
from serenity.database.logging_repository import LoggingRepository
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

LOG_TYPES = ["member", "message", "voice", "server", "mod"]

LOG_TYPE_LABELS: dict[str, str] = {
    "member":  "👤 Member",
    "message": "💬 Message",
    "voice":   "🔊 Voice",
    "server":  "⚙️ Server",
    "mod":     "🔨 Mod",
}

CHANNEL_NAMES: dict[str, str] = {
    "member":  "member-log",
    "message": "message-log",
    "voice":   "voice-log",
    "server":  "server-log",
    "mod":     "mod-log",
}

plugin = arc.GatewayPlugin(
    "logging_commands",
    default_permissions=hikari.Permissions.MANAGE_GUILD,
)

logging_group = plugin.include_slash_group(
    "logging",
    "Configure audit logging for this server.",
)

@plugin.set_error_handler
async def on_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            "❌ The **Logging** module is not enabled. "
            "Use `/module enable logging` first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    raise exc

async def _set_channel(
    ctx: arc.GatewayContext,
    repo: LoggingRepository,
    log_type: str,
    channel: hikari.TextableGuildChannel,
) -> None:
    """Set a log channel and confirm to the user."""
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await repo.set_log_channel(ctx.guild_id, log_type, channel.id)

    label = LOG_TYPE_LABELS[log_type]
    embed = hikari.Embed(
        title=f"✅ {label} Log Channel Set",
        description=f"{channel.mention} will now receive **{label.lower()}** events.",
        color=hikari.Color(0x57F287),
    )
    await ctx.respond(embed=embed)
    logger.info(
        f"Set {log_type} log channel to {channel.id} in guild {ctx.guild_id} "
        f"by user {ctx.user.id}"
    )

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand(
    "setup",
    "Quick setup — creates all 5 log channels automatically.",
)
async def setup(
    ctx: arc.GatewayContext,
    repo: LoggingRepository = arc.inject(),
) -> None:
    """
    Creates a '📋 Serenity Logs' category with five text channels:
    member-log, message-log, voice-log, server-log, mod-log.

    Permissions are set so only the bot can send messages.
    Admins can adjust visibility by editing the category permissions.
    """
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await ctx.defer()

    rest = ctx.client.app.rest
    guild_id = ctx.guild_id
    bot_user = ctx.client.app.get_me()

    # Build permission overwrites:
    # - @everyone: cannot send messages (read-only for humans)
    # - Bot: can send, view, read history, and embed links
    overwrites: list[hikari.PermissionOverwrite] = [
        hikari.PermissionOverwrite(
            id=guild_id,  # @everyone role has same ID as the guild
            type=hikari.PermissionOverwriteType.ROLE,
            deny=hikari.Permissions.SEND_MESSAGES | hikari.Permissions.VIEW_CHANNEL,
        ),
    ]
    if bot_user:
        overwrites.append(
            hikari.PermissionOverwrite(
                id=bot_user.id,
                type=hikari.PermissionOverwriteType.MEMBER,
                allow=(
                    hikari.Permissions.SEND_MESSAGES
                    | hikari.Permissions.VIEW_CHANNEL
                    | hikari.Permissions.READ_MESSAGE_HISTORY
                    | hikari.Permissions.EMBED_LINKS
                ),
            )
        )

    # Create category
    try:
        category = await rest.create_guild_category(
            guild_id,
            name="📋 Serenity Logs",
        )
    except hikari.ForbiddenError:
        await ctx.respond(
            "❌ I don't have permission to create channels. "
            "Please grant me **Manage Channels** and try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    except Exception as exc:
        logger.error(f"Failed to create log category in guild {guild_id}: {exc}", exc_info=True)
        await ctx.respond(
            "❌ An error occurred while creating the log category. Please try again.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    # Create each channel
    created: list[tuple[str, hikari.GuildTextChannel]] = []
    failed: list[str] = []

    for log_type, channel_name in CHANNEL_NAMES.items():
        try:
            channel = await rest.create_guild_text_channel(
                guild_id,
                name=channel_name,
                category=category,
                permission_overwrites=overwrites,
            )
            await repo.set_log_channel(guild_id, log_type, channel.id)
            created.append((log_type, channel))
        except Exception as exc:
            logger.error(
                f"Failed to create {channel_name} in guild {guild_id}: {exc}", exc_info=True
            )
            failed.append(channel_name)

    # Build response embed
    color = hikari.Color(0x57F287) if not failed else hikari.Color(0xFEE75C)
    embed = hikari.Embed(
        title="✅ Logging Setup Complete" if not failed else "⚠️ Logging Setup Partial",
        color=color,
    )

    if created:
        embed.add_field(
            "Channels Created",
            "\n".join(f"{LOG_TYPE_LABELS[lt]}: {ch.mention}" for lt, ch in created),
            inline=False,
        )

    if failed:
        embed.add_field(
            "Failed to Create",
            "\n".join(f"`{name}`" for name in failed),
            inline=False,
        )

    embed.add_field(
        "Next Steps",
        "Control who can **view** the log channels by editing the "
        "**📋 Serenity Logs** category permissions.\n"
        "Only the bot can send messages in these channels.",
        inline=False,
    )

    await ctx.respond(embed=embed)
    logger.info(
        f"Logging setup completed for guild {guild_id} by {ctx.user.id}. "
        f"Created: {len(created)}, Failed: {len(failed)}"
    )

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("view", "View the current logging configuration.")
async def view(
    ctx: arc.GatewayContext,
    repo: LoggingRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    configs = await repo.get_all_log_channels(ctx.guild_id)
    config_map = {c["log_type"]: c for c in configs}

    embed = hikari.Embed(
        title="📋 Logging Configuration",
        color=hikari.Color(0x5865F2),
    )

    for log_type in LOG_TYPES:
        label = LOG_TYPE_LABELS[log_type]
        config = config_map.get(log_type)

        if not config or not config["channel_id"]:
            value = "*Not configured*"
        else:
            status = "✅ Enabled" if config["is_enabled"] else "❌ Disabled"
            channel_mention = f"<#{config['channel_id']}>"
            ignored_ch = len(config.get("ignored_channels") or [])
            ignored_us = len(config.get("ignored_users") or [])
            ignore_parts = []
            if ignored_ch:
                ignore_parts.append(f"{ignored_ch} channel{'s' if ignored_ch != 1 else ''}")
            if ignored_us:
                ignore_parts.append(f"{ignored_us} user{'s' if ignored_us != 1 else ''}")
            ignore_str = f"\n*Ignoring: {', '.join(ignore_parts)}*" if ignore_parts else ""
            value = f"{status} → {channel_mention}{ignore_str}"

        embed.add_field(label, value, inline=True)

    embed.set_footer(
        text="Use /logging <type>-log <channel> to configure • /logging enable/disable to toggle"
    )
    await ctx.respond(embed=embed)

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("member-log", "Set the channel for member join/leave/update logs.")
async def member_log(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to send member events to"),
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    await _set_channel(ctx, repo, "member", channel)


@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("message-log", "Set the channel for message delete/edit logs.")
async def message_log(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to send message events to"),
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    await _set_channel(ctx, repo, "message", channel)

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("voice-log", "Set the channel for voice state logs.")
async def voice_log(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to send voice events to"),
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    await _set_channel(ctx, repo, "voice", channel)


@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("server-log", "Set the channel for server change logs.")
async def server_log(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to send server events to"),
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    await _set_channel(ctx, repo, "server", channel)

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("mod-log", "Set the channel for moderation action logs.")
async def mod_log(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to send moderation actions to"),
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    await _set_channel(ctx, repo, "mod", channel)

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("enable", "Enable a log type.")
async def enable_log(
    ctx: arc.GatewayContext,
    log_type: arc.Option[
        str, arc.StrParams("The log type to enable", choices=LOG_TYPES)
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    config = await repo.get_log_channel(ctx.guild_id, log_type)
    if not config or not config["channel_id"]:
        await ctx.respond(
            f"❌ No channel is configured for **{LOG_TYPE_LABELS[log_type]}** logs. "
            f"Use `/logging {log_type}-log <channel>` to set one first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await repo.enable_log_channel(ctx.guild_id, log_type)

    embed = hikari.Embed(
        title=f"✅ {LOG_TYPE_LABELS[log_type]} Log Enabled",
        description=f"Events will now be sent to <#{config['channel_id']}>.",
        color=hikari.Color(0x57F287),
    )
    await ctx.respond(embed=embed)

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("disable", "Disable a log type.")
async def disable_log(
    ctx: arc.GatewayContext,
    log_type: arc.Option[
        str, arc.StrParams("The log type to disable", choices=LOG_TYPES)
    ],
    repo: LoggingRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    await repo.disable_log_channel(ctx.guild_id, log_type)

    embed = hikari.Embed(
        title=f"❌ {LOG_TYPE_LABELS[log_type]} Log Disabled",
        description="Events of this type will no longer be logged.",
        color=hikari.Color(0xED4245),
    )
    await ctx.respond(embed=embed)

_LOG_TYPE_OR_ALL = ["all"] + LOG_TYPES


@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("ignore-channel", "Stop logging events from a specific channel.")
async def ignore_channel(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel,
        arc.ChannelParams("The channel to ignore"),
    ],
    log_type: arc.Option[
        str,
        arc.StrParams(
            "Which log type to apply to (default: all)",
            choices=_LOG_TYPE_OR_ALL,
        ),
    ] = "all",
    repo: LoggingRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    types_to_update = LOG_TYPES if log_type == "all" else [log_type]

    for lt in types_to_update:
        try:
            await repo.add_ignored_channel(ctx.guild_id, lt, channel.id)
        except Exception:
            # Log type may not be configured yet; skip silently
            pass

    scope = "all log types" if log_type == "all" else f"**{LOG_TYPE_LABELS[log_type]}** logs"
    embed = hikari.Embed(
        title="🔇 Channel Ignored",
        description=f"{channel.mention} will no longer appear in {scope}.",
        color=hikari.Color(0x5865F2),
    )
    await ctx.respond(embed=embed)

@logging_group.include
@arc.with_hook(require_module(ModuleType.LOGGING))
@arc.slash_subcommand("ignore-user", "Stop logging events involving a specific user.")
async def ignore_user(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User, arc.UserParams("The user to ignore")],
    log_type: arc.Option[
        str,
        arc.StrParams(
            "Which log type to apply to (default: all)",
            choices=_LOG_TYPE_OR_ALL,
        ),
    ] = "all",
    repo: LoggingRepository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    types_to_update = LOG_TYPES if log_type == "all" else [log_type]

    for lt in types_to_update:
        try:
            await repo.add_ignored_user(ctx.guild_id, lt, user.id)
        except Exception:
            pass

    scope = "all log types" if log_type == "all" else f"**{LOG_TYPE_LABELS[log_type]}** logs"
    embed = hikari.Embed(
        title="🔇 User Ignored",
        description=f"{user.mention} will no longer appear in {scope}.",
        color=hikari.Color(0x5865F2),
    )
    await ctx.respond(embed=embed)

@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Logging commands plugin loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Logging commands plugin unloaded.")
