from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from serenity.utils.logging import get_logger

logger = get_logger(__name__)


async def _metrics_handler(_: web.Request) -> web.Response:
    return web.Response(
        body=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


async def _health_handler(_: web.Request) -> web.Response:
    return web.Response(text="OK")


class MetricsServer:
    def __init__(self, port: int = 8080) -> None:
        self.port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/metrics", _metrics_handler)
        app.router.add_get("/health", _health_handler)
        app.router.add_get("/", _health_handler)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

        logger.info(f"Metrics server started on port {self.port}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            logger.info("Metrics server stopped")
