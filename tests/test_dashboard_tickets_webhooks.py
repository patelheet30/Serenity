"""Dashboard ticket webhooks (#77): the /ticket webhook rules over the API, and the delivery log."""

import hashlib
import hmac
import logging

import aiohttp
import pytest

from serenity.services.ticket_webhooks import MAX_WEBHOOKS
from tests.test_dashboard_api import GUILD, client_for, settings, ticket_guild  # noqa: F401

URL = "https://1.1.1.1/hook"  # a public literal address, so nothing is resolved


@pytest.fixture
def receiver(monkeypatch):
    """Stands in for the receiving server: answers each POST with `status` and keeps the request."""

    class Response:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    sent = []

    def post(session, url, *, data, headers, allow_redirects):
        sent.append({"url": url, "body": data, "headers": headers})
        return Response()

    monkeypatch.setattr(aiohttp.ClientSession, "post", post)
    post.sent, post.response = sent, Response
    return post


@pytest.mark.asyncio
async def test_webhook_crud_follows_the_command_rules(settings, ticket_guild, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    support = (await ticket_guild.repo.list_types(GUILD))[0]

    status, hook = await settings(
        "POST", "/tickets/webhooks", {"url": URL, "events": ["ticket.opened"], "type_id": support["id"]}
    )
    assert status == 201
    secret = hook["secret"]
    assert len(secret) == 64
    assert (hook["url"], hook["events"], hook["type_id"]) == (URL, ["ticket.opened"], support["id"])

    status, body = await settings("GET", "/tickets/webhooks")
    assert status == 200 and len(body["webhooks"]) == 1
    assert "secret" not in body["webhooks"][0] and secret not in str(body)
    assert body["webhooks"][0]["last"] is None

    # The same URL checks as /ticket webhook add, on adds and edits alike.
    for url in ("http://example.com/hook", "https://10.0.0.1/hook", "https://[::1]/hook", "https://localhost/x"):
        assert (await settings("POST", "/tickets/webhooks", {"url": url}))[0] == 422, url
        assert (await settings("PATCH", f"/tickets/webhooks/{hook['id']}", {"url": url}))[0] == 422, url
    status, body = await settings("POST", "/tickets/webhooks", {"url": URL, "events": ["ticket.exploded"]})
    assert status == 422 and "opened" in body["error"]["message"]
    assert (await settings("POST", "/tickets/webhooks", {"url": URL, "type_id": 999_999}))[0] == 422
    assert (await settings("POST", "/tickets/webhooks", {"events": []}))[0] == 422

    status, edited = await settings(
        "PATCH", f"/tickets/webhooks/{hook['id']}", {"events": [], "type_id": None, "url": URL + "2"}
    )
    assert status == 200
    assert (edited["url"], edited["events"], edited["type_id"]) == (URL + "2", [], None)
    assert "secret" not in edited

    status, rotated = await settings("POST", f"/tickets/webhooks/{hook['id']}/rotate")
    assert status == 200 and rotated["secret"] != secret
    stored = await ticket_guild.repo.get_webhook(GUILD, hook["id"])
    assert stored["secret"] == rotated["secret"]
    assert secret not in caplog.text and rotated["secret"] not in caplog.text

    for _ in range(MAX_WEBHOOKS - 1):
        assert (await settings("POST", "/tickets/webhooks", {"url": URL}))[0] == 201
    status, body = await settings("POST", "/tickets/webhooks", {"url": URL})
    assert status == 422 and f"at most {MAX_WEBHOOKS}" in body["error"]["message"]

    assert (await settings("DELETE", f"/tickets/webhooks/{hook['id']}"))[0] == 200
    for method, path in (("DELETE", ""), ("PATCH", ""), ("POST", "/rotate"), ("POST", "/test")):
        status, _ = await settings(method, f"/tickets/webhooks/{hook['id']}{path}", {})
        assert status == 404, (method, path)
    # The server log gets the host, never the full URL (which may carry a token) or the secret.
    assert ("Ticket webhook #%d" % hook["id"], "none", "1.1.1.1 (opened, type %d only)" % support["id"]) in (
        settings.changes()
    )


@pytest.mark.asyncio
async def test_test_delivery_shows_up_in_the_log(settings, ticket_guild, receiver):  # noqa: F811
    _, hook = await settings("POST", "/tickets/webhooks", {"url": URL})
    _, rotated = await settings("POST", f"/tickets/webhooks/{hook['id']}/rotate")

    assert await settings("POST", f"/tickets/webhooks/{hook['id']}/test") == (200, {"ok": True, "error": None})
    (request,) = receiver.sent
    headers = request["headers"]
    assert request["url"] == URL and headers["X-Serenity-Event"] == "ticket.test"
    signed = f"{headers['X-Serenity-Timestamp']}.".encode() + request["body"]
    mac = hmac.new(rotated["secret"].encode(), signed, hashlib.sha256).hexdigest()
    assert headers["X-Serenity-Signature"] == f"sha256={mac}"  # signed with the new secret

    receiver.response.status = 500
    status, body = await settings("POST", f"/tickets/webhooks/{hook['id']}/test")
    assert (status, body) == (200, {"ok": False, "error": "HTTP 500"})

    status, log = await settings("GET", f"/tickets/webhooks/{hook['id']}/deliveries")
    assert status == 200
    assert [(d["event"], d["attempts"], d["status"], d["error"]) for d in log] == [
        ("ticket.test", 1, 500, "HTTP 500"),  # newest first
        ("ticket.test", 1, 204, None),
    ]
    assert all(isinstance(d["latency_ms"], int) for d in log)
    last = (await settings("GET", "/tickets/webhooks"))[1]["webhooks"][0]["last"]
    assert (last["status"], last["error"]) == (500, "HTTP 500")


@pytest.mark.asyncio
async def test_other_servers_webhooks_are_out_of_reach(settings, ticket_guild):  # noqa: F811
    theirs = await ticket_guild.repo.add_webhook(GUILD + 1, URL, "s", [], None)
    await ticket_guild.repo.add_delivery(theirs["id"], "ticket.opened")
    assert await settings("GET", f"/tickets/webhooks/{theirs['id']}/deliveries") == (200, [])
    for method, path in (("PATCH", ""), ("POST", "/rotate"), ("POST", "/test"), ("DELETE", "")):
        status, _ = await settings(method, f"/tickets/webhooks/{theirs['id']}{path}", {})
        assert status == 404, (method, path)
    assert (await ticket_guild.repo.get_webhook(GUILD + 1, theirs["id"]))["secret"] == "s"
