"""Match gateway events to the audit log entry that says who did them (#151).

Discord sends the change (e.g. GUILD_CHANNEL_UPDATE) and its audit log entry as separate events,
in either order. The audit log listener feeds every entry here; a log handler asks for the entry
for its change and waits briefly if it hasn't arrived yet.
"""

import asyncio
import time
from typing import Callable, Iterable, Optional

import hikari

WAIT = 2.0  # seconds a log waits for its audit log entry
# Seconds an entry that arrived before its change stays matchable. Kept well under the time a
# later change of the same target takes (e.g. auto-slowmode reacting), so an entry no log
# claimed isn't handed to it. Entries arriving after the change go through WAIT instead.
KEEP = 1.0

Key = tuple[int, int, int]  # guild, action type, target


class AuditActors:
    def __init__(self, can_view: Callable[[int], bool]) -> None:
        """can_view(guild_id): whether the bot has View Audit Log there (else nothing arrives)."""
        self._can_view = can_view
        self._recent: dict[Key, tuple[float, hikari.AuditLogEntry]] = {}
        self._waiting: dict[Key, list[asyncio.Future[hikari.AuditLogEntry]]] = {}

    def feed(self, entry: hikari.AuditLogEntry) -> None:
        if entry.target_id is None:
            return
        key = (int(entry.guild_id), int(entry.action_type), int(entry.target_id))
        waiters = [f for f in self._waiting.pop(key, []) if not f.done()]
        for future in waiters:
            future.set_result(entry)
        if not waiters:
            now = time.monotonic()
            self._recent = {k: v for k, v in self._recent.items() if now - v[0] < KEEP}
            self._recent[key] = (now, entry)

    async def find(
        self,
        guild_id: int,
        action_types: Iterable[hikari.AuditLogEventType],
        target_id: int,
        timeout: float = WAIT,
    ) -> Optional[hikari.AuditLogEntry]:
        """The audit log entry for a change, or None if it doesn't come within the timeout."""
        if not self._can_view(guild_id):
            return None
        keys = [(int(guild_id), int(t), int(target_id)) for t in action_types]
        now = time.monotonic()
        for key in keys:
            hit = self._recent.pop(key, None)
            if hit and now - hit[0] < KEEP:
                return hit[1]

        future: asyncio.Future[hikari.AuditLogEntry] = asyncio.get_running_loop().create_future()
        for key in keys:
            self._waiting.setdefault(key, []).append(future)
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            for key in keys:
                if future in self._waiting.get(key, []):
                    self._waiting[key].remove(future)
                    if not self._waiting[key]:
                        del self._waiting[key]
