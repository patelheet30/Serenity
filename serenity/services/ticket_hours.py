"""Support hours: a weekly schedule in a timezone, and time counted only while it's open.

A schedule is seven lists (Monday first) of [start, end] minutes past local midnight, end ≤ 1440.
`None` means no schedule: always open. Everything works on aware datetimes and goes through
UTC, so a day that DST makes 23 or 25 hours long counts as exactly that.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Schedule = list[list[list[int]]]

_PART = re.compile(r"(daily|[a-z]{3})(?:\s*-\s*([a-z]{3}))?((?:\s+\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})+)")
_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


def parse_schedule(text: str) -> Schedule:
    """`mon-fri 09:00-17:00; sat 10:00-14:00` as a schedule. Raises ValueError, said plainly.

    ponytail: no overnight ranges; a 22:00–02:00 shift is `mon 22:00-24:00; tue 00:00-02:00`.
    """
    schedule: Schedule = [[] for _ in DAYS]
    for part in re.split(r"[;,\n]", text.lower()):
        if not part.strip():
            continue
        match = _PART.fullmatch(part.strip())
        if not match or (match[1] == "daily" and match[2]) or not {match[1], match[2]} <= {*DAYS, "daily", None}:
            raise ValueError(f"I couldn't read “{part.strip()}”. Write it like `mon-fri 09:00-17:00; sat 10:00-14:00`.")
        if match[1] == "daily":
            days = list(range(7))
        else:
            first, last = DAYS.index(match[1]), DAYS.index(match[2] or match[1])
            days = [(first + i) % 7 for i in range((last - first) % 7 + 1)]  # fri-mon wraps
        for h1, m1, h2, m2 in _RANGE.findall(match[3]):
            start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
            if int(m1) > 59 or int(m2) > 59 or not 0 <= start < end <= 1440:
                raise ValueError(f"{h1}:{m1}-{h2}:{m2} isn't a time range within one day.")
            for day in days:
                schedule[day].append([start, end])
    if not any(schedule):
        raise ValueError("Give at least one day and time range, like `mon-fri 09:00-17:00`.")
    for i, ranges in enumerate(schedule):  # merge overlaps, so no minute counts twice
        merged: list[list[int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        schedule[i] = merged
    return schedule


def format_schedule(schedule: Schedule) -> str:
    """"Mon–Fri 09:00–17:00, Sat 10:00–14:00"."""

    def hm(minutes: int) -> str:
        return f"{minutes // 60:02}:{minutes % 60:02}"

    parts, i = [], 0
    while i < 7:
        j = i
        while j + 1 < 7 and schedule[j + 1] == schedule[i]:
            j += 1
        if schedule[i]:
            days = DAYS[i].title() if i == j else f"{DAYS[i].title()}–{DAYS[j].title()}"
            parts.append(f"{days} " + ", ".join(f"{hm(s)}–{hm(e)}" for s, e in schedule[i]))
        i = j + 1
    return ", ".join(parts)


def _intervals(schedule: Schedule, zone: ZoneInfo, day: date) -> Iterator[tuple[datetime, datetime]]:
    """The open periods of one local day, in UTC."""

    def at(minutes: int) -> datetime:
        local = datetime.combine(day + timedelta(days=minutes // 1440), time(minutes % 1440 // 60, minutes % 60), zone)
        return local.astimezone(timezone.utc)  # same-tzinfo datetimes compare by wall clock

    for start, end in schedule[day.weekday()]:
        yield at(start), at(end)


def within_hours(schedule: Optional[Schedule], tz: str, now: datetime) -> bool:
    if schedule is None:
        return True
    now = now.astimezone(timezone.utc)
    zone = ZoneInfo(tz)
    return any(start <= now < end for start, end in _intervals(schedule, zone, now.astimezone(zone).date()))


def next_open(schedule: Optional[Schedule], tz: str, now: datetime) -> Optional[datetime]:
    """When support is next open: `now` if it is, None if the schedule is empty."""
    if schedule is None:
        return now
    now = now.astimezone(timezone.utc)
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    for offset in range(8):
        for start, end in _intervals(schedule, zone, today + timedelta(days=offset)):
            if end > now:
                return max(start, now)
    return None


def business_seconds_between(schedule: Optional[Schedule], tz: str, start: datetime, end: datetime) -> int:
    """Seconds between `start` and `end` that fall within the schedule."""
    if end <= start:
        return 0
    if schedule is None:
        return int((end - start).total_seconds())
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    zone = ZoneInfo(tz)
    day, last = start.astimezone(zone).date(), end.astimezone(zone).date()
    total = 0.0
    while day <= last:
        for a, b in _intervals(schedule, zone, day):
            total += max(0.0, (min(b, end) - max(a, start)).total_seconds())
        day += timedelta(days=1)
    return int(total)


def effective_schedule(type_schedule: Optional[Schedule], settings: dict) -> Optional[Schedule]:
    """A type's own schedule, else the server's (the Tickets module settings)."""
    return type_schedule or settings.get("schedule")


def offline_status(settings: dict, type_schedule: Optional[Schedule], now: datetime) -> Optional[dict]:
    """None while support is open; otherwise {"back": datetime or None, "note": str or None}.

    `settings` is the Tickets module's: "schedule", "timezone" and "away" ({"until", "note"}).
    """
    schedule = effective_schedule(type_schedule, settings)
    tz = settings.get("timezone", "UTC")
    away = settings.get("away")
    if away and away.get("until") is not None and away["until"] <= now.timestamp():
        away = None  # away mode ended by itself
    if away:
        until = away.get("until")
        back = next_open(schedule, tz, datetime.fromtimestamp(until, timezone.utc)) if until else None
        return {"back": back, "note": away.get("note")}
    if within_hours(schedule, tz, now):
        return None
    return {"back": next_open(schedule, tz, now), "note": None}
