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
            """SELECT t.*, array_agg(other.field_key ORDER BY other.field_key) AS linked_by
            FROM ticket_identity mine
            JOIN ticket_identity other ON other.guild_id = mine.guild_id
                AND other.field_key = mine.field_key AND other.value = mine.value
                AND other.ticket_id <> mine.ticket_id
            JOIN tickets t ON t.id = other.ticket_id
            WHERE mine.ticket_id = $1
            GROUP BY t.id ORDER BY t.opened_at DESC, t.id DESC LIMIT $2""",
            ticket_id,
            limit,
        )
        return [_decode(r) for r in rows]  # type: ignore[misc]

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
            "tier_id", "tier_pinged_at",
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

    async def create_tier(self, type_id: int, /, **values: Any) -> dict:
        pool = self._require_pool()
        columns = ["type_id", "created_at"]
        params: list[Any] = [type_id, int(time.time())]
        for key, value in values.items():
            if key not in TIER_COLUMNS:
                raise ValueError(f"Unknown ticket tier column: {key}")
            columns.append(key)
            params.append(value)
        placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
        row = await pool.fetchrow(
            f"INSERT INTO ticket_tiers ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *",
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
        pool = self._require_pool()
        await pool.execute("DELETE FROM ticket_tiers WHERE id = $1", tier_id)

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
        """Store (or, after a reopen, replace) a ticket's transcript."""
        pool = self._require_pool()
        await pool.execute(
            """INSERT INTO ticket_transcripts (ticket_id, guild_id, document, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (ticket_id) DO UPDATE SET
            document = EXCLUDED.document, created_at = EXCLUDED.created_at,
            expires_at = EXCLUDED.expires_at""",
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

    async def get_blacklist_entry(self, guild_id: int, user_id: int) -> Optional[dict]:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT * FROM ticket_blacklist WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return dict(row) if row else None
