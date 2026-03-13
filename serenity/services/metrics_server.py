import base64
import os
import secrets

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from serenity.utils.logging import get_logger

logger = get_logger(__name__)

_METRICS_USERNAME = os.getenv("METRICS_USERNAME", "")
_METRICS_PASSWORD = os.getenv("METRICS_PASSWORD", "")
_AUTH_ENABLED = bool(_METRICS_USERNAME and _METRICS_PASSWORD)


def _check_basic_auth(request: web.Request) -> bool:
    """Validate the Authorization header against configured credentials."""
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False

    # Use constant-time comparison to prevent timing attacks
    return secrets.compare_digest(username, _METRICS_USERNAME) and secrets.compare_digest(
        password, _METRICS_PASSWORD
    )


async def _metrics_handler(request: web.Request) -> web.Response:
    """Serve Prometheus metrics (behind basic auth in production)."""
    if _AUTH_ENABLED and not _check_basic_auth(request):
        return web.Response(
            status=401,
            text="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="metrics"'},
        )

    return web.Response(
        body=generate_latest(),
        headers={"Content-Type": CONTENT_TYPE_LATEST},
    )


async def _health_handler(_: web.Request) -> web.Response:
    """Simple health check endpoint (no auth required)."""
    return web.Response(text="OK")


class MetricsServer:
    """Manages the HTTP server lifecycle for metrics exposition."""

    def __init__(self, port: int = 8080) -> None:
        self.port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Start the metrics HTTP server."""
        app = web.Application()
        app.router.add_get("/metrics", _metrics_handler)
        app.router.add_get("/health", _health_handler)
        app.router.add_get("/", _health_handler)

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

        auth_status = "enabled" if _AUTH_ENABLED else "disabled (no credentials set)"
        logger.info(f"Metrics server started on port {self.port} (auth: {auth_status})")

    async def stop(self) -> None:
        """Stop the metrics HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Metrics server stopped.")
