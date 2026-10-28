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
PANEL_COLOR = hikari.Color(0x5865F2)


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
        # Posted panel messages, so a deleted message only costs a query if it was a panel.
        self.panel_messages: set[int] = set()

    async def load(self) -> None:
        self.open_channels = set(await self.repo.open_ticket_channels())
        self.panel_messages = set(await self.repo.panel_message_ids())

    async def resolve_type(self, guild_id: int, name: str) -> dict:
        ticket_type = await self.repo.get_type_by_name(guild_id, name)
        if ticket_type is None:
            raise TicketError(f"There's no ticket type called **{name}**.")
        return ticket_type

    def type_label(self, ticket_type: dict) -> str:
        emoji: Optional[str] = ticket_type["emoji"]
        return f"{emoji} {ticket_type['name']}" if emoji else ticket_type["name"]

    # Panels

    def build_panel(
        self, panel: dict, types: list[dict]
    ) -> tuple[hikari.Embed, list[hikari.api.MessageActionRowBuilder]]:
        """The panel message: one button per type (5 per row), or a dropdown of them."""
        embed = hikari.Embed(
            title=panel["title"], description=panel["description"], color=PANEL_COLOR
        )
        rows: list[hikari.api.MessageActionRowBuilder] = []
        if panel["style"] == "menu":
            menu = self.rest.build_message_action_row().add_text_menu(
                f"ticket:menu:{panel['id']}", placeholder="Choose what you need help with"
            )
            for t in types:
                menu.add_option(
                    t["name"], str(t["id"]), description=t["description"] or hikari.UNDEFINED,
                    emoji=t["emoji"] or hikari.UNDEFINED,
                )
            rows.append(menu.parent)
        else:
            for i, t in enumerate(types):
                if i % 5 == 0:
                    rows.append(self.rest.build_message_action_row())
                rows[-1].add_interactive_button(
                    hikari.ButtonStyle.PRIMARY, f"ticket:open:{t['id']}",
                    label=t["name"], emoji=t["emoji"] or hikari.UNDEFINED,
                )
        return embed, rows

    async def _panel_types(self, panel: dict) -> list[dict]:
        types = {t["id"]: t for t in await self.repo.list_types(panel["guild_id"])}
        return [types[i] for i in panel["type_ids"] if i in types]

    async def post_panel(self, panel: dict) -> dict:
        """Send the panel to its channel, or edit it in place if it's already there."""
        types = await self._panel_types(panel)
        if not types:
            raise TicketError("A panel needs at least one ticket type.")
        embed, rows = self.build_panel(panel, types)
        try:
            if panel["message_id"]:
                try:
                    await self.rest.edit_message(
                        panel["channel_id"], panel["message_id"], embed=embed, components=rows
                    )
                    return panel
                except hikari.NotFoundError:
                    pass  # deleted while we weren't looking: post a new one
            message = await self.rest.create_message(
                panel["channel_id"], embed=embed, components=rows
            )
        except hikari.ForbiddenError:
            raise TicketError(
                f"I can't post in <#{panel['channel_id']}>. Check my View Channel, "
                "Send Messages and Embed Links permissions there."
            ) from None
        self.panel_messages.add(message.id)
        return await self.repo.update_panel(  # type: ignore[return-value]
            panel["guild_id"], panel["id"], message_id=message.id
        )

    async def delete_panel(self, panel: dict) -> None:
        if panel["message_id"]:
            try:
                await self.rest.delete_message(panel["channel_id"], panel["message_id"])
            except (hikari.NotFoundError, hikari.ForbiddenError):
                pass
        self.panel_messages.discard(panel["message_id"])
        await self.repo.delete_panel(panel["guild_id"], panel["id"])

    async def panel_message_deleted(self, message_id: int) -> None:
        if message_id in self.panel_messages:
            self.panel_messages.discard(message_id)
            await self.repo.forget_panel_message(message_id)

    async def refresh_panels(self, guild_id: int, type_id: Optional[int] = None) -> None:
        """Re-render posted panels after a type they show changed."""
        for panel in await self.repo.list_panels(guild_id):
            if panel["message_id"] and (type_id is None or type_id in panel["type_ids"]):
                try:
                    await self.post_panel(panel)
                except (TicketError, hikari.HTTPError) as exc:  # one broken panel can't stop the rest
                    logger.warning(f"Couldn't refresh ticket panel {panel['id']}: {exc}")
