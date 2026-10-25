"""The ignore/unignore commands must send the "*" sentinel when "all" is picked.

log_ignores.log_type has a CHECK that rejects "all", so this pins the Discord-facing contract
(arc builds choices from the {name: value} mapping), not just the schema.
"""

import inspect

import pytest

import serenity.extensions.logging as logging_ext


@pytest.mark.parametrize(
    "command", ["ignore_channel", "unignore_channel", "ignore_user", "unignore_user"]
)
def test_all_choice_sends_star(command):
    option = getattr(logging_ext, command).options["log_type"].to_command_option()
    choices = {c.name: c.value for c in option.choices or []}

    assert choices["all"] == "*"
    assert all(name == value for name, value in choices.items() if name != "all")

    # Leaving the option out (the common case) must also send "*"
    default = inspect.signature(getattr(logging_ext, command).callback).parameters["log_type"]
    assert default.default == "*"


def test_log_types_match_the_schema_and_setup_channel_names():
    """LOG_TYPES drives /logging setup's channel names and must stay within the DB CHECK."""
    import re
    from pathlib import Path

    from serenity.services.logging_service import CHANNEL_NAMES, LOG_TYPES

    assert CHANNEL_NAMES == {
        "member": "member-log",
        "message": "message-log",
        "voice": "voice-log",
        "server": "server-log",
        "mod": "mod-log",
    }
    schema = Path("serenity/database/migrations/001_initial.sql").read_text()
    table = re.search(r"CREATE TABLE log_channels \((.*?)\n\);", schema, re.S)
    assert table is not None
    check = re.search(r"CHECK \(log_type IN \(([^)]*)\)\)", table.group(1))
    assert check is not None
    assert [t.strip(" '") for t in check.group(1).split(",")] == LOG_TYPES
