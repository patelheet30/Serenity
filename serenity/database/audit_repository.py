"""Stored audit log entries for the Audit history module (#154)."""

import datetime
import enum
import json
import time
from collections.abc import Mapping
from typing import Any, Optional

import asyncpg
import hikari

from serenity.core.modules import ModuleType

RETENTION_KEY = "retention_days"  # in the Audit history module's settings
RETENTION_DEFAULT = 90  # days
RETENTION_MAX = 365


def _plain(value: Any) -> Any:
    """An audit log change value (hikari objects included) as JSON."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, enum.Enum):
        return value.value if isinstance(value.value, (int, str)) else value.name
    if isinstance(value, int):  # Snowflake, Permissions
        return str(value) if value > 2**53 else int(value)
    if isinstance(value, float):
        return value
    if isinstance(value, datetime.timedelta):
        return int(value.total_seconds())
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return [_plain(v) for v in value.values()]
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, hikari.PartialRole):
        return {"id": str(value.id), "name": value.name}
    return str(value)


class AuditRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def add(self, entry: hikari.AuditLogEntry) -> None:
        changes = [
            {"key": str(c.key), "old": _plain(c.old_value), "new": _plain(c.new_value)}
            for c in entry.changes
        ]
        options = None
        if entry.options is not None:
            options = {
                k: _plain(getattr(entry.options, k))
                for k in ("channel_id", "count", "message_id", "delete_member_days", "members_removed", "type")
                if hasattr(entry.options, k)
            }
        await self.pool.execute(
            """INSERT INTO audit_log_entries
            (guild_id, entry_id, action_type, actor_id, target_id, reason, changes, options, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT DO NOTHING""",
            entry.guild_id,
            entry.id,
            int(entry.action_type),
            entry.user_id,
            entry.target_id,
            entry.reason,
            json.dumps(changes),
            None if options is None else json.dumps(options),
            int(entry.id.created_at.timestamp()),
        )

    async def search(
        self,
        guild_id: int,
        *,
        actor: Optional[int] = None,
        target: Optional[int] = None,
        actions: Optional[list[int]] = None,
        q: str = "",
        since: Optional[int] = None,
        before: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Newest first; `before` is an entry_id cursor from the previous page."""
        clauses, params = ["guild_id = $1"], [guild_id]

        def add(clause: str, value: Any) -> None:
            params.append(value)
            clauses.append(clause.format(f"${len(params)}"))

        if actor:
            add("actor_id = {}", actor)
        if target:
            add("target_id = {}", target)
        if actions:
            add("action_type = ANY({})", actions)
        if q:
            like = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            add("(reason ILIKE {0} OR changes::text ILIKE {0})", f"%{like}%")
        if since:
            add("created_at >= {}", since)
        if before:
            add("entry_id < {}", before)
        params.append(limit)
        rows = await self.pool.fetch(
            f"SELECT * FROM audit_log_entries WHERE {' AND '.join(clauses)} "
            f"ORDER BY entry_id DESC LIMIT ${len(params)}",
            *params,
        )
        return [
            {**dict(r), "changes": json.loads(r["changes"]), "options": r["options"] and json.loads(r["options"])}
            for r in rows
        ]

    async def clear(self, guild_id: int) -> int:
        result = await self.pool.execute("DELETE FROM audit_log_entries WHERE guild_id = $1", guild_id)
        return int(result.split()[-1])

    async def cleanup(self, now: Optional[int] = None) -> None:
        """Drop entries older than each server's retention (default 90 days)."""
        # One retention lookup per server with history, then an index range delete per server.
        await self.pool.execute(
            """WITH cutoff AS (
                SELECT g.guild_id, $1 - 86400 * COALESCE((m.settings::jsonb ->> $3)::int, $2) AS before
                FROM (SELECT DISTINCT guild_id FROM audit_log_entries) g
                LEFT JOIN module_config m ON m.guild_id = g.guild_id AND m.module_type = $4
            )
            DELETE FROM audit_log_entries a USING cutoff c
            WHERE a.guild_id = c.guild_id AND a.created_at < c.before""",
            now or int(time.time()),
            RETENTION_DEFAULT,
            RETENTION_KEY,
            ModuleType.AUDIT_HISTORY.value,
        )
