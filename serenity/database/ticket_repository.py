import json
import time
from typing import Any, Optional

import asyncpg

from serenity.utils.errors import DatabaseError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

# asyncpg returns JSONB as text unless the pool has a codec; the pool is shared, so decode here.
_JSON_COLUMNS = ("fields", "stages", "data", "document", "schedule")

# Columns a ticket type can be created or updated with.
TYPE_COLUMNS = (
    "name",
    "emoji",
    "description",
    "category_id",
    "staff_role_ids",
    "fields",
    "stages",
    "claim_mode",
    "open_limit",
    "name_template",
    "transcripts",
    "retention_days",
    "auto_unclaim_hours",
    "ratings",
    "auto_close_hours",
    "schedule",
    "open_off_hours",
)

# Columns a tier can be created or updated with.
TIER_COLUMNS = ("name", "position", "role_ids", "can_summon", "requires_reason", "reping_minutes")


def _decode(row: Optional[asyncpg.Record]) -> Optional[dict]:
    if row is None:
        return None
    out = dict(row)
    for key in _JSON_COLUMNS:
        if isinstance(out.get(key), str):
            out[key] = json.loads(out[key])
    return out


def _encode(key: str, value: Any) -> Any:
    return json.dumps(value) if key in _JSON_COLUMNS else value


class TicketRepository:
    """Repository for ticket types, panels, tickets and their transcripts"""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    def _require_pool(self) -> asyncpg.Pool:
        if not self.pool:
            raise DatabaseError("Database connection is not initialised.")
        return self.pool

    # Types

    async def create_type(self, guild_id: int, name: str, /, **values: Any) -> dict:
        pool = self._require_pool()
        columns = ["guild_id", "name", "created_at"]
        params: list[Any] = [guild_id, name, int(time.time())]
        for key, value in values.items():
            if key not in TYPE_COLUMNS:
                raise ValueError(f"Unknown ticket type column: {key}")
            columns.append(key)
            params.append(_encode(key, value))
        placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
        row = await pool.fetchrow(
            f"INSERT INTO ticket_types ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *",
            *params,
        )
        return _decode(row)  # type: ignore[return-value]

    async def update_type(self, guild_id: int, type_id: int, /, **values: Any) -> Optional[dict]:
        pool = self._require_pool()
        sets, params = [], [guild_id, type_id]
        for key, value in values.items():
            if key not in TYPE_COLUMNS:
                raise ValueError(f"Unknown ticket type column: {key}")
            params.append(_encode(key, value))
            sets.append(f"{key} = ${len(params)}")
        if not sets:
            return await self.get_type(guild_id, type_id)
        row = await pool.fetchrow(
            f"UPDATE ticket_types SET {', '.join(sets)} WHERE guild_id = $1 AND id = $2 RETURNING *",
            *params,
        )
        return _decode(row)

    async def delete_type(self, guild_id: int, type_id: int) -> bool:
        """Delete a type that no panel shows and that has never had a ticket (tickets keep
        theirs for history)."""
        pool = self._require_pool()
        status = await pool.execute(
            """DELETE FROM ticket_types t WHERE guild_id = $1 AND id = $2
            AND NOT EXISTS (SELECT 1 FROM tickets WHERE type_id = t.id)
            AND NOT EXISTS (SELECT 1 FROM ticket_panels WHERE t.id = ANY(type_ids))""",
            guild_id,
            type_id,
        )
        return status != "DELETE 0"

    async def get_type(self, guild_id: int, type_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_types WHERE guild_id = $1 AND id = $2", guild_id, type_id
        )
        return _decode(row)

    async def get_type_by_name(self, guild_id: int, name: str) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_types WHERE guild_id = $1 AND lower(name) = lower($2)",
            guild_id,
            name,
        )
        return _decode(row)

    async def list_types(self, guild_id: int) -> list[dict]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_types WHERE guild_id = $1 ORDER BY id", guild_id
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    # Panels

    async def _check_types(self, guild_id: int, type_ids: list[int]) -> None:
        """Panels may only show the guild's own types (type_ids has no foreign key)."""
        pool = self._require_pool()
        found = await pool.fetchval(
            "SELECT COUNT(*) FROM ticket_types WHERE guild_id = $1 AND id = ANY($2::bigint[])",
            guild_id,
            type_ids,
        )
        if found != len(set(type_ids)):
            raise ValueError("Panels can only show this guild's ticket types.")

    async def create_panel(
        self,
        guild_id: int,
        channel_id: int,
        title: str,
        type_ids: list[int],
        description: Optional[str] = None,
        style: str = "buttons",
    ) -> dict:
        pool = self._require_pool()
        await self._check_types(guild_id, type_ids)
        row = await pool.fetchrow(
            """INSERT INTO ticket_panels (guild_id, channel_id, title, description, style, type_ids, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *""",
            guild_id,
            channel_id,
            title,
            description,
            style,
            type_ids,
            int(time.time()),
        )
        return dict(row)

    async def get_panel(self, guild_id: int, panel_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_panels WHERE guild_id = $1 AND id = $2", guild_id, panel_id
        )
        return dict(row) if row else None

    async def list_panels(self, guild_id: int) -> list[dict]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_panels WHERE guild_id = $1 ORDER BY id", guild_id
        )
        return [dict(r) for r in rows]

    async def update_panel(self, guild_id: int, panel_id: int, /, **values: Any) -> Optional[dict]:
        pool = self._require_pool()
        allowed = ("channel_id", "message_id", "title", "description", "style", "type_ids")
        sets, params = [], [guild_id, panel_id]
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"Unknown ticket panel column: {key}")
            params.append(value)
            sets.append(f"{key} = ${len(params)}")
        if not sets:
            return await self.get_panel(guild_id, panel_id)
        if "type_ids" in values:
            await self._check_types(guild_id, values["type_ids"])
        row = await pool.fetchrow(
            f"UPDATE ticket_panels SET {', '.join(sets)} WHERE guild_id = $1 AND id = $2 RETURNING *",
            *params,
        )
        return dict(row) if row else None

    async def delete_panel(self, guild_id: int, panel_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "DELETE FROM ticket_panels WHERE guild_id = $1 AND id = $2 RETURNING *",
            guild_id,
            panel_id,
        )
        return dict(row) if row else None

    async def panel_message_ids(self) -> list[int]:
        pool = self._require_pool()
        rows = await pool.fetch("SELECT message_id FROM ticket_panels WHERE message_id IS NOT NULL")
        return [r["message_id"] for r in rows]

    async def panel_guilds(self) -> list[int]:
        """Guilds with a posted panel, whose support status the panels show."""
        pool = self._require_pool()
        rows = await pool.fetch("SELECT DISTINCT guild_id FROM ticket_panels WHERE message_id IS NOT NULL")
        return [r["guild_id"] for r in rows]

    async def forget_panel_message(self, message_id: int) -> Optional[dict]:
        """The panel's message was deleted by hand: keep the panel, unpost it."""
        pool = self._require_pool()
        row = await pool.fetchrow(
            "UPDATE ticket_panels SET message_id = NULL WHERE message_id = $1 RETURNING *",
            message_id,
        )
        return dict(row) if row else None

    # Tickets

    async def create_ticket(
        self,
        guild_id: int,
        type_id: int,
        opener_id: int,
        fields: dict[str, str],
        identity: Optional[dict[str, str]] = None,
    ) -> dict:
        """Insert a ticket with the guild's next number (no channel yet), and its identity
        values (normalised identity-field answers, by key)."""
        pool = self._require_pool()
        now = int(time.time())
        async with pool.acquire() as conn, conn.transaction():
            # Same per-guild numbering lock as mod cases; the key space is shared, which only
            # means a case and a ticket in one guild can briefly wait on each other.
            await conn.execute("SELECT pg_advisory_xact_lock($1)", guild_id)
            row = await conn.fetchrow(
                """INSERT INTO tickets (guild_id, number, type_id, opener_id, fields, opened_at, last_activity_at, stage)
                SELECT $1, COALESCE(MAX(number), 0) + 1, $2, $3, $4, $5, $5,
                       (SELECT stages -> 0 ->> 'name' FROM ticket_types WHERE id = $2)  -- the first stage
                FROM tickets WHERE guild_id = $1
                HAVING EXISTS (SELECT 1 FROM ticket_types WHERE id = $2 AND guild_id = $1)
                RETURNING *""",
                guild_id,
                type_id,
                opener_id,
                _encode("fields", fields),
                now,
            )
            if row is None:  # the type belongs to another guild (the FK isn't guild-scoped)
                raise ValueError(f"Ticket type {type_id} isn't in guild {guild_id}.")
            await conn.execute(
                "INSERT INTO ticket_events (ticket_id, kind, actor_id, created_at) VALUES ($1, 'opened', $2, $3)",
                row["id"],
                opener_id,
                now,
            )
            await self._set_identity(conn, row["id"], guild_id, identity or {})
        return _decode(row)  # type: ignore[return-value]

    async def _set_identity(
        self, conn: asyncpg.Connection, ticket_id: int, guild_id: int, identity: dict[str, str]
    ) -> None:
        await conn.execute("DELETE FROM ticket_identity WHERE ticket_id = $1", ticket_id)
        await conn.executemany(
            "INSERT INTO ticket_identity (ticket_id, guild_id, field_key, value) VALUES ($1, $2, $3, $4)",
            [(ticket_id, guild_id, key, value) for key, value in identity.items()],
        )

    async def update_fields(
        self, ticket_id: int, guild_id: int, fields: dict[str, str], identity: dict[str, str]
    ) -> dict:
        pool = self._require_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE tickets SET fields = $2 WHERE id = $1 RETURNING *",
                ticket_id,
                _encode("fields", fields),
            )
            await self._set_identity(conn, ticket_id, guild_id, identity)
        return _decode(row)  # type: ignore[return-value]

    async def linked_tickets(self, ticket_id: int, limit: int = 10) -> list[dict]:
        """Other tickets, of any type, sharing an identity value with this one; newest first.
        Each has `linked_by`: the field keys they share."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT t.*, ty.name AS type_name,
                array_agg(other.field_key ORDER BY other.field_key) AS linked_by
            FROM ticket_identity mine
            JOIN ticket_identity other ON other.guild_id = mine.guild_id
                AND other.field_key = mine.field_key AND other.value = mine.value
                AND other.ticket_id <> mine.ticket_id
            JOIN tickets t ON t.id = other.ticket_id
            JOIN ticket_types ty ON ty.id = t.type_id
            WHERE mine.ticket_id = $1
            GROUP BY t.id, ty.name ORDER BY t.opened_at DESC, t.id DESC LIMIT $2""",
            ticket_id,
            limit,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    async def opener_tickets(
        self, guild_id: int, opener_id: int, exclude_id: int, limit: int = 5
    ) -> tuple[list[dict], int]:
        """A member's other tickets, newest first, and how many there are in all."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT t.*, ty.name AS type_name, COUNT(*) OVER () AS total
            FROM tickets t JOIN ticket_types ty ON ty.id = t.type_id
            WHERE t.guild_id = $1 AND t.opener_id = $2 AND t.id <> $3
            ORDER BY t.opened_at DESC, t.id DESC LIMIT $4""",
            guild_id,
            opener_id,
            exclude_id,
            limit,
        )
        return [_decode(r) for r in rows], rows[0]["total"] if rows else 0  # type: ignore[misc]

    async def delete_ticket(self, ticket_id: int) -> None:
        """Undo a ticket whose channel couldn't be created."""
        pool = self._require_pool()
        await pool.execute("DELETE FROM tickets WHERE id = $1", ticket_id)

    async def get_ticket(self, guild_id: int, number: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM tickets WHERE guild_id = $1 AND number = $2", guild_id, number
        )
        return _decode(row)

    async def get_ticket_by_id(self, ticket_id: int) -> Optional[dict]:
        pool = self._require_pool()
        return _decode(await pool.fetchrow("SELECT * FROM tickets WHERE id = $1", ticket_id))

    async def get_ticket_by_channel(self, channel_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow("SELECT * FROM tickets WHERE channel_id = $1", channel_id)
        return _decode(row)

    async def set_ticket_channel(self, ticket_id: int, channel_id: Optional[int]) -> None:
        pool = self._require_pool()
        await pool.execute("UPDATE tickets SET channel_id = $2 WHERE id = $1", ticket_id, channel_id)

    async def update_ticket(self, ticket_id: int, /, **values: Any) -> Optional[dict]:
        pool = self._require_pool()
        allowed = (
            "type_id", "assignee_id", "priority", "stage", "waiting_on", "last_activity_at",
            "tier_id", "tier_pinged_at", "close_warned_at",
        )
        sets, params = [], [ticket_id]
        for key, value in values.items():
            if key not in allowed:
                raise ValueError(f"Unknown ticket column: {key}")
            params.append(value)
            sets.append(f"{key} = ${len(params)}")
        if not sets:
            return await self.get_ticket_by_id(ticket_id)
        row = await pool.fetchrow(
            f"UPDATE tickets SET {', '.join(sets)} WHERE id = $1 RETURNING *", *params
        )
        return _decode(row)

    async def count_open(self, guild_id: int, opener_id: int, type_id: int) -> int:
        pool = self._require_pool()
        return await pool.fetchval(
            """SELECT COUNT(*) FROM tickets
            WHERE guild_id = $1 AND opener_id = $2 AND type_id = $3 AND status = 'open'""",
            guild_id,
            opener_id,
            type_id,
        )

    async def last_opened_at(self, guild_id: int, opener_id: int) -> Optional[int]:
        """When this member last opened a ticket here, open or closed (the open cooldown)."""
        pool = self._require_pool()
        return await pool.fetchval(
            "SELECT MAX(opened_at) FROM tickets WHERE guild_id = $1 AND opener_id = $2",
            guild_id,
            opener_id,
        )

    async def count_opened_since(self, guild_id: int, since: int) -> int:
        """Tickets opened here since a time, open or closed (the raid brake)."""
        pool = self._require_pool()
        return await pool.fetchval(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = $1 AND opened_at >= $2", guild_id, since
        )

    async def list_open(self, guild_id: int) -> list[dict]:
        """A guild's open tickets, oldest first (the dashboard queue filters them)."""
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM tickets WHERE guild_id = $1 AND status = 'open' ORDER BY opened_at, id",
            guild_id,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    async def open_ticket_channels(self) -> list[int]:
        """Every open ticket's channel, across guilds (the bot's in-memory lookup set)."""
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT channel_id FROM tickets WHERE status = 'open' AND channel_id IS NOT NULL"
        )
        return [r["channel_id"] for r in rows]

    # Claiming and the credit chain

    async def claim(self, ticket_id: int, user_id: int, *, force: bool = False) -> Optional[dict]:
        """Assign the ticket to `user_id`: only if unassigned, unless `force` (a reassign).

        Returns the updated ticket, or None if someone else already has it.
        """
        pool = self._require_pool()
        now = int(time.time())
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                # Claiming counts as activity, so auto-unclaim gives the new assignee a full window,
                # and it answers an escalation: the tier stops being re-pinged.
                f"""UPDATE tickets SET assignee_id = $2, last_activity_at = $3, tier_pinged_at = NULL
                WHERE id = $1 AND status = 'open' {"" if force else "AND assignee_id IS NULL"}
                RETURNING *""",
                ticket_id,
                user_id,
                now,
            )
            if row is None:
                return None
            await conn.execute(
                """INSERT INTO ticket_handlers (ticket_id, user_id, role, joined_at)
                VALUES ($1, $2, 'claimed', $3)
                ON CONFLICT (ticket_id, user_id) DO UPDATE SET role = 'claimed'""",
                ticket_id,
                user_id,
                now,
            )
        return _decode(row)

    async def unclaim(self, ticket_id: int, expected: Optional[int] = None) -> Optional[dict]:
        """Clear the assignee, if it's still `expected` when given (a claim or reassign that
        landed in between wins). They stay in the credit chain for what they already did.
        Returns None if nothing changed."""
        pool = self._require_pool()
        row = await pool.fetchrow(
            """UPDATE tickets SET assignee_id = NULL
            WHERE id = $1 AND ($2::bigint IS NULL OR assignee_id = $2) RETURNING *""",
            ticket_id,
            expected,
        )
        return _decode(row)

    async def record_message(self, ticket_id: int, author_id: int, *, staff: bool) -> Optional[dict]:
        """Count a message: staff replies join the credit chain and hand the turn to the opener."""
        pool = self._require_pool()
        now = int(time.time())
        async with pool.acquire() as conn, conn.transaction():
            if staff:
                await conn.execute(
                    """INSERT INTO ticket_handlers (ticket_id, user_id, role, message_count, joined_at)
                    VALUES ($1, $2, 'assisted', 1, $3)
                    ON CONFLICT (ticket_id, user_id) DO UPDATE SET
                    message_count = ticket_handlers.message_count + 1""",
                    ticket_id,
                    author_id,
                    now,
                )
            row = await conn.fetchrow(
                """UPDATE tickets SET last_activity_at = $2, waiting_on = $3,
                first_response_at = CASE WHEN $4 THEN COALESCE(first_response_at, $2)
                                         ELSE first_response_at END
                WHERE id = $1 RETURNING *""",
                ticket_id,
                now,
                "opener" if staff else "staff",
                staff,
            )
        return _decode(row)

    async def list_handlers(self, ticket_id: int) -> list[dict]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_handlers WHERE ticket_id = $1 ORDER BY joined_at, user_id",
            ticket_id,
        )
        return [dict(r) for r in rows]

    async def credit_handlers(
        self, ticket_id: int, conn: Optional[asyncpg.Connection] = None
    ) -> None:
        """On close: one handled credit for each handler who sent at least one message."""
        await (conn or self._require_pool()).execute(
            "UPDATE ticket_handlers SET credited = message_count >= 1 WHERE ticket_id = $1",
            ticket_id,
        )

    async def due_for_unclaim(self, now: int) -> list[dict]:
        """Claimed tickets whose opener has waited longer than the type's auto-unclaim hours."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT t.*, ty.auto_unclaim_hours, ty.staff_role_ids
            FROM tickets t JOIN ticket_types ty ON ty.id = t.type_id
            WHERE t.status = 'open' AND t.assignee_id IS NOT NULL AND t.waiting_on = 'staff'
            AND ty.auto_unclaim_hours IS NOT NULL
            AND t.last_activity_at < $1 - ty.auto_unclaim_hours * 3600""",
            now,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    # Escalation tiers

    async def list_tiers(self, type_id: int) -> list[dict]:
        """A type's ladder, lowest tier first."""
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_tiers WHERE type_id = $1 ORDER BY position", type_id
        )
        return [dict(r) for r in rows]

    async def guild_tiers(self, guild_id: int) -> list[dict]:
        """Every ladder in the guild in one query: by type, lowest tier first."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT tr.* FROM ticket_tiers tr JOIN ticket_types ty ON ty.id = tr.type_id
            WHERE ty.guild_id = $1 ORDER BY tr.type_id, tr.position""",
            guild_id,
        )
        return [dict(r) for r in rows]

    async def create_tier(self, type_id: int, /, **values: Any) -> dict:
        """Without a position, the new top rung, read in the INSERT itself (the unique
        (type_id, position) catches two at once)."""
        pool = self._require_pool()
        columns = ["type_id", "created_at"]
        params: list[Any] = [type_id, int(time.time())]
        for key, value in values.items():
            if key not in TIER_COLUMNS:
                raise ValueError(f"Unknown ticket tier column: {key}")
            columns.append(key)
            params.append(value)
        placeholders = [f"${i}" for i in range(1, len(params) + 1)]
        if "position" not in values:
            columns.append("position")
            placeholders.append(
                "(SELECT COALESCE(MAX(position), 0) + 1 FROM ticket_tiers WHERE type_id = $1)"
            )
        row = await pool.fetchrow(
            f"INSERT INTO ticket_tiers ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) RETURNING *",
            *params,
        )
        return dict(row)

    async def update_tier(self, tier_id: int, /, **values: Any) -> Optional[dict]:
        pool = self._require_pool()
        sets, params = [], [tier_id]
        for key, value in values.items():
            if key not in TIER_COLUMNS:
                raise ValueError(f"Unknown ticket tier column: {key}")
            params.append(value)
            sets.append(f"{key} = ${len(params)}")
        row = await pool.fetchrow(
            f"UPDATE ticket_tiers SET {', '.join(sets)} WHERE id = $1 RETURNING *", *params
        )
        return dict(row) if row else None

    async def delete_tier(self, tier_id: int) -> None:
        """Delete a tier and take it out of the other tiers' can_summon."""
        pool = self._require_pool()
        await pool.execute(
            """WITH gone AS (DELETE FROM ticket_tiers WHERE id = $1 RETURNING type_id)
            UPDATE ticket_tiers SET can_summon = array_remove(can_summon, $1)
            WHERE type_id IN (SELECT type_id FROM gone) AND id <> $1 AND $1 = ANY(can_summon)""",
            tier_id,
        )

    async def reorder_tiers(self, type_id: int, tier_ids: list[int]) -> None:
        """Renumber a type's tiers 1, 2, ... in this order (all of them, lowest first)."""
        pool = self._require_pool()
        async with pool.acquire() as conn, conn.transaction():
            # Off the ladder first, since (type_id, position) is unique at every step.
            await conn.execute(
                "UPDATE ticket_tiers SET position = -position WHERE type_id = $1", type_id
            )
            await conn.execute(
                """UPDATE ticket_tiers t SET position = o.n
                FROM unnest($2::bigint[]) WITH ORDINALITY AS o(id, n)
                WHERE t.id = o.id AND t.type_id = $1""",
                type_id,
                tier_ids,
            )

    async def guilds_with_types(self, guild_ids: list[int]) -> list[int]:
        """Which of these servers have any ticket type, so can have ticket staff."""
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT DISTINCT guild_id FROM ticket_types WHERE guild_id = ANY($1::BIGINT[])", guild_ids
        )
        return [r["guild_id"] for r in rows]

    async def staff_roles(self, guild_id: int) -> list[dict]:
        """Every type's staff roles, one row per tier (tier_id NULL for a type without tiers)."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT ty.id AS type_id, ty.staff_role_ids, tr.id AS tier_id,
                COALESCE(tr.role_ids, '{}') AS tier_role_ids
            FROM ticket_types ty LEFT JOIN ticket_tiers tr ON tr.type_id = ty.id
            WHERE ty.guild_id = $1""",
            guild_id,
        )
        return [dict(r) for r in rows]

    async def due_for_reping(self, now: int) -> list[dict]:
        """Escalated tickets their tier hasn't picked up within its re-ping time."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT t.*, tr.name AS tier_name, tr.role_ids AS tier_role_ids, tr.reping_minutes
            FROM tickets t JOIN ticket_tiers tr ON tr.id = t.tier_id
            WHERE t.status = 'open' AND t.tier_pinged_at IS NOT NULL
            AND tr.reping_minutes IS NOT NULL
            AND t.tier_pinged_at < $1 - tr.reping_minutes * 60""",
            now,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    async def due_for_auto_close(self, now: int) -> list[dict]:
        """Tickets waiting on their opener for longer than the type's auto-close hours, by the
        wall clock. The caller discounts time outside support hours."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT t.*, ty.auto_close_hours
            FROM tickets t JOIN ticket_types ty ON ty.id = t.type_id
            WHERE t.status = 'open' AND t.waiting_on = 'opener'
            AND ty.auto_close_hours IS NOT NULL
            AND t.last_activity_at < $1 - ty.auto_close_hours * 3600""",
            now,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    # Closing and reopening

    async def close_ticket(
        self, ticket_id: int, closed_by: int, reason: Optional[str]
    ) -> Optional[dict]:
        """Mark a ticket closed and credit its handlers; None if it was already closed."""
        pool = self._require_pool()
        now = int(time.time())
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """UPDATE tickets SET status = 'closed', closed_at = $2, closed_by = $3,
                close_reason = $4, channel_id = NULL, tier_pinged_at = NULL
                WHERE id = $1 AND status = 'open' RETURNING *""",
                ticket_id,
                now,
                closed_by,
                reason,
            )
            if row is None:
                return None
            await self.credit_handlers(ticket_id, conn)
            await conn.execute(
                """INSERT INTO ticket_events (ticket_id, kind, actor_id, data, created_at)
                VALUES ($1, 'closed', $2, $3, $4)""",
                ticket_id,
                closed_by,
                json.dumps({"reason": reason}),
                now,
            )
        return _decode(row)

    async def reopen_ticket(self, ticket_id: int, channel_id: int, actor_id: int) -> Optional[dict]:
        """Reopen a closed ticket in `channel_id`; None if it isn't closed (any more)."""
        pool = self._require_pool()
        now = int(time.time())
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """UPDATE tickets SET status = 'open', channel_id = $2, closed_at = NULL,
                closed_by = NULL, close_reason = NULL, waiting_on = 'staff', last_activity_at = $3
                WHERE id = $1 AND status = 'closed' RETURNING *""",
                ticket_id,
                channel_id,
                now,
            )
            if row is None:
                return None
            # Kept until the ticket closes again, when it's rebuilt with the new messages.
            await conn.execute(
                "UPDATE ticket_transcripts SET expires_at = NULL WHERE ticket_id = $1", ticket_id
            )
            await conn.execute(
                """INSERT INTO ticket_events (ticket_id, kind, actor_id, created_at)
                VALUES ($1, 'reopened', $2, $3)""",
                ticket_id,
                actor_id,
                now,
            )
        return _decode(row)

    # Events and notes

    async def add_event(
        self, ticket_id: int, kind: str, actor_id: Optional[int], data: Optional[dict] = None
    ) -> None:
        pool = self._require_pool()
        await pool.execute(
            """INSERT INTO ticket_events (ticket_id, kind, actor_id, data, created_at)
            VALUES ($1, $2, $3, $4, $5)""",
            ticket_id,
            kind,
            actor_id,
            _encode("data", data or {}),
            int(time.time()),
        )

    async def list_events(self, ticket_id: int) -> list[dict]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_events WHERE ticket_id = $1 ORDER BY created_at, id", ticket_id
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    async def add_note(self, ticket_id: int, author_id: int, body: str) -> int:
        pool = self._require_pool()
        return await pool.fetchval(
            """INSERT INTO ticket_notes (ticket_id, author_id, body, created_at)
            VALUES ($1, $2, $3, $4) RETURNING id""",
            ticket_id,
            author_id,
            body,
            int(time.time()),
        )

    async def list_notes(self, ticket_id: int) -> list[dict]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_notes WHERE ticket_id = $1 ORDER BY created_at, id", ticket_id
        )
        return [dict(r) for r in rows]

    # Transcripts

    async def save_transcript(
        self, ticket_id: int, guild_id: int, document: dict, expires_at: Optional[int]
    ) -> None:
        """Store (or, after a reopen, replace) a ticket's transcript. A case made from the ticket
        holds it as evidence, unless it's already held."""
        pool = self._require_pool()
        await pool.execute(
            """INSERT INTO ticket_transcripts
            (ticket_id, guild_id, document, created_at, expires_at, held_by_case_number)
            VALUES ($1, $2, $3, $4, $5,
                (SELECT MAX(case_number) FROM mod_cases WHERE source_ticket_id = $1))
            ON CONFLICT (ticket_id) DO UPDATE SET
            document = EXCLUDED.document, created_at = EXCLUDED.created_at,
            expires_at = EXCLUDED.expires_at,
            held_by_case_number = COALESCE(
                ticket_transcripts.held_by_case_number, EXCLUDED.held_by_case_number
            )""",
            ticket_id,
            guild_id,
            _encode("document", document),
            int(time.time()),
            expires_at,
        )

    async def get_transcript(self, ticket_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_transcripts WHERE ticket_id = $1", ticket_id
        )
        return _decode(row)

    async def list_transcripts(
        self,
        guild_id: int,
        *,
        type_ids: Optional[list[int]],
        opener_id: Optional[int] = None,
        handler_id: Optional[int] = None,
        outcome: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        before: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Closed tickets with a transcript, newest number first, each with its retention, rating,
        handler ids and outcome (staff, opener or auto). type_ids None is every type; `before`
        is a ticket number, for paging."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT * FROM (
                SELECT t.*, ty.name AS type_name, ty.emoji AS type_emoji, tr.expires_at,
                    tr.held_by_case_number, tr.deletion_requested_at, r.rating,
                    ARRAY(SELECT user_id FROM ticket_handlers h WHERE h.ticket_id = t.id
                        ORDER BY h.joined_at, h.user_id) AS handler_ids,
                    CASE WHEN EXISTS (SELECT 1 FROM ticket_events e
                            WHERE e.ticket_id = t.id AND e.kind = 'auto_closed') THEN 'auto'
                        WHEN t.closed_by = t.opener_id THEN 'opener' ELSE 'staff' END AS outcome
                FROM ticket_transcripts tr
                JOIN tickets t ON t.id = tr.ticket_id
                JOIN ticket_types ty ON ty.id = t.type_id
                LEFT JOIN ticket_ratings r ON r.ticket_id = t.id
                WHERE t.guild_id = $1 AND t.status = 'closed'  -- tickets (guild_id, number) drives it
                    AND ($2::bigint[] IS NULL OR t.type_id = ANY($2))
                    AND ($3::bigint IS NULL OR t.opener_id = $3)
                    AND ($4::bigint IS NULL OR EXISTS (SELECT 1 FROM ticket_handlers h
                        WHERE h.ticket_id = t.id AND h.user_id = $4))
                    AND ($5::bigint IS NULL OR t.closed_at >= $5)
                    AND ($6::bigint IS NULL OR t.closed_at < $6)
                    AND ($7::int IS NULL OR t.number < $7)
            ) x WHERE ($8::text IS NULL OR outcome = $8)
            ORDER BY number DESC LIMIT $9""",
            guild_id,
            type_ids,
            opener_id,
            handler_id,
            since,
            until,
            before,
            outcome,
            limit,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

    async def hold_transcript(self, ticket_id: int, case_number: Optional[int]) -> bool:
        """Keep a transcript as evidence for a mod case (None releases it)."""
        pool = self._require_pool()
        status = await pool.execute(
            "UPDATE ticket_transcripts SET held_by_case_number = $2 WHERE ticket_id = $1",
            ticket_id,
            case_number,
        )
        return status != "UPDATE 0"

    async def request_transcript_deletion(self, ticket_id: int) -> bool:
        pool = self._require_pool()
        status = await pool.execute(
            """UPDATE ticket_transcripts SET deletion_requested_at = $2
            WHERE ticket_id = $1 AND deletion_requested_at IS NULL""",
            ticket_id,
            int(time.time()),
        )
        return status != "UPDATE 0"

    async def delete_transcript(self, ticket_id: int) -> bool:
        """Delete a transcript and the staff notes that went into it."""
        pool = self._require_pool()
        async with pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "DELETE FROM ticket_transcripts WHERE ticket_id = $1", ticket_id
            )
            await conn.execute("DELETE FROM ticket_notes WHERE ticket_id = $1", ticket_id)
        return status != "DELETE 0"

    async def purge_expired_transcripts(self, now: int) -> int:
        """Hard-delete transcripts past their expiry, and their staff notes, except those held
        for a mod case."""
        pool = self._require_pool()
        return await pool.fetchval(
            """WITH gone AS (
                DELETE FROM ticket_transcripts
                WHERE expires_at IS NOT NULL AND expires_at < $1 AND held_by_case_number IS NULL
                RETURNING ticket_id
            ), notes AS (
                DELETE FROM ticket_notes WHERE ticket_id IN (SELECT ticket_id FROM gone)
            )
            SELECT COUNT(*) FROM gone""",
            now,
        )

    # Webhooks

    async def add_webhook(
        self, guild_id: int, url: str, secret: str, events: list[str], type_id: Optional[int], /
    ) -> dict:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """INSERT INTO ticket_webhooks (guild_id, url, secret, events, type_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING *""",
            guild_id,
            url,
            secret,
            events,
            type_id,
            int(time.time()),
        )
        return dict(row)  # type: ignore[arg-type]

    async def update_webhook(self, guild_id: int, webhook_id: int, /, **values: Any) -> Optional[dict]:
        """Set any of url, secret, events and type_id; None if there's no such webhook."""
        pool = self._require_pool()
        sets, params = [], [guild_id, webhook_id]
        for key, value in values.items():
            if key not in ("url", "secret", "events", "type_id"):
                raise ValueError(f"Unknown ticket webhook column: {key}")
            params.append(value)
            sets.append(f"{key} = ${len(params)}")
        if not sets:
            return await self.get_webhook(guild_id, webhook_id)
        row = await pool.fetchrow(
            f"UPDATE ticket_webhooks SET {', '.join(sets)} WHERE guild_id = $1 AND id = $2 RETURNING *",
            *params,
        )
        return dict(row) if row else None

    async def remove_webhook(self, guild_id: int, webhook_id: int, /) -> bool:
        pool = self._require_pool()
        status = await pool.execute(
            "DELETE FROM ticket_webhooks WHERE guild_id = $1 AND id = $2", guild_id, webhook_id
        )
        return status != "DELETE 0"

    async def get_webhook(self, guild_id: int, webhook_id: int, /) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_webhooks WHERE guild_id = $1 AND id = $2", guild_id, webhook_id
        )
        return dict(row) if row else None

    async def list_webhooks(self, guild_id: int, /) -> list[dict]:
        """A server's webhooks, each with its latest delivery's outcome (`last_*`)."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT w.*, d.event AS last_event, d.attempts AS last_attempts,
                d.status AS last_status, d.latency_ms AS last_latency_ms,
                d.error AS last_error, d.created_at AS last_at
            FROM ticket_webhooks w
            LEFT JOIN LATERAL (
                SELECT * FROM ticket_webhook_deliveries WHERE webhook_id = w.id
                ORDER BY id DESC LIMIT 1
            ) d ON TRUE
            WHERE w.guild_id = $1 ORDER BY w.id""",
            guild_id,
        )
        return [dict(r) for r in rows]

    async def webhooks_for(self, guild_id: int, event: str, type_id: int, /) -> list[dict]:
        """The webhooks subscribed to `event` on tickets of `type_id`."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT * FROM ticket_webhooks WHERE guild_id = $1
            AND (events = '{}' OR $2 = ANY(events)) AND (type_id IS NULL OR type_id = $3)
            ORDER BY id""",
            guild_id,
            event,
            type_id,
        )
        return [dict(r) for r in rows]

    async def add_delivery(self, webhook_id: int, event: str, /) -> int:
        pool = self._require_pool()
        return await pool.fetchval(
            """INSERT INTO ticket_webhook_deliveries (webhook_id, event, created_at)
            VALUES ($1, $2, $3) RETURNING id""",
            webhook_id,
            event,
            int(time.time()),
        )

    async def update_delivery(
        self,
        delivery_id: int,
        attempts: int,
        status: Optional[int],
        latency_ms: Optional[int],
        error: Optional[str],
        /,
    ) -> None:
        pool = self._require_pool()
        await pool.execute(
            """UPDATE ticket_webhook_deliveries
            SET attempts = $2, status = $3, latency_ms = $4, error = $5 WHERE id = $1""",
            delivery_id,
            attempts,
            status,
            latency_ms,
            error,
        )

    async def list_deliveries(self, guild_id: int, webhook_id: int, /, limit: int = 50) -> list[dict]:
        """A webhook's latest deliveries, newest first; none if it isn't this server's."""
        pool = self._require_pool()
        rows = await pool.fetch(
            """SELECT d.* FROM ticket_webhook_deliveries d
            JOIN ticket_webhooks w ON w.id = d.webhook_id
            WHERE w.guild_id = $1 AND d.webhook_id = $2 ORDER BY d.id DESC LIMIT $3""",
            guild_id,
            webhook_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def purge_webhook_deliveries(self, before: int, /) -> int:
        pool = self._require_pool()
        status = await pool.execute(
            "DELETE FROM ticket_webhook_deliveries WHERE created_at < $1", before
        )
        return int(status.split()[-1])

    # Ratings

    async def add_rating(
        self, ticket_id: int, user_id: int, rating: int, comment: Optional[str], /
    ) -> Optional[dict]:
        """Rate a closed ticket, crediting its credited handlers; None if it's already rated."""
        pool = self._require_pool()
        row = await pool.fetchrow(
            """INSERT INTO ticket_ratings (ticket_id, guild_id, user_id, rating, comment, handler_ids, created_at)
            SELECT t.id, t.guild_id, $2, $3, $4,
                ARRAY(SELECT user_id FROM ticket_handlers WHERE ticket_id = t.id AND credited ORDER BY joined_at, user_id),
                $5
            FROM tickets t WHERE t.id = $1
            ON CONFLICT (ticket_id) DO NOTHING RETURNING *""",
            ticket_id,
            user_id,
            rating,
            comment,
            int(time.time()),
        )
        return dict(row) if row else None

    async def get_rating(self, ticket_id: int, /) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow("SELECT * FROM ticket_ratings WHERE ticket_id = $1", ticket_id)
        return dict(row) if row else None

    # Stats

    async def stats(
        self, guild_id: int, type_ids: Optional[list[int]], since: int, now: int, /
    ) -> dict[str, list[dict]]:
        """What the dashboard's ticket stats are built from, for tickets of `type_ids` (None: all).

        tickets: every open ticket and every one opened or closed since `since`, with its rating
            and whether it was ever escalated or auto-closed.
        stages:  per type and stage, how many tickets opened since `since` reached it and their
            average time in it (summed per ticket; an open ticket's current stage counts until now).
        staff:   per user and kind, distinct tickets since `since`: claimed (claims and assigns),
            handled (credited on tickets closed since), assisted (credited without claiming) and
            escalated_from (escalations they made).
        csat:    per credited handler, their ratings on tickets closed since `since`.
        """
        pool = self._require_pool()
        scope = "t.guild_id = $1 AND ($2::bigint[] IS NULL OR t.type_id = ANY($2))"
        args = (guild_id, type_ids, since)
        tickets = await pool.fetch(
            f"""SELECT t.id, t.type_id, t.tier_id, t.status, t.assignee_id, t.waiting_on, t.opened_at,
                t.first_response_at, t.closed_at, r.rating,
                EXISTS (SELECT 1 FROM ticket_events e WHERE e.ticket_id = t.id AND e.kind = 'escalated') AS escalated,
                EXISTS (SELECT 1 FROM ticket_events e WHERE e.ticket_id = t.id AND e.kind = 'auto_closed') AS auto_closed
            FROM tickets t LEFT JOIN ticket_ratings r ON r.ticket_id = t.id
            WHERE {scope} AND (t.status = 'open' OR t.opened_at >= $3 OR t.closed_at >= $3)""",
            *args,
        )
        # A stage event says where a ticket went and where it was, so the first one also dates the
        # stage it opened at; a ticket never moved spent its whole life at its stage.
        # ponytail: a transfer resets the stage without an event, so its old type's stages count here.
        stages = await pool.fetch(
            f"""WITH scoped AS (
                SELECT t.id, t.type_id, t.stage, t.opened_at, COALESCE(t.closed_at, $4) AS ended
                FROM tickets t WHERE {scope} AND t.opened_at >= $3
            ), moves AS (
                SELECT s.id, s.type_id, s.opened_at, s.ended, e.data->>'from' AS from_stage,
                    e.data->>'to' AS to_stage, e.created_at,
                    ROW_NUMBER() OVER w AS n, LEAD(e.created_at) OVER w AS next_at
                FROM scoped s JOIN ticket_events e ON e.ticket_id = s.id AND e.kind = 'stage'
                WINDOW w AS (PARTITION BY s.id ORDER BY e.created_at, e.id)
            ), spans AS (
                SELECT type_id, id, from_stage AS stage, created_at - opened_at AS seconds
                FROM moves WHERE n = 1 AND from_stage IS NOT NULL
                UNION ALL
                SELECT type_id, id, to_stage, COALESCE(next_at, ended) - created_at FROM moves
                UNION ALL
                SELECT type_id, id, stage, ended - opened_at FROM scoped s
                WHERE stage IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM ticket_events e WHERE e.ticket_id = s.id AND e.kind = 'stage')
            )
            SELECT type_id, stage, COUNT(*) AS reached, AVG(seconds)::float AS avg_seconds
            FROM (SELECT type_id, id, stage, SUM(seconds) AS seconds FROM spans GROUP BY type_id, id, stage) x
            GROUP BY type_id, stage""",
            *args,
            now,
        )
        staff = await pool.fetch(
            f"""SELECT user_id, kind, COUNT(DISTINCT ticket_id) AS n FROM (
                SELECT (e.data->>'assignee')::bigint AS user_id, 'claimed' AS kind, t.id AS ticket_id
                FROM ticket_events e JOIN tickets t ON t.id = e.ticket_id
                WHERE {scope} AND e.kind IN ('claimed', 'assigned') AND e.created_at >= $3
                UNION ALL
                SELECT e.actor_id, 'escalated_from', t.id
                FROM ticket_events e JOIN tickets t ON t.id = e.ticket_id
                WHERE {scope} AND e.kind = 'escalated' AND e.created_at >= $3
                UNION ALL
                SELECT h.user_id, k.kind, t.id
                FROM ticket_handlers h JOIN tickets t ON t.id = h.ticket_id,
                    LATERAL (VALUES ('handled'), ('assisted')) k (kind)
                WHERE {scope} AND h.credited AND t.status = 'closed' AND t.closed_at >= $3
                    AND (k.kind = 'handled' OR h.role = 'assisted')
            ) x WHERE user_id IS NOT NULL GROUP BY user_id, kind""",
            *args,
        )
        csat = await pool.fetch(
            f"""SELECT u.user_id, AVG(r.rating)::float AS avg, COUNT(*) AS n
            FROM ticket_ratings r JOIN tickets t ON t.id = r.ticket_id, unnest(r.handler_ids) u (user_id)
            WHERE {scope} AND t.status = 'closed' AND t.closed_at >= $3 GROUP BY u.user_id""",
            *args,
        )
        return {
            "tickets": [dict(r) for r in tickets],
            "stages": [dict(r) for r in stages],
            "staff": [dict(r) for r in staff],
            "csat": [dict(r) for r in csat],
        }

    # Blacklist

    async def blacklist_add(
        self, guild_id: int, user_id: int, moderator_id: int, reason: Optional[str]
    ) -> bool:
        pool = self._require_pool()
        status = await pool.execute(
            """INSERT INTO ticket_blacklist (guild_id, user_id, moderator_id, reason, created_at)
            VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING""",
            guild_id,
            user_id,
            moderator_id,
            reason,
            int(time.time()),
        )
        return status != "INSERT 0 0"

    async def blacklist_remove(self, guild_id: int, user_id: int) -> bool:
        pool = self._require_pool()
        status = await pool.execute(
            "DELETE FROM ticket_blacklist WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return status != "DELETE 0"

    async def list_blacklist(self, guild_id: int, limit: int) -> list[dict]:
        """Who can't open tickets, newest first."""
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT * FROM ticket_blacklist WHERE guild_id = $1 ORDER BY created_at DESC, user_id DESC LIMIT $2",
            guild_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def get_blacklist_entry(self, guild_id: int, user_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_blacklist WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return dict(row) if row else None
