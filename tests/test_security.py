"""Security module: anti-nuke alerts (#152)."""

from unittest.mock import AsyncMock, MagicMock

import hikari
import pytest

from serenity.core.modules import ModuleConfig, ModuleType
from serenity.services.security_service import SecurityService

GUILD, ME, OWNER, MOD, ROLE = 1, 10, 11, 20, 70


def entry(action=hikari.AuditLogEventType.CHANNEL_DELETE, actor=MOD, target=99) -> hikari.AuditLogEntry:
    return hikari.AuditLogEntry(
        app=MagicMock(),
        id=hikari.Snowflake(1 << 40),
        guild_id=hikari.Snowflake(GUILD),
        target_id=hikari.Snowflake(target),
        changes=[],
        user_id=hikari.Snowflake(actor),
        action_type=action,
        options=None,
        reason=None,
    )


def service(enabled=True, settings=None, roles=()) -> SecurityService:
    modules = AsyncMock()
    modules.get_module_config.return_value = ModuleConfig(GUILD, ModuleType.SECURITY, enabled, settings or {}, 0)
    cache = MagicMock()
    cache.get_guild.return_value = MagicMock(owner_id=OWNER)
    cache.get_guild.return_value.name = "Test"
    cache.get_member.return_value = MagicMock(role_ids=list(roles))
    svc = SecurityService(modules, AsyncMock(), cache, AsyncMock())
    return svc


async def burst(svc, n, **kwargs):
    for i in range(n):
        await svc.on_entry(entry(target=100 + i, **kwargs), me=ME)


@pytest.mark.asyncio
async def test_alerts_once_at_the_threshold():
    svc = service()
    await burst(svc, 2)
    svc._logging.log_security_alert.assert_not_called()

    await burst(svc, 4)  # 3rd channel delete crosses the default of 3, then the cooldown holds
    svc._logging.log_security_alert.assert_called_once()
    embed = svc._logging.log_security_alert.call_args.kwargs["embed"]
    assert "**3** channels deleted" in embed.description
    svc._rest.create_dm_channel.assert_awaited_once_with(OWNER)


@pytest.mark.asyncio
async def test_kinds_are_counted_separately():
    svc = service()
    await burst(svc, 2)
    await burst(svc, 2, action=hikari.AuditLogEventType.ROLE_DELETE)
    svc._logging.log_security_alert.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "svc, actor",
    [
        (service(enabled=False), MOD),  # module off
        (service(settings={"antinuke": False}), MOD),
        (service(), OWNER),
        (service(), ME),
        (service(settings={"trusted_users": [str(MOD)]}), MOD),
        (service(settings={"trusted_roles": [str(ROLE)]}, roles=[ROLE]), MOD),
        (service(settings={"thresholds": {"channels": 10}}), MOD),
    ],
)
async def test_no_alert(svc, actor):
    await burst(svc, 5, actor=actor)
    svc._logging.log_security_alert.assert_not_called()
