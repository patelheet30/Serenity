import arc
import hikari

from serenity.database.repository import Repository
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("admin_commands", default_permissions=hikari.Permissions.MANAGE_CHANNELS)


@plugin.set_error_handler
async def on_command_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, SerenityPermissionError):
        await ctx.respond(
            f"❌ {str(exc)}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    raise exc


serenity = plugin.include_slash_group("serenity", "Serenity bot commands.")
guild = serenity.include_subgroup("guild", "Guild configuration commands.")


@guild.include
@arc.slash_subcommand("enable", "Enable Serenity in this guild.")
async def enable_serenity(ctx: arc.GatewayContext, repo: Repository = arc.inject()) -> None:
    if hikari.Permissions.MANAGE_GUILD not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError("You need the Manage Guild permission to enable Serenity.")

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    try:
        await repo.update_guild_config(ctx.guild_id, is_enabled=True)
        embed = hikari.Embed(
            title="✅ Serenity **Enabled**",
            description=(
                "Serenity has been enabled in this guild. Automatic slowmode will now be applied "
                "to channels based on activity."
            ),
            color=0x00FF00,
        )
        embed.add_field(
            name="Next Steps",
            value=(
                "Use `/serenity channel enable` in channels where you want automatic slowmode to "
                "be applied. You can also customise settings using other `/serenity` commands."
                "Channels will not have Serenity settings applied until they are enabled."
            ),
            inline=False,
        )
        await ctx.respond(embed=embed)
        logger.info(f"Serenity enabled in guild {ctx.guild_id} by user {ctx.user.id}")
    except Exception as e:
        logger.error(f"Failed to enable guild {ctx.guild_id}: {e}", exc_info=True)
        await ctx.respond(
            "❌ An error occurred while enabling automatic slowmode. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@guild.include
@arc.slash_subcommand("disable", "Disable Serenity in this guild.")
async def disable_serenity(ctx: arc.GatewayContext, repo: Repository = arc.inject()) -> None:
    if hikari.Permissions.MANAGE_GUILD not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError("You need the Manage Guild permission to disable Serenity.")

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    try:
        await repo.update_guild_config(ctx.guild_id, is_enabled=False)
        embed = hikari.Embed(
            title="✅ Serenity **Disabled**",
            description=(
                "Serenity has been disabled in this guild. Automatic slowmode will no longer be "
                "applied to channels."
            ),
            color=0xFF0000,
        )
        await ctx.respond(embed=embed)
        logger.info(f"Serenity disabled in guild {ctx.guild_id} by user {ctx.user.id}")
    except Exception as e:
        logger.error(f"Failed to disable guild {ctx.guild_id}: {e}", exc_info=True)
        await ctx.respond(
            "❌ An error occurred while disabling automatic slowmode. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@guild.include
@arc.slash_subcommand("threshold", "Set the default message threshold for this server")
async def set_threshold(
    ctx: arc.GatewayContext,
    threshold: arc.Option[
        int, arc.IntParams("Messages per minute before slowmode activates", min=1, max=100)
    ],
    repo: Repository = arc.inject(),
) -> None:
    if hikari.Permissions.MANAGE_GUILD not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError("You need the Manage Guild permission to set the threshold.")

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    try:
        await repo.update_guild_config(ctx.guild_id, default_threshold=threshold)
        embed = hikari.Embed(
            title="✅ Threshold Updated",
            description=(
                f"The default message threshold has been set to **{threshold}** messages/minute. "
                "Channels exceeding this rate may have slowmode applied."
            ),
            color=0x00FF00,
        )
        await ctx.respond(embed=embed)
        logger.info(f"Threshold set to {threshold} in guild {ctx.guild_id} by user {ctx.user.id}")
    except Exception as e:
        logger.error(f"Failed to set threshold in guild {ctx.guild_id}: {e}", exc_info=True)
        await ctx.respond(
            "❌ An error occurred while setting the threshold. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@guild.include
@arc.slash_subcommand("interval", "Set the update interval for slowmode adjustments.")
async def set_update_interval(
    ctx: arc.GatewayContext,
    interval: arc.Option[int, arc.IntParams("Interval in minutes", min=1, max=5)],
    repo: Repository = arc.inject(),
) -> None:
    if hikari.Permissions.MANAGE_GUILD not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError(
            "You need the Manage Guild permission to set the update interval."
        )

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    try:
        await repo.update_guild_config(ctx.guild_id, update_interval=(interval * 60))
        embed = hikari.Embed(
            title="✅ Update Interval Updated",
            description=(f"The slowmode update interval has been set to **{interval}** minutes."),
            color=0x00FF00,
        )
        await ctx.respond(embed=embed)
        logger.info(
            f"Update interval set to {interval} minutes in guild {ctx.guild_id} by user {ctx.user.id}"
        )
    except Exception as e:
        logger.error(f"Failed to set update interval in guild {ctx.guild_id}: {e}", exc_info=True)
        await ctx.respond(
            "❌ An error occurred while setting the update interval. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


channel = serenity.include_subgroup("channel", "Channel configuration commands.")


@channel.include
@arc.slash_subcommand("enable", "Enable automatic slowmode for this channel.")
async def enable_channel(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel | None, arc.ChannelParams("The channel to enable")
    ] = None,
    repo: Repository = arc.inject(),
) -> None:
    if hikari.Permissions.MANAGE_CHANNELS not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError(
            "You need the Manage Channels permission to enable a channel."
        )

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    target_channel = channel or ctx.channel
    target_channel_id = target_channel.id

    try:
        await repo.get_channel_config(target_channel_id, ctx.guild_id)
        await repo.update_channel_config(channel_id=target_channel_id, is_enabled=True)
        embed = hikari.Embed(
            title="✅ Channel Enabled",
            description=(f"Automatic slowmode has been **enabled** for {target_channel.mention}."),
            color=0x00FF00,
        )
        await ctx.respond(embed=embed)
        logger.info(
            f"Automatic slowmode enabled for channel {target_channel_id} in guild {ctx.guild_id} "
            f"by user {ctx.user.id}"
        )
    except Exception as e:
        logger.error(
            f"Failed to enable channel {target_channel_id} in guild {ctx.guild_id}: {e}",
            exc_info=True,
        )
        await ctx.respond(
            "❌ An error occurred while enabling automatic slowmode for this channel. "
            "Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@channel.include
@arc.slash_subcommand("disable", "Disable automatic slowmode for this channel.")
async def disable_channel(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel | None, arc.ChannelParams("The channel to disable")
    ] = None,
    repo: Repository = arc.inject(),
) -> None:
    if hikari.Permissions.MANAGE_CHANNELS not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError(
            "You need the Manage Channels permission to disable a channel."
        )

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    target_channel = channel or ctx.channel
    target_channel_id = target_channel.id

    try:
        await repo.get_channel_config(target_channel_id, ctx.guild_id)
        await repo.update_channel_config(channel_id=target_channel_id, is_enabled=False)
        embed = hikari.Embed(
            title="✅ Channel Disabled",
            description=(f"Automatic slowmode has been **disabled** for {target_channel.mention}."),
            color=0xFF0000,
        )
        await ctx.respond(embed=embed)
        logger.info(
            f"Automatic slowmode disabled for channel {target_channel_id} in guild {ctx.guild_id} "
            f"by user {ctx.user.id}"
        )
    except Exception as e:
        logger.error(
            f"Failed to disable channel {target_channel_id} in guild {ctx.guild_id}: {e}",
            exc_info=True,
        )
        await ctx.respond(
            "❌ An error occurred while disabling automatic slowmode for this channel. "
            "Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@channel.include
@arc.slash_subcommand("threshold", "Set the message threshold for this channel.")
async def set_channel_threshold(
    ctx: arc.GatewayContext,
    threshold: arc.Option[
        int,
        arc.IntParams(
            "Messages per minute before slowmode activates (0 to use server default)",
            min=0,
            max=100,
        ),
    ],
    channel: arc.Option[
        hikari.TextableGuildChannel | None,
        arc.ChannelParams("The channel to set the threshold for"),
    ] = None,
    repo: Repository = arc.inject(),
) -> None:
    if hikari.Permissions.MANAGE_CHANNELS not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError(
            "You need the Manage Channels permission to set the threshold for a channel."
        )

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    target_channel = channel or ctx.channel
    target_channel_id = target_channel.id

    try:
        threshold_value = threshold if threshold > 0 else None
        await repo.get_channel_config(target_channel_id, ctx.guild_id)
        await repo.update_channel_config(channel_id=target_channel_id, threshold=threshold_value)
        if threshold_value is not None:
            description = (
                f"The message threshold has been set to **{threshold_value}** messages/minute "
                f"for {target_channel.mention}."
            )
        else:
            description = (
                f"The message threshold for {target_channel.mention} has been reset to the "
                "server default."
            )
        embed = hikari.Embed(
            title="✅ Channel Threshold Updated",
            description=description,
            color=0x00FF00,
        )
        await ctx.respond(embed=embed)
        logger.info(
            f"Message threshold set to {threshold} in channel {target_channel_id} in guild "
            f"{ctx.guild_id} by user {ctx.user.id}"
        )
    except Exception as e:
        logger.error(
            f"Failed to set message threshold in channel {target_channel_id} in guild "
            f"{ctx.guild_id}: {e}",
            exc_info=True,
        )
        await ctx.respond(
            "❌ An error occurred while setting the message threshold for this channel. "
            "Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@serenity.include
@arc.slash_subcommand("config", "View the current configuration for this server")
async def view_config(
    ctx: arc.GatewayContext,
    repo: Repository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    try:
        guild_config = await repo.get_guild_config(ctx.guild_id)
        enabled_channels = await repo.get_enabled_channels(ctx.guild_id)

        status_emoji = "✅" if guild_config.is_enabled else "❌"
        status_text = "Enabled" if guild_config.is_enabled else "Disabled"

        embed = hikari.Embed(
            title="⚙️ Serenity Configuration",
            description=f"**Serenity is {status_text} {status_emoji}**",
            color=hikari.Color(0x5865F2) if guild_config.is_enabled else hikari.Color(0x99AAB5),
        )

        embed.add_field(
            name="📊 Default Settings",
            value=(
                f"**Threshold:** {guild_config.default_threshold} messages/minute\n"
                f"**Check Interval:** {guild_config.update_interval} seconds"
            ),
            inline=False,
        )
        embed.add_field(
            name="📝 Enabled Channels",
            value=f"{len(enabled_channels)} channels"
            if enabled_channels
            else "No channels enabled",
            inline=False,
        )

        embed.set_footer(text="Use /serenity to configure settings")
        await ctx.respond(embed=embed)
    except Exception as e:
        logger.error(
            f"Failed to retrieve configuration for guild {ctx.guild_id}: {e}", exc_info=True
        )
        await ctx.respond(
            "❌ An error occurred while retrieving the configuration. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@serenity.include
@arc.slash_subcommand("channel-info", "View configuration for a specific channel")
async def channel_config(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel | None,
        arc.ChannelParams("The channel to view the configuration for"),
    ] = None,
    repo: Repository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a guild.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    target_channel = channel or ctx.channel
    target_channel_id = target_channel.id

    try:
        channel_config = await repo.get_channel_config(target_channel_id, ctx.guild_id)
        guild_config = await repo.get_guild_config(ctx.guild_id)

        status_emoji = "✅" if channel_config.is_enabled else "❌"
        status_text = "Enabled" if channel_config.is_enabled else "Disabled"
        threshold_value = (
            channel_config.threshold
            if channel_config.threshold is not None
            else guild_config.default_threshold
        )

        embed = hikari.Embed(
            title=f"⚙️ Configuration for {target_channel.name}",
            description=f"**Automatic Slowmode is {status_text} {status_emoji}**",
            color=hikari.Color(0x5865F2) if channel_config.is_enabled else hikari.Color(0x99AAB5),
        )

        embed.add_field(
            name="📊 Channel Settings",
            value=(
                f"**Threshold:** {threshold_value} messages/minute\n"
                f"(Channel-specific threshold: "
                f"{channel_config.threshold if channel_config.threshold is not None else 'None'})"
            ),
            inline=False,
        )

        embed.set_footer(text="Use /serenity channel to configure settings")
        await ctx.respond(embed=embed)
    except Exception as e:
        logger.error(
            f"Failed to retrieve configuration for channel {target_channel_id} in guild "
            f"{ctx.guild_id}: {e}",
            exc_info=True,
        )
        await ctx.respond(
            "❌ An error occurred while retrieving the channel configuration. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@arc.loader
def loader(client: arc.GatewayClient) -> None:
    """Load the admin commands plugin"""
    client.add_plugin(plugin)
    logger.info("Admin commands plugin loaded")


@arc.unloader
def unloader(client: arc.GatewayClient) -> None:
    """Unload the admin commands plugin"""
    client.remove_plugin(plugin)
    logger.info("Admin commands plugin unloaded")
