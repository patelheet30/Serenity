import arc
import hikari

from serenity.core.modules import ModuleManager, ModuleNotEnabledError, ModuleType
from serenity.utils.errors import ConfigurationError
from serenity.utils.errors import PermissionError as SerenityPermissionError
from serenity.utils.logging import get_logger

logger = get_logger(__name__)

plugin = arc.GatewayPlugin("module_commands", default_permissions=hikari.Permissions.MANAGE_GUILD)


@plugin.set_error_handler
async def on_command_error(ctx: arc.GatewayContext, exc: Exception) -> None:
    """Handle errors for module commands."""
    if isinstance(exc, (SerenityPermissionError, ConfigurationError)):
        await ctx.respond(
            f"❌ {str(exc)}",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    if isinstance(exc, ModuleNotEnabledError):
        await ctx.respond(
            f"❌ The module '{exc.module.value}' is not enabled in this server. "
            f"Please enable it using `/module enable {exc.module.value}`.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    raise exc


module = plugin.include_slash_group("module", "Module management commands.")


@module.include
@arc.slash_subcommand("list", "List all available modules and their status.")
async def list_modules(
    ctx: arc.GatewayContext,
    manager: ModuleManager = arc.inject(),
) -> None:
    """List all available modules and their status."""
    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    try:
        enabled_modules = await manager.get_enabled_modules(ctx.guild_id)
        enabled_set = set(enabled_modules)

        embed = hikari.Embed(
            title="📦 Serenity Modules",
            description="Available modules and their current status",
            color=hikari.Color(0x5865F2),
        )

        core_modules = [ModuleType.SLOWMODE, ModuleType.MODERATION, ModuleType.LOGGING]
        core_status = []

        for mod in core_modules:
            status = "✅ Enabled" if mod in enabled_set else "❌ Disabled"
            deps = manager.get_dependencies(mod)
            dep_text = f" (Require: {', '.join(dep.value for dep in deps)})" if deps else ""
            core_status.append(f"**{mod.value}**: {status}{dep_text}")

        embed.add_field(
            name="Core Modules",
            value="\n".join(core_status) if core_status else "No core modules",
            inline=False,
        )

        embed.add_field(
            name="Future Modules",
            value="More modules coming soon!",
            inline=False,
        )

        embed.set_footer(
            text="Use /module enable <module> to enable a module | /module disable <module> to disable"
        )
        await ctx.respond(embed=embed)
    except Exception as e:
        logger.error(f"Failed to list modules for guild {ctx.guild_id}: {e}", exc_info=True)
        await ctx.respond(
            "❌ An error occurred while listing modules. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@module.include
@arc.slash_subcommand("enable", "Enable a module in this server.")
async def enable_module(
    ctx: arc.GatewayContext,
    module: arc.Option[
        str,
        arc.StrParams(
            "The module to enable.",
            choices=[
                hikari.CommandChoice(name="Slowmode", value="slowmode"),
                hikari.CommandChoice(name="Moderation", value="moderation"),
                hikari.CommandChoice(name="Logging", value="logging"),
            ],
        ),
    ],
    manager: ModuleManager = arc.inject(),
) -> None:
    """Enable a module."""
    if hikari.Permissions.MANAGE_GUILD not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError(
            "You need the 'Manage Guild/Server' permission to enable modules."
        )

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    try:
        module_type = ModuleType(module)

        if await manager.is_enabled(ctx.guild_id, module_type):
            await ctx.respond(
                f"❌ The module '{module_type.value}' is already enabled.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        await manager.enable_module(ctx.guild_id, module_type)

        embed = hikari.Embed(
            title="✅ Module Enabled",
            description=f"The **{module}** module has been enabled for this server.",
            color=0x00FF00,
        )

        deps = manager.get_dependencies(module_type)
        if deps:
            embed.add_field(
                name="Dependencies",
                value=f"This module requires: {', '.join(d.value for d in deps)}",
                inline=False,
            )

        if module_type == ModuleType.SLOWMODE:
            embed.add_field(
                name="Next Steps",
                value="Use `/serenity channel enable` to enable slowmode in specific channels.",
                inline=False,
            )
        elif module_type == ModuleType.MODERATION:
            embed.add_field(
                name="Next Steps",
                value="Moderation commands like `/ban`, `/kick`, `/timeout` should be used directly now.",
                inline=False,
            )
        elif module_type == ModuleType.LOGGING:
            embed.add_field(
                name="Next Steps",
                value="Use `/logging setup` to configure log channels.",
                inline=False,
            )

        await ctx.respond(embed=embed)
        logger.info(f"Enabled module {module} in guild {ctx.guild_id} by user {ctx.user.id}")
    except ConfigurationError as e:
        await ctx.respond(f"❌ {str(e)}", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e:
        logger.error(
            f"Failed to enable module {module} in guild {ctx.guild_id}: {e}", exc_info=True
        )
        await ctx.respond(
            "❌ An error occurred while enabling the module. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@module.include
@arc.slash_subcommand("disable", "Disable a module for this server.")
async def disable_module(
    ctx: arc.GatewayContext,
    module: arc.Option[
        str,
        arc.StrParams(
            "The module to disable.",
            choices=[
                hikari.CommandChoice(name="Slowmode", value="slowmode"),
                hikari.CommandChoice(name="Moderation", value="moderation"),
                hikari.CommandChoice(name="Logging", value="logging"),
            ],
        ),
    ],
    manager: ModuleManager = arc.inject(),
) -> None:
    if hikari.Permissions.MANAGE_GUILD not in ctx.member.permissions:  # type: ignore
        raise SerenityPermissionError(
            "You need the 'Manage Guild/Server' permission to disable modules."
        )

    if not ctx.guild_id:
        await ctx.respond(
            "❌ This command can only be used in a server.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )
        return

    try:
        module_type = ModuleType(module)

        if not await manager.is_enabled(ctx.guild_id, module_type):
            await ctx.respond(
                f"❌ The module '{module_type.value}' is already disabled.",
                flags=hikari.MessageFlag.EPHEMERAL,
            )
            return

        await manager.disable_module(ctx.guild_id, module_type)

        embed = hikari.Embed(
            title="✅ Module Disabled",
            description=f"The **{module}** module has been disabled for this server.",
            color=0xFF0000,
        )

        if module_type == ModuleType.SLOWMODE:
            embed.add_field(
                name="Next Steps",
                value="Automatic slowmode adjustments will no longer occur. You may want to manually adjust slowmode settings in your channels if needed.",
                inline=False,
            )
        elif module_type == ModuleType.MODERATION:
            embed.add_field(
                name="Next Steps",
                value="Moderation commands will no longer be available. Make sure to manage any existing moderation settings or roles as needed.",
                inline=False,
            )
        elif module_type == ModuleType.LOGGING:
            embed.add_field(
                name="Next Steps",
                value="Logging will stop. Moderation module will also be disabled as it depends on logging.",
                inline=False,
            )

        await ctx.respond(embed=embed)
        logger.info(f"Disabled module {module} in guild {ctx.guild_id} by user {ctx.user.id}")
    except ConfigurationError as e:
        await ctx.respond(f"❌ {str(e)}", flags=hikari.MessageFlag.EPHEMERAL)
    except Exception as e:
        logger.error(
            f"Failed to disable module {module} in guild {ctx.guild_id}: {e}", exc_info=True
        )
        await ctx.respond(
            "❌ An error occurred while disabling the module. Please try again later.",
            flags=hikari.MessageFlag.EPHEMERAL,
        )


@arc.loader
def load(client: arc.GatewayClient) -> None:
    client.add_plugin(plugin)
    logger.info("Module management commands loaded.")


@arc.unloader
def unload(client: arc.GatewayClient) -> None:
    client.remove_plugin(plugin)
    logger.info("Module management commands unloaded.")
