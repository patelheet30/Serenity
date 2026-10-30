"""/security: the Security module's settings (#152, #153)."""

from __future__ import annotations

import arc
import hikari

from serenity.core.hooks import require_module
from serenity.core.modules import ModuleNotEnabledError, ModuleType
from serenity.services.security_service import KINDS, SecurityService, settings_of
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("security_commands", default_permissions=hikari.Permissions.MANAGE_GUILD)
security = plugin.include_slash_group("security", "Anti-nuke and dangerous-change alerts.")


@plugin.set_error_handler
async def on_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            "❌ The **Security** module is not enabled. Use `/module enable security` first.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return
    raise exc


async def _update(ctx: arc.GatewayContext, svc: SecurityService, **changes: object) -> dict:
    config = await svc.modules.get_module_config(ctx.guild_id, ModuleType.SECURITY)  # type: ignore[arg-type]
    raw = {**config.settings, **changes}
    await svc.modules.update_module_settings(ctx.guild_id, ModuleType.SECURITY, raw)  # type: ignore[arg-type]
    return settings_of(raw)


@security.include
@arc.with_hook(require_module(ModuleType.SECURITY))
@arc.slash_subcommand("status", "Show the Security settings.")
async def status(ctx: arc.GatewayContext, svc: SecurityService = arc.inject()) -> None:
    settings = settings_of(
        (await svc.modules.get_module_config(ctx.guild_id, ModuleType.SECURITY)).settings  # type: ignore[arg-type]
    )
    embed = hikari.Embed(title="🔒 Security", color=hikari.Color(0x5865F2))
    embed.add_field(
        "Anti-nuke",
        ("✅ On" if settings["antinuke"] else "❌ Off")
        + "\n"
        + "\n".join(f"{KINDS[k][2].capitalize()}: **{n}**/min" for k, n in settings["thresholds"].items()),
        inline=True,
    )
    embed.add_field("Dangerous-change warnings", "✅ On" if settings["warnings"] else "❌ Off", inline=True)
    trusted = [f"<@{u}>" for u in settings["trusted_users"]] + [f"<@&{r}>" for r in settings["trusted_roles"]]
    embed.add_field("Trusted (never counted)", " ".join(trusted) or "*Only the owner*", inline=False)
    perms = ctx.interaction.app_permissions
    if perms is not None and not perms & hikari.Permissions.VIEW_AUDIT_LOG:
        embed.add_field("⚠️ View Audit Log", "I need **View Audit Log** to see who does what.", inline=False)
    await ctx.respond(embed=embed)


@security.include
@arc.with_hook(require_module(ModuleType.SECURITY))
@arc.slash_subcommand("antinuke", "Turn anti-nuke alerts on or off.")
async def antinuke(
    ctx: arc.GatewayContext,
    enabled: arc.Option[bool, arc.BoolParams("Alert on bursts of destructive actions")],
    svc: SecurityService = arc.inject(),
) -> None:
    await _update(ctx, svc, antinuke=enabled)
    await ctx.respond(f"✅ Anti-nuke alerts are now **{'on' if enabled else 'off'}**.")


@security.include
@arc.with_hook(require_module(ModuleType.SECURITY))
@arc.slash_subcommand("warnings", "Turn dangerous-change warnings on or off.")
async def warnings(
    ctx: arc.GatewayContext,
    enabled: arc.Option[bool, arc.BoolParams("Warn about admin roles, new bots, webhooks, lowered security")],
    svc: SecurityService = arc.inject(),
) -> None:
    await _update(ctx, svc, warnings=enabled)
    await ctx.respond(f"✅ Dangerous-change warnings are now **{'on' if enabled else 'off'}**.")


@security.include
@arc.with_hook(require_module(ModuleType.SECURITY))
@arc.slash_subcommand("threshold", "How many of an action in one minute sends an alert.")
async def threshold(
    ctx: arc.GatewayContext,
    action: arc.Option[
        str, arc.StrParams("The action", choices={KINDS[k][2].capitalize(): k for k in KINDS})
    ],
    count: arc.Option[int, arc.IntParams("Alert at this many per minute", min=2, max=50)],
    svc: SecurityService = arc.inject(),
) -> None:
    current = settings_of(
        (await svc.modules.get_module_config(ctx.guild_id, ModuleType.SECURITY)).settings  # type: ignore[arg-type]
    )
    await _update(ctx, svc, thresholds={**current["thresholds"], action: count})
    await ctx.respond(f"✅ Alert at **{count}** {KINDS[action][2]} per minute.")


async def _trust(ctx: arc.GatewayContext, svc: SecurityService, user, role, trust: bool) -> None:
    if not user and not role:
        await ctx.respond("❌ Pick a user or a role.", flags=hikari.MessageFlag.EPHEMERAL)
        return
    current = settings_of(
        (await svc.modules.get_module_config(ctx.guild_id, ModuleType.SECURITY)).settings  # type: ignore[arg-type]
    )
    users = {str(u) for u in current["trusted_users"]}
    roles = {str(r) for r in current["trusted_roles"]}
    for ids, target in ((users, user), (roles, role)):
        if target:
            (ids.add if trust else ids.discard)(str(target.id))
    await _update(ctx, svc, trusted_users=sorted(users), trusted_roles=sorted(roles))
    who = " ".join(t.mention for t in (user, role) if t)
    await ctx.respond(
        f"✅ {who} {'is now trusted: never counted by anti-nuke' if trust else 'is no longer trusted'}."
    )


@security.include
@arc.with_hook(require_module(ModuleType.SECURITY))
@arc.slash_subcommand("trust", "Never count a user's or role's actions for anti-nuke.")
async def trust(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User | None, arc.UserParams("A user to trust")] = None,
    role: arc.Option[hikari.Role | None, arc.RoleParams("A role to trust")] = None,
    svc: SecurityService = arc.inject(),
) -> None:
    await _trust(ctx, svc, user, role, True)


@security.include
@arc.with_hook(require_module(ModuleType.SECURITY))
@arc.slash_subcommand("untrust", "Count a user's or role's actions for anti-nuke again.")
async def untrust(
    ctx: arc.GatewayContext,
    user: arc.Option[hikari.User | None, arc.UserParams("A user to stop trusting")] = None,
    role: arc.Option[hikari.Role | None, arc.RoleParams("A role to stop trusting")] = None,
    svc: SecurityService = arc.inject(),
) -> None:
    await _trust(ctx, svc, user, role, False)


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Security commands plugin loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Security commands plugin unloaded.")
