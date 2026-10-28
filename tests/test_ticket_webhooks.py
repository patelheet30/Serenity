"""Ticket webhooks: signatures, SSRF checks and retries, without the network or Postgres."""

import hashlib
import hmac
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.resolver import ThreadedResolver

from serenity.extensions.tickets import parse_events
from serenity.services.ticket_service import TicketError
from serenity.services.ticket_webhooks import (
    GlobalResolver,
    TicketWebhooks,
    WebhookError,
    check_url,
    event_name,
    sign,
)


def test_receiver_can_verify_signature():
    body, secret, ts = b'{"event":"ticket.opened"}', "s3cret", 1_700_000_000
    header = sign(secret, ts, body)
    # What a receiver does with the X-Serenity-Timestamp header and the raw body.
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(header, f"sha256={expected}")
    assert sign(secret, ts, body + b" ") != header
    assert sign("other", ts, body) != header


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",  # not https
        "https://127.0.0.1/hook",
        "https://10.0.0.5/hook",
        "https://192.168.1.1:8443/hook",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://100.64.0.1/hook",  # carrier-grade NAT
        "https://0.0.0.0/hook",
        "https://[::1]/hook",
        "https://[fe80::1]/hook",
        "https://[fc00::1]/hook",
        "https://[::ffff:127.0.0.1]/hook",
        "https://[64:ff9b::a00:1]/hook",  # NAT64 of 10.0.0.1
        "https://[2002:7f00:1::]/hook",  # 6to4 of 127.0.0.1
        "https://localhost/hook",  # a name resolving to loopback
        "https://user:pass@example.com/hook",
    ],
)
async def test_private_and_non_https_urls_are_refused(url):
    with pytest.raises(WebhookError):
        await check_url(url)


@pytest.mark.asyncio
async def test_public_address_is_allowed():
    await check_url("https://1.1.1.1/hook")
    await check_url("https://[2606:4700:4700::1111]:8443/hook")


@pytest.mark.asyncio
async def test_resolver_refuses_a_name_pointing_inside():
    """Checked at delivery too: a name that re-resolves to a private address is refused."""
    answers = [{"hostname": "evil.example", "host": "93.184.216.34"}, {"hostname": "evil.example", "host": "10.0.0.1"}]
    with patch.object(ThreadedResolver, "resolve", AsyncMock(return_value=answers)):
        with pytest.raises(WebhookError):
            await GlobalResolver().resolve("evil.example", 443)
        with pytest.raises(WebhookError):
            await check_url("https://evil.example/hook")


@pytest.mark.asyncio
async def test_delivery_to_private_address_is_refused_without_connecting():
    webhooks = TicketWebhooks(AsyncMock())
    hook = {"id": 1, "url": "https://127.0.0.1/hook", "secret": "x"}
    with patch("aiohttp.ClientSession") as session:
        status, latency, error = await webhooks.post(hook, "ticket.opened", 1, b"{}")
    assert (status, latency) == (None, None)
    assert "private" in error
    session.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_retries_with_backoff_and_logs_each_attempt():
    repo = AsyncMock()
    repo.add_delivery.return_value = 7
    webhooks = TicketWebhooks(repo)
    webhooks.post = AsyncMock(
        side_effect=[(None, None, "timeout"), (500, 30, "HTTP 500"), (200, 25, None)]
    )
    with patch("asyncio.sleep", AsyncMock()) as sleep:
        error = await webhooks.deliver({"id": 3}, "ticket.closed", b"{}", delays=(0, 10, 60, 300))
    assert error is None
    assert [c.args[0] for c in sleep.call_args_list] == [0, 10, 60]  # stopped once accepted
    assert [c.args for c in repo.update_delivery.call_args_list] == [
        (7, 1, None, None, "timeout"),
        (7, 2, 500, 30, "HTTP 500"),
        (7, 3, 200, 25, None),
    ]


def test_events():
    assert event_name("stage") == "ticket.stage_changed"
    assert parse_events(None) == []
    assert parse_events("opened, ticket.closed opened") == ["ticket.opened", "ticket.closed"]
    with pytest.raises(TicketError):
        parse_events("exploded")
