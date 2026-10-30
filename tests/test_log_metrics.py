"""Log-event counter: counts only posted logs, and every series starts at 0."""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import hikari
import pytest

from serenity.database.logging_repository import LoggingRepository
from serenity.services.logging_service import _AUDIT_LOGS, LoggingService
from serenity.services.metrics import LOG_EVENTS, LOG_EVENTS_SENT, init_guild_log_events


def _sent(guild_id: int) -> float:
    return LOG_EVENTS_SENT.labels(guild_id=str(guild_id), event="role_update")._value.get()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("should_log", "send_error", "expected"),
    [
        ((True, 555), None, 1),
        ((False, None), None, 0),
        ((True, 555), hikari.ForbiddenError("", {}, b""), 0),
    ],
)
async def test_counts_only_posted_logs(should_log, send_error, expected):
    guild_id = 4242 + expected + (send_error is not None)  # fresh label set per case
    repo = AsyncMock(spec=LoggingRepository)
    repo.should_log_event.return_value = should_log
    rest = AsyncMock()
    rest.create_message.side_effect = send_error
    svc = LoggingService(repo=repo, rest=rest)

    before = _sent(guild_id)
    await svc._send(
        guild_id=guild_id, log_type="server", log_event="role_update", embed=hikari.Embed()
    )

    assert _sent(guild_id) - before == expected


def test_log_events_cover_every_sent_event():
    """init_guild_log_events only zero-inits what's in LOG_EVENTS, so it must not drift."""
    logging_src = Path("serenity/services/logging_service.py").read_text()
    moderation_src = Path("serenity/services/moderation_service.py").read_text()
    sent = set(re.findall(r'log_event="(\w+)"', logging_src)) | {e for _, _, e in _AUDIT_LOGS.values()}
    actions = re.findall(r'action="(\w+)"', moderation_src)
    external = re.findall(r'MEMBER_\w+: "(\w+)"', moderation_src) + ["timeout", "untimeout"]
    assert sent | {f"mod_{a}" for a in actions} | {f"external_{a}" for a in external} == set(LOG_EVENTS)


def test_init_guild_log_events_starts_at_zero():
    init_guild_log_events(777)
    assert all(
        LOG_EVENTS_SENT.labels(guild_id="777", event=e)._value.get() == 0 for e in LOG_EVENTS
    )
