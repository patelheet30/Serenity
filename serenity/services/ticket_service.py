from __future__ import annotations

import re
from typing import Optional, Sequence

import hikari

from serenity.core.modules import ModuleManager
from serenity.database.ticket_repository import TicketRepository
from serenity.utils.errors import SerenityError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

MAX_FIELDS = 5  # a Discord modal holds at most five inputs
MAX_LABEL = 45  # and each input's label is at most 45 characters


class TicketError(SerenityError):
    """A ticket action the member can't do; the message is shown to them as is."""


def parse_questions(text: str) -> list[dict]:
    """`/ticket type` questions ("Order number | What went wrong?") as form fields.

    ponytail: every field is a required paragraph; per-field style and optional fields come
    with the dashboard's type editor.
    """
    labels = [q.strip() for q in text.split("|") if q.strip()]
    if len(labels) > MAX_FIELDS:
        raise TicketError(f"A form can have at most {MAX_FIELDS} questions.")
    fields: list[dict] = []
    for label in labels:
        if len(label) > MAX_LABEL:
            raise TicketError(f"Questions can be at most {MAX_LABEL} characters: “{label}”.")
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:32] or f"q{len(fields) + 1}"
        while key in {f["key"] for f in fields}:
            key += "_"
        fields.append(
            {"key": key, "label": label, "style": "paragraph", "required": True, "max_length": 1000}
        )
    return fields


def is_staff(
    ticket_type: dict, role_ids: Sequence[int], permissions: hikari.Permissions
) -> bool:
    """Staff of a type: one of its staff roles, or Manage Server."""
    if permissions & (hikari.Permissions.MANAGE_GUILD | hikari.Permissions.ADMINISTRATOR):
        return True
    return any(role in ticket_type["staff_role_ids"] for role in role_ids)


class TicketService:
    """Ticket flows shared by the slash commands and the panel/ticket buttons."""

    def __init__(
        self,
        repo: TicketRepository,
        rest: hikari.api.RESTClient,
        cache: hikari.api.Cache,
        modules: ModuleManager,
    ) -> None:
        self.repo = repo
        self.rest = rest
        self.cache = cache
        self.modules = modules
        # Channels of open tickets, so the message listener can skip every other message
        # without a query. ponytail: one bot process; shard across processes and this moves to the DB.
        self.open_channels: set[int] = set()

    async def load(self) -> None:
        self.open_channels = set(await self.repo.open_ticket_channels())

    async def resolve_type(self, guild_id: int, name: str) -> dict:
        ticket_type = await self.repo.get_type_by_name(guild_id, name)
        if ticket_type is None:
            raise TicketError(f"There's no ticket type called **{name}**.")
        return ticket_type

    def type_label(self, ticket_type: dict) -> str:
        emoji: Optional[str] = ticket_type["emoji"]
        return f"{emoji} {ticket_type['name']}" if emoji else ticket_type["name"]
