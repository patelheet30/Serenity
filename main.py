import asyncio
import logging
import os
from pathlib import Path

import arc
import hikari
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("serenity")

if os.name != "nt":
    try:
        import uvloop

        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("Using uvloop for improved performance.")
    except ImportError:
        logger.info("uvloop is not installed; using default asyncio event loop.")

bot = hikari.GatewayBot(
    token=os.environ["TOKEN"],
    intents=hikari.Intents.GUILD_MESSAGES | hikari.Intents.GUILDS,
)

client = arc.GatewayClient(bot)


@client.add_startup_hook
async def on_startup(_: arc.GatewayClient) -> None:
    """Called when the bot starts up."""
    logger.info(f"Serenity is starting up... {bot.get_me()}")
    logger.info("Bot if ready!")


@client.add_shutdown_hook
async def on_shutdown(_: arc.GatewayClient) -> None:
    """Called when the bot is shutting down."""
    logger.info("Serenity is shutting down...")


if __name__ == "__main__":
    logger.info("Starting Serenity bot...")
    bot.run()
