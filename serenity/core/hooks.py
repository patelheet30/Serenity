from typing import Any

import arc

from serenity.core.modules import ModuleManager, ModuleNotEnabledError, ModuleType


def require_module(module_type: ModuleType):
    """Factory function to create a hook that checks if a module is enabled."""

    async def module_check_hook(ctx: arc.Context[Any]) -> None:
        """Hook that checks if the required module is enabled for the guild"""
        if not ctx.guild_id:
            return

        manager: ModuleManager = ctx.client.get_type_dependency(ModuleManager)

        if not await manager.is_enabled(ctx.guild_id, module_type):
            raise ModuleNotEnabledError(module_type)

    return module_check_hook
