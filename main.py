import asyncio
import logging
import os

import arc
import hikari
from dotenv import load_dotenv

from serenity.database.repository import Repository
from serenity.services.slowmode_engine import SlowmodeEngine

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
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

db_path = os.getenv("DATABASE_PATH", "data/serenity.db")
repo = Repository(db_path=db_path)
engine = SlowmodeEngine(repo)


@client.add_startup_hook
async def on_startup(_: arc.GatewayClient) -> None:
    """Called when the bot starts up."""
    await repo.init()

    client.set_type_dependency(Repository, repo)
    client.set_type_dependency(SlowmodeEngine, engine)

    logger.info("Database initialized and dependencies set.")

    logger.info(f"Serenity is starting up... {bot.get_me()}")
    logger.info("Bot if ready!")


@client.add_shutdown_hook
async def on_shutdown(_: arc.GatewayClient) -> None:
    """Called when the bot is shutting down."""
    await repo.close()
    logger.info("Serenity is shutting down...")


if __name__ == "__main__":
    logger.info("Starting Serenity bot...")

    client.load_extensions_from("serenity/extensions")

    bot.run()
