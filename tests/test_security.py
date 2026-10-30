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


# --- Dangerous-change warnings (#153) ----------------------------------------------------------

P = hikari.Permissions
K = hikari.AuditLogChangeKey


def change_entry(action, changes, target=99, actor=MOD) -> hikari.AuditLogEntry:
    e = entry(action=action, actor=actor, target=target)
    e.changes = [hikari.AuditLogChange(key=k, old_value=o, new_value=n) for k, o, n in changes]
    return e


async def warned(svc, e) -> str | None:
    await svc.check_dangerous(e, me=ME)
    call = svc._logging.log_security_alert.call_args
    return call and call.kwargs["embed"].description


@pytest.mark.asyncio
async def test_role_gaining_admin_warns():
    e = change_entry(hikari.AuditLogEventType.ROLE_UPDATE, [(K.PERMISSIONS, P.SEND_MESSAGES, P.SEND_MESSAGES | P.ADMINISTRATOR)])
    assert "gained **Administrator**" in await warned(service(), e)


@pytest.mark.asyncio
async def test_everyone_role_is_named():
    e = change_entry(hikari.AuditLogEventType.ROLE_UPDATE, [(K.PERMISSIONS, P.NONE, P.MENTION_ROLES)], target=GUILD)
    assert (await warned(service(), e)).startswith("@everyone gained")


@pytest.mark.asyncio
async def test_powerful_role_given():
    svc = service()
    svc._cache.get_role.return_value = MagicMock(permissions=P.BAN_MEMBERS)
    role = MagicMock(id=hikari.Snowflake(ROLE))
    e = change_entry(hikari.AuditLogEventType.MEMBER_ROLE_UPDATE, [(K.ADD_ROLE_TO_MEMBER, None, {ROLE: role})], target=40)
    assert f"<@40> was given <@&{ROLE}>" in await warned(svc, e)


@pytest.mark.asyncio
async def test_lowered_and_raised_security():
    lower = change_entry(hikari.AuditLogEventType.GUILD_UPDATE, [(K.VERIFICATION_LEVEL, hikari.GuildVerificationLevel.HIGH, hikari.GuildVerificationLevel.LOW)], target=GUILD)
    assert "Verification level" in await warned(service(), lower)

    raise_ = change_entry(hikari.AuditLogEventType.GUILD_UPDATE, [(K.MFA_LEVEL, hikari.GuildMFALevel.NONE, hikari.GuildMFALevel.ELEVATED)], target=GUILD)
    assert await warned(service(), raise_) is None


@pytest.mark.asyncio
async def test_bot_and_webhook():
    assert "was added" in await warned(service(), entry(action=hikari.AuditLogEventType.BOT_ADD))
    hook = change_entry(hikari.AuditLogEventType.WEBHOOK_CREATE, [(K.NAME, None, "Spidey"), (K.CHANNEL_ID, None, 5)])
    assert "**Spidey**" in await warned(service(), hook)


@pytest.mark.asyncio
async def test_no_warning_when_off_harmless_or_serenity():
    admin = [(K.PERMISSIONS, P.NONE, P.ADMINISTRATOR)]
    assert await warned(service(settings={"warnings": False}), change_entry(hikari.AuditLogEventType.ROLE_UPDATE, admin)) is None
    assert await warned(service(enabled=False), change_entry(hikari.AuditLogEventType.ROLE_UPDATE, admin)) is None
    assert await warned(service(), change_entry(hikari.AuditLogEventType.ROLE_UPDATE, admin, actor=ME)) is None
    harmless = [(K.PERMISSIONS, P.NONE, P.SEND_MESSAGES)]
    assert await warned(service(), change_entry(hikari.AuditLogEventType.ROLE_UPDATE, harmless)) is None


@pytest.mark.asyncio
async def test_owner_changes_log_without_a_dm():
    svc = service()
    await svc.check_dangerous(entry(action=hikari.AuditLogEventType.BOT_ADD, actor=OWNER), me=ME)
    svc._logging.log_security_alert.assert_called_once()
    svc._rest.create_dm_channel.assert_not_called()
