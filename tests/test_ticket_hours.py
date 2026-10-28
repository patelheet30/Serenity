"""Support hours across timezones and DST (Europe/London: BST ends 25 Oct 2026, starts 29 Mar 2026)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from serenity.services.ticket_hours import (
    business_seconds_between,
    format_schedule,
    next_open,
    offline_status,
    parse_schedule,
    within_hours,
)

LONDON = "Europe/London"
WEEKDAYS = parse_schedule("mon-fri 09:00-17:00")


def local(*args: int) -> datetime:
    return datetime(*args, tzinfo=ZoneInfo(LONDON))


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_parse_and_format():
    schedule = parse_schedule("Mon-Fri 09:00-12:00, mon-fri 11:00-17:00; sat 10:00-14:00; sun-mon 20:00-24:00")
    assert schedule[0] == [[540, 1020], [1200, 1440]]  # overlaps merged
    assert schedule[6] == [[1200, 1440]]
    assert format_schedule(WEEKDAYS) == "Mon–Fri 09:00–17:00"
    assert len(parse_schedule("daily 00:00-24:00")) == 7
    for bad in ("weekdays 9:00-17:00", "mon 17:00-09:00", "mon 09:00-25:00", "mon 09:70-10:00", ""):
        with pytest.raises(ValueError):
            parse_schedule(bad)


def test_opened_at_3am_gets_the_next_opening_across_dst():
    # Saturday 03:00 BST; Monday 09:00 is GMT by then, so it's 09:00 UTC, not 08:00.
    now = local(2026, 10, 24, 3, 0)
    assert not within_hours(WEEKDAYS, LONDON, now)
    assert next_open(WEEKDAYS, LONDON, now) == utc(2026, 10, 26, 9, 0)
    # And the other way in spring: Monday 09:00 BST is 08:00 UTC.
    assert next_open(WEEKDAYS, LONDON, local(2026, 3, 28, 3, 0)) == utc(2026, 3, 30, 8, 0)
    assert within_hours(WEEKDAYS, LONDON, utc(2026, 3, 30, 8, 0))
    assert not within_hours(WEEKDAYS, LONDON, utc(2026, 3, 30, 16, 30))  # 17:30 BST


def test_business_seconds_follow_the_real_length_of_dst_days():
    always = parse_schedule("daily 00:00-24:00")
    assert business_seconds_between(always, LONDON, local(2026, 10, 25), local(2026, 10, 26)) == 25 * 3600
    assert business_seconds_between(always, LONDON, local(2026, 3, 29), local(2026, 3, 30)) == 23 * 3600
    # Friday 16:00 to Monday 10:00: one hour each side of the weekend.
    assert business_seconds_between(WEEKDAYS, LONDON, local(2026, 10, 23, 16), local(2026, 10, 26, 10)) == 2 * 3600
    assert business_seconds_between(None, LONDON, utc(2026, 1, 1), utc(2026, 1, 2)) == 86400
    assert business_seconds_between(WEEKDAYS, LONDON, utc(2026, 1, 2), utc(2026, 1, 1)) == 0


def test_offline_status_with_away_mode():
    settings = {"schedule": WEEKDAYS, "timezone": LONDON}
    monday_noon = local(2026, 10, 26, 12)
    assert offline_status(settings, None, monday_noon) is None
    assert offline_status({}, None, monday_noon) is None  # nothing configured: always open
    assert offline_status(settings, None, local(2026, 10, 24, 3)) == {"back": utc(2026, 10, 26, 9), "note": None}

    # Away until Wednesday 00:00: back at Wednesday's opening, with the note.
    away = {**settings, "away": {"until": local(2026, 10, 28).timestamp(), "note": "Team offsite"}}
    assert offline_status(away, None, monday_noon) == {"back": utc(2026, 10, 28, 9), "note": "Team offsite"}
    assert offline_status(away, None, local(2026, 10, 28, 12)) is None  # it ended by itself
    assert offline_status({"away": {"until": None, "note": None}}, None, monday_noon) == {"back": None, "note": None}
    # A type's own schedule wins over the server's.
    assert offline_status(settings, parse_schedule("daily 00:00-24:00"), local(2026, 10, 24, 3)) is None
