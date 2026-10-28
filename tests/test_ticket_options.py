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


@pytest.fixture(scope="module")
def client():
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


def test_module_choices_cover_every_module():
    from serenity.core.modules import ModuleType

    for command in (modules_ext.enable_module, modules_ext.disable_module):
        assert set(choices(command, "module").values()) == {m.value for m in ModuleType}


def test_module_hook_gates_every_ticket_command(client):
    """The plugin-wide require_module hook reaches every /ticket command, subgroups included."""
    commands = list(tickets.plugin.walk_commands(hikari.CommandType.SLASH, callable_only=True))
    assert commands
    for command in commands:
        assert "module_check_hook" in [h.__name__ for h in command._resolve_hooks()], command.name
