import asyncio
import logging
import os

import arc
import hikari
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("bot")

if os.name != "nt":
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

bot = hikari.GatewayBot(
    token=os.environ["TOKEN"],
    intents=hikari.Intents.GUILD_MESSAGES,
    # logs="TRACE_HIKARI",
)

client = arc.GatewayClient(bot)


@client.add_startup_hook
async def startup(_: arc.GatewayClient) -> None:
    logger.info(f"Bot is starting up... {bot.get_me()}")
    logger.info(f"Connected to {len(bot.cache.get_unavailable_guilds_view())} guilds")


@client.include
@arc.slash_command("ping", "Check the bot's latency")
async def ping(ctx: arc.Context) -> None:
    await ctx.respond(f"Pong! {bot.heartbeat_latency * 1000:.2f} ms")


@client.include
@arc.slash_command("help", "Get help with the bot commands")
async def help_command(ctx: arc.Context) -> None:
    embed = hikari.Embed(
        title="Help",
        description="Here are the available commands:",
    )
    embed.add_field(
        name="auto-slowmode server enable/disable",
        value="Enable or disable auto slowmode for the entire server.",
    )
    embed.add_field(
        name="auto-slowmode channel enable/disable",
        value="Enable or disable auto slowmode for a specific channel.",
    )
    embed.add_field(
        name="auto-slowmode server threshold",
        value="Set the message threshold for auto slowmode across the entire server. A default threshold of 10 messages per minute is set.",
    )
    embed.add_field(
        name="auto-slowmode channel threshold",
        value="Set the message threshold for auto slowmode in a specific channel. A default threshold of 10 messages per minute is set.",
    )
    embed.add_field(
        name="auto-slowmode stats",
        value="Get the current auto slowmode statistics for a specific channel.",
    )
    embed.add_field(
        name="** **",
        value="** **",
    )
    embed.add_field(
        name="** **",
        value="** **",
    )
    embed.add_field(
        name="Support Server",
        value="[Join Serenity Server](https://discord.gg/SSdKyxwTZu)",
    )
    embed.set_thumbnail(
        "https://cdn.discordapp.com/avatars/1359250509009260604/b0ea8243f79c6120df3352ff4593c59a.webp"
    )
    await ctx.respond(embed=embed)


client.load_extensions_from("extensions")

if __name__ == "__main__":
    bot.run()
