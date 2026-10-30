import asyncio
import logging
import os
import sys

os.environ["PROMETHEUS_DISABLE_CREATED_SERIES"] = "true"

import arc
import hikari
import toolbox
from dotenv import load_dotenv

from serenity.core.modules import ModuleManager
from serenity.dashboard.api import build_api
from serenity.database.import_sqlite import cutover_if_needed
from serenity.database.logging_repository import LoggingRepository
from serenity.database.moderation_repository import ModerationRepository
from serenity.database.repository import Repository
from serenity.database.ticket_repository import TicketRepository
from serenity.services.audit_actors import AuditActors
from serenity.services.logging_service import LoggingService
from serenity.services.metrics import BOT_INFO, COMMANDS_USED
from serenity.services.metrics_server import MetricsServer
from serenity.services.moderation_service import ModerationService
from serenity.services.slowmode_engine import SlowmodeEngine
from serenity.services.ticket_service import TicketService

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
    intents= hikari.Intents.GUILD_MESSAGES
    | hikari.Intents.GUILDS
    | hikari.Intents.GUILD_MEMBERS          # Member join/leave/update  (privileged)
    | hikari.Intents.GUILD_VOICE_STATES     # Voice logging
    | hikari.Intents.GUILD_EMOJIS           # Emoji logging
    | hikari.Intents.MESSAGE_CONTENT        # Message content for edit/delete logs (privileged)
    | hikari.Intents.GUILD_MODERATION       # Bans and audit log entries (external cases)
)

client = arc.GatewayClient(bot)


@client.add_post_hook
async def count_command(ctx: arc.GatewayContext) -> None:
    """Count every slash command use (failed ones too) to see which features get used."""
    COMMANDS_USED.labels(command=" ".join(ctx.command.qualified_name)).inc()


# Connection settings come from PGHOST/PGUSER/PGPASSWORD/PGDATABASE
repo = Repository(pool_size=int(os.getenv("DB_POOL_SIZE", "5")))
engine = SlowmodeEngine(repo)
module_manager = ModuleManager(repo)
metrics_server = MetricsServer(port=int(os.getenv("METRICS_PORT", "8080")))


def can_view_audit_log(guild_id: int) -> bool:
    """Whether Discord will send this guild's audit log entries (needs View Audit Log)."""
    me = bot.get_me()
    member = me and bot.cache.get_member(guild_id, me.id)
    try:
        return bool(member and toolbox.calculate_permissions(member) & hikari.Permissions.VIEW_AUDIT_LOG)
    except Exception:  # the guild's roles aren't cached yet
        return False


@client.add_startup_hook
async def on_startup(_: arc.GatewayClient) -> None:
    """Called when the bot starts up."""
    await repo.init()

    logging_repo = LoggingRepository(repo.pool)
    audit = AuditActors(can_view_audit_log)
    logging_svc = LoggingService(repo=logging_repo, rest=bot.rest, audit=audit)
    mod_repo = ModerationRepository(repo.pool)
    mod_svc = ModerationService(
        mod_repo=mod_repo,
        logging_svc=logging_svc,
        rest=bot.rest,
    )
    ticket_repo = TicketRepository(repo.pool)
    ticket_svc = TicketService(
        repo=ticket_repo,
        rest=bot.rest,
        cache=bot.cache,
        modules=module_manager,
        logging=logging_svc,
    )
    await ticket_svc.load()

    client.set_type_dependency(Repository, repo)
    client.set_type_dependency(SlowmodeEngine, engine)
    client.set_type_dependency(ModuleManager, module_manager)
    client.set_type_dependency(LoggingRepository, logging_repo)
    client.set_type_dependency(LoggingService, logging_svc)
    client.set_type_dependency(AuditActors, audit)
    client.set_type_dependency(ModerationRepository, mod_repo)
    client.set_type_dependency(ModerationService, mod_svc)
    client.set_type_dependency(TicketRepository, ticket_repo)
    client.set_type_dependency(TicketService, ticket_svc)

    await metrics_server.start(api=build_api(bot, repo, ticket_svc))

    BOT_INFO.info({"version": "1.0.0", "environment": os.getenv("ENVIRONMENT", "local")})

    logger.info("Database initialized and dependencies set.")

    logger.info(f"Serenity is starting up... {bot.get_me()}")
    logger.info("Bot if ready!")


@client.add_shutdown_hook
async def on_shutdown(_: arc.GatewayClient) -> None:
    """Called when the bot is shutting down."""
    await metrics_server.stop()
    await repo.close()
    logger.info("Serenity is shutting down...")


if __name__ == "__main__":
    logger.info("Starting Serenity bot...")

    # One-off SQLite -> Postgres cutover (#16): runs before connecting to Discord, so nothing
    # writes mid-import. A no-op once the old file has been renamed to *.imported.
    try:
        cutover_if_needed(os.getenv("DATABASE_PATH"))
    except Exception:
        logger.exception(
            "SQLite -> Postgres cutover failed; not starting. The import is all-or-nothing "
            "and the SQLite file is untouched. If Postgres already holds this data on purpose, "
            "rename the SQLite file to *.imported."
        )
        sys.exit(1)

    client.load_extensions_from("serenity/extensions")

    bot.run()
