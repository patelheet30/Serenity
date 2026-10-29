"""Outgoing ticket webhooks: ticket events POSTed, signed, to a server's own endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
from typing import Optional
from urllib.parse import urlsplit

import aiohttp
from aiohttp.resolver import ThreadedResolver

from serenity.database.ticket_repository import TicketRepository
from serenity.utils.errors import SerenityError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

# What a webhook can subscribe to: the kind TicketService.log() sends -> its public event name.
EVENT_NAMES = {
    kind: f"ticket.{name}"
    for kind, name in {
        "opened": "opened",
        "claimed": "claimed",
        "assigned": "assigned",
        "unclaimed": "unclaimed",
        "stage": "stage_changed",  # sent by stages (#125)
        "transferred": "transferred",
        "closed": "closed",
        "reopened": "reopened",
        "deletion_requested": "deletion_requested",
    }.items()
}
EVENTS = tuple(EVENT_NAMES.values())
MAX_WEBHOOKS = 5  # per server
DELIVERY_RETENTION = 7 * 86400
# Seconds to wait before each attempt. ponytail: retries live in memory, so a restart drops
# pending ones; a queue table if that ever matters.
RETRY_DELAYS = (0, 10, 60, 300, 1800)
TIMEOUT = aiohttp.ClientTimeout(total=10)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class WebhookError(SerenityError):
    """A webhook that can't be saved or sent to; the message is shown to the admin as is."""


def event_name(kind: str) -> str:
    # A kind added later still reaches the webhooks subscribed to every event.
    return EVENT_NAMES.get(kind, f"ticket.{kind}")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The X-Serenity-Signature header: HMAC-SHA256 of "<timestamp>.<body>", keyed with the
    webhook's secret. Receivers recompute it, compare in constant time and check the timestamp."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def require_global(address: str) -> None:
    """Refuse anything but a public address: private, loopback, link-local, multicast, reserved,
    and IPv6 forms wrapping one of those. Raises ValueError if `address` isn't an IP at all."""
    ip = ipaddress.ip_address(address.split("%")[0])  # drop an IPv6 zone
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        candidates += [a for a in (ip.ipv4_mapped, ip.sixtofour) if a]
        if ip in _NAT64:
            candidates.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if any(not a.is_global or a.is_multicast for a in candidates):
        raise WebhookError("Webhooks can't point at private or internal addresses.")


class GlobalResolver(ThreadedResolver):
    """aiohttp's resolver, refusing non-global addresses. The connection uses the addresses
    checked here, so a DNS answer can't change between the check and the connect."""

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET):  # type: ignore[override]
        hosts = await super().resolve(host, port, family)
        for h in hosts:
            require_global(h["host"])
        return hosts


async def check_url(url: str, *, resolve: bool = True) -> None:
    """Refuse a webhook URL that isn't https or that reaches a non-global address.

    Deliveries pass resolve=False: their connector's GlobalResolver checks the names, and
    aiohttp skips the resolver for literal IPs, which are checked here.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise WebhookError("Webhook URLs must start with https://.")
    if parts.username or parts.password:
        raise WebhookError("Webhook URLs can't contain a username or password.")
    try:
        port = parts.port or 443
    except ValueError:
        raise WebhookError("That URL's port isn't valid.") from None
    try:
        require_global(parts.hostname)
        return
    except ValueError:  # a name, not an address
        pass
    if resolve:
        try:
            await GlobalResolver().resolve(parts.hostname, port, socket.AF_UNSPEC)
        except OSError:
            raise WebhookError(f"I couldn't resolve **{parts.hostname}**.") from None


def payload(event: str, ticket: dict, ticket_type: dict, actor_id: Optional[int], detail: Optional[str]) -> bytes:
    def snowflake(value: Optional[int]) -> Optional[str]:  # strings, like Discord's API, for JS
        return None if value is None else str(value)

    return json.dumps(
        {
            "event": event,
            "timestamp": int(time.time()),
            "guild_id": snowflake(ticket["guild_id"]),
            "actor_id": snowflake(actor_id),
            "detail": detail,
            "ticket": {
                "id": ticket["id"],
                "number": ticket["number"],
                "status": ticket["status"],
                "priority": ticket["priority"],
                "stage": ticket.get("stage"),
                "opener_id": snowflake(ticket["opener_id"]),
                "assignee_id": snowflake(ticket.get("assignee_id")),
                "channel_id": snowflake(ticket.get("channel_id")),
                "opened_at": ticket["opened_at"],
                "closed_at": ticket.get("closed_at"),
                "close_reason": ticket.get("close_reason"),
                "fields": ticket.get("fields") or {},
            },
            "type": {"id": ticket_type["id"], "name": ticket_type["name"]},
        }
    ).encode()


class TicketWebhooks:
    """Sends ticket events to the webhooks subscribed to them. Secrets are never logged."""

    def __init__(self, repo: TicketRepository) -> None:
        self.repo = repo
        self._tasks: set[asyncio.Task] = set()  # running deliveries, so they aren't GC'd

    async def add(self, guild_id: int, url: str, events: list[str], type_id: Optional[int]) -> dict:
        if len(await self.repo.list_webhooks(guild_id)) >= MAX_WEBHOOKS:
            raise WebhookError(f"A server can have at most {MAX_WEBHOOKS} webhooks.")
        await check_url(url)
        return await self.repo.add_webhook(guild_id, url, secrets.token_hex(32), events, type_id)

    def dispatch(
        self, ticket: dict, ticket_type: dict, kind: str, actor_id: Optional[int], detail: Optional[str]
    ) -> None:
        """Send an event in the background, so ticket actions never wait on a receiver."""
        task = asyncio.create_task(self._dispatch(ticket, ticket_type, kind, actor_id, detail))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _dispatch(
        self, ticket: dict, ticket_type: dict, kind: str, actor_id: Optional[int], detail: Optional[str]
    ) -> None:
        event = event_name(kind)
        try:
            hooks = await self.repo.webhooks_for(ticket["guild_id"], event, ticket_type["id"])
            if hooks:
                body = payload(event, ticket, ticket_type, actor_id, detail)
                await asyncio.gather(*(self.deliver(h, event, body) for h in hooks))
        except Exception:
            logger.exception(f"Ticket webhooks for {event} in guild {ticket['guild_id']} failed")

    async def deliver(
        self, hook: dict, event: str, body: bytes, delays: tuple[int, ...] = RETRY_DELAYS
    ) -> Optional[str]:
        """POST `body` until it's accepted or the retries run out, recording each attempt in the
        delivery log. Returns the last error, or None once delivered."""
        delivery_id = await self.repo.add_delivery(hook["id"], event)
        error: Optional[str] = None
        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)
            # Removing a webhook stops its pending retries too.
            if attempt > 1 and await self.repo.get_webhook(hook["guild_id"], hook["id"]) is None:
                return "Webhook was removed."
            status, latency, error = await self.post(hook, event, delivery_id, body)
            await self.repo.update_delivery(delivery_id, attempt, status, latency, error)
            if error is None:
                break
        return error

    async def post(
        self, hook: dict, event: str, delivery_id: int, body: bytes
    ) -> tuple[Optional[int], Optional[int], Optional[str]]:
        """One attempt: (HTTP status, latency in ms, error)."""
        timestamp = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Serenity-Webhooks/1.0",
            "X-Serenity-Event": event,
            "X-Serenity-Delivery": str(delivery_id),
            "X-Serenity-Timestamp": str(timestamp),
            "X-Serenity-Signature": sign(hook["secret"], timestamp, body),
        }
        start = time.monotonic()
        try:
            await check_url(hook["url"], resolve=False)
            connector = aiohttp.TCPConnector(resolver=GlobalResolver())
            async with aiohttp.ClientSession(connector=connector, timeout=TIMEOUT) as http:
                # No redirects: a redirect could point anywhere, private addresses included.
                async with http.post(hook["url"], data=body, headers=headers, allow_redirects=False) as resp:
                    status = resp.status
        except WebhookError as exc:
            return None, None, str(exc)
        except Exception as exc:  # the exception, never the request: its headers hold the signature
            return None, None, (str(exc) or type(exc).__name__)[:200]
        latency = int((time.monotonic() - start) * 1000)
        return status, latency, None if 200 <= status < 300 else f"HTTP {status}"

    async def send_test(self, hook: dict) -> Optional[str]:
        """One attempt of a `ticket.test` event; the error, or None if it was accepted."""
        body = json.dumps(
            {"event": "ticket.test", "timestamp": int(time.time()), "guild_id": str(hook["guild_id"])}
        ).encode()
        return await self.deliver(hook, "ticket.test", body, delays=(0,))
