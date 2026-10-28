"""Pin the /ticket options' Discord-facing choices and defaults to the values the code stores."""

import inspect

import arc
import hikari
import pytest

from serenity.extensions import modules as modules_ext
from serenity.extensions import tickets


def choices(command, option: str) -> dict[str, str]:
    return {c.name: c.value for c in command.options[option].to_command_option().choices or []}


def default(command, option: str):
    return inspect.signature(command.callback).parameters[option].default


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TICKETS_GUILDS", "1")
    client = arc.GatewayClient(hikari.GatewayBot("MTAzMDMwMjk0MDcyMTM4MzQ2OQ.Gx.x"))
    client.load_extension("serenity.extensions.tickets")
    yield client
    client.unload_extension("serenity.extensions.tickets")


def test_claim_modes_map_to_stored_values():
    # Stored values are the ticket_types.claim_mode CHECK; the default matches the column's.
    assert choices(tickets.type_create, "claim_mode") == {
        "First staff reply": "reply",
        "Claim button": "button",
        "Both": "both",
    }
    assert default(tickets.type_create, "claim_mode") == "reply"
    assert choices(tickets.type_edit, "claim_mode") == choices(tickets.type_create, "claim_mode")


@pytest.mark.asyncio
async def test_module_names_hide_tickets_outside_the_allowlist(monkeypatch):
    from unittest.mock import MagicMock

    from serenity.core.modules import ModuleType

    monkeypatch.setenv("TICKETS_GUILDS", "1, 2")
    allowed = await modules_ext.module_names(MagicMock(guild_id=2, focused_value=""))
    other = await modules_ext.module_names(MagicMock(guild_id=3, focused_value=""))
    assert set(allowed.values()) == {m.value for m in ModuleType}
    assert set(other.values()) == {m.value for m in ModuleType} - {"tickets"}
    for command in (modules_ext.enable_module, modules_ext.disable_module):
        assert command.options["module"].autocomplete_with is modules_ext.module_names


def test_tickets_arent_loaded_without_an_allowlist(monkeypatch):
    monkeypatch.delenv("TICKETS_GUILDS", raising=False)
    client = arc.GatewayClient(hikari.GatewayBot("MTAzMDMwMjk0MDcyMTM4MzQ2OQ.Gx.x"))
    client.load_extension("serenity.extensions.tickets")
    assert "tickets" not in client.plugins
    client.unload_extension("serenity.extensions.tickets")


def test_panel_styles_map_to_stored_values():
    # Stored values are the ticket_panels.style CHECK.
    assert choices(tickets.panel_post, "style") == {"Buttons": "buttons", "Dropdown": "menu"}
    assert default(tickets.panel_post, "style") == "buttons"
    assert choices(tickets.panel_edit, "style") == choices(tickets.panel_post, "style")


def test_module_hook_gates_every_ticket_command(client):
    """The plugin-wide require_module hook reaches every /ticket command, subgroups included."""
    # The plugin the client loaded: unload_extension drops the module, so a reload is a new one.
    plugin = client.plugins["tickets"]
    commands = list(plugin.walk_commands(hikari.CommandType.SLASH, callable_only=True))
    assert commands
    for command in commands:
        assert "module_check_hook" in [h.__name__ for h in command._resolve_hooks()], command.name
