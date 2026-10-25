"""Log-event counter only counts embeds actually posted, never skipped or failed sends."""

from unittest.mock import AsyncMock

import hikari
import pytest

from serenity.database.logging_repository import LoggingRepository
from serenity.services.logging_service import LoggingService
from serenity.services.metrics import LOG_EVENTS_SENT


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
