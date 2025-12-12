import arc
import hikari

from serenity.database.repository import Repository
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("user_commands")


@plugin.include
@arc.slash_command("ping", "Check bot latency and status")
async def ping(ctx: arc.GatewayContext) -> None:
    heartbeat_latency = ctx.client.app.heartbeat_latency

    response = await ctx.respond(
        "🏓 Pinging...",
        flags=hikari.MessageFlag.EPHEMERAL,
    )

    embed = hikari.Embed(
        title="🏓 Pong!",
        description="Bot latency information",
        color=hikari.Color(0x5865F2),
    )

    embed.add_field(
        name="Heartbeat Latency",
        value=f"{heartbeat_latency:.2f} ms",
        inline=False,
    )

    if heartbeat_latency < 100:
        status = "🟢 Excellent"
    elif heartbeat_latency < 200:
        status = "🟡 Good"
    else:
        status = "🔴 Poor"

    embed.add_field(
        name="Connection Status",
        value=status,
        inline=False,
    )

    await response.edit(embed=embed)


@plugin.include
@arc.slash_command("stats", "View slowmode statistics for a channel")
async def stats(
    ctx: arc.GatewayContext,
    channel: arc.Option[
        hikari.TextableGuildChannel | None,
        arc.ChannelParams("The channel to view stats for (defaults to current channel)"),
    ] = None,
    repo: Repository = arc.inject(),
) -> None:
    if not ctx.guild_id:
        await ctx.respond(
            "This command can only be used in a server.", flags=hikari.MessageFlag.EPHEMERAL
        )
        return

    target_channel = channel or ctx.channel
    target_channel_id = target_channel.id

    try:
        channel_config = await repo.get_channel_config(target_channel_id, ctx.guild_id)
        guild_config = await repo.get_guild_config(ctx.guild_id)

        rate_1m = await repo.get_message_rate(target_channel_id, 60)
        rate_5m = await repo.get_message_rate(target_channel_id, 300)
        rate_15m = await repo.get_message_rate(target_channel_id, 900)

        threshold = channel_config.threshold or guild_config.default_threshold

        if not channel_config.is_enabled:
            status = "⚪ Disabled"
            status_color = hikari.Color(0x99AAB5)
        elif rate_1m < threshold * 0.5:
            status = "🟢 Low Activity"
            status_color = hikari.Color(0x43B581)
        elif rate_1m < threshold:
            status = "🟡 Moderate Activity"
            status_color = hikari.Color(0xFAA61A)
        else:
            status = "🔴 High Activity"
            status_color = hikari.Color(0xF04747)

        embed = hikari.Embed(
            title=f"📊 Channel Slowmode Statistics: {target_channel.mention}",
            description=f"**Status:** {status}",
            color=status_color,
        )

        embed.add_field(
            name="📈 Current Activity",
            value=(
                f"1 Minute: {rate_1m:.2f} msgs/min\n"
                f"5 Minutes: {rate_5m:.2f} msgs/min\n"
                f"15 Minutes: {rate_15m:.2f} msgs/min\n"
            ),
            inline=True,
        )

        embed.add_field(
            name="⚙️ Configuration",
            value=(
                f"**Threshold:** {threshold} msg/min\n"
                f"**Enabled:** {'Yes' if channel_config.is_enabled else 'No'}\n"
            ),
            inline=True,
        )

        try:
            discord_channel = ctx.client.app.cache.get_guild_channel(target_channel_id)
            if discord_channel and isinstance(discord_channel, hikari.GuildTextChannel):
                current_slowmode = int(discord_channel.rate_limit_per_user.total_seconds())

                embed.add_field(
                    name="⏱️ Current Slowmode",
                    value=f"{current_slowmode} seconds" if current_slowmode > 0 else "Disabled",
                    inline=False,
                )
        except Exception as e:
            logger.error(f"Failed to fetch current slowmode for channel {target_channel_id}: {e}")

        if rate_1m > threshold and channel_config.is_enabled:
            embed.set_footer(text="⚠️ Activity is above threshold - slowmode may be adjusted soon")
        else:
            embed.set_footer(text="Use /serenity channel-info for more configuration details")

        await ctx.respond(embed=embed)
    except Exception as e:
        logger.error(
            f"Failed to get stats for channel {target_channel_id} in guild {ctx.guild_id}: {e}",
            exc_info=True,
        )
        await ctx.respond(
            "❌ An error occurred while retrieving channel statistics. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@plugin.include
@arc.slash_command("about", "Learn about Serenity")
async def about(ctx: arc.GatewayContext) -> None:
    """Display information about the bot"""
    embed = hikari.Embed(
        title="🌙 Serenity",
        description="An intelligent Discord bot that automatically manages slowmode based on channel activity.",
        color=hikari.Color(0x5865F2),
    )

    embed.add_field(
        name="✨ Features",
        value=(
            "• **Smart Detection** - Multi-factor analysis of message patterns\n"
            "• **Automatic Adjustment** - Slowmode adapts to channel activity\n"
            "• **Customizable** - Per-channel and server-wide settings\n"
            "• **Historical Learning** - Adapts based on typical channel patterns"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔧 Getting Started",
        value=(
            "Admins can use `/serenity guild enable` to enable Serenity\n"
            "Then use `/serenity channel enable` in specific channels\n"
            "Customise with `/serenity guild threshold` and other commands"
        ),
        inline=False,
    )

    embed.add_field(
        name="📚 Commands",
        value=(
            "`/ping` - Check bot latency\n"
            "`/stats` - View channel statistics\n"
            "`/serenity {guild | channel}` - Configuration commands for channels and guilds (servers) (admin only)"
        ),
        inline=False,
    )
    
    embed.add_field(
        name="UPDATES",
        value="We're currently working on new features and improvements! Stay tuned on our [Discord Server](https://discord.gg/SSdKyxwTZu) for updates.",
    )
    
    embed.add_field(
        name="** **",
        value="** **",
    )
    
    embed.add_field(
        name="Support Server",
        value="[Join Serenity Server](https://discord.gg/SSdKyxwTZu)",
    )

    embed.set_footer(text="Built with Hikari & Hikari-Arc | Developed by patelheet30")

    await ctx.respond(embed=embed)


@arc.loader
def loader(client: arc.GatewayClient) -> None:
    """Load the user commands plugin"""
    client.add_plugin(plugin)
    logger.info("User commands plugin loaded")


@arc.unloader
def unloader(client: arc.GatewayClient) -> None:
    """Unload the user commands plugin"""
    client.remove_plugin(plugin)
    logger.info("User commands plugin unloaded")
