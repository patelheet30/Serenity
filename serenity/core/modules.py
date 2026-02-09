from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List

from serenity.utils.errors import ConfigurationError
from serenity.utils.logging import get_logger

if TYPE_CHECKING:
    from serenity.database.repository import Repository

logger = get_logger(__name__)


class ModuleType(Enum):
    """Available module types."""

    SLOWMODE = "slowmode"
    MODERATION = "moderation"
    LOGGING = "logging"

    # Future module types will be added here


@dataclass
class ModuleConfig:
    """Configuration for a module."""

    guild_id: int
    module_type: ModuleType
    is_enabled: bool
    settings: Dict[str, Any]
    updated_at: int


MODULE_DEPENDENCIES: Dict[ModuleType, List[ModuleType]] = {
    ModuleType.SLOWMODE: [],
    ModuleType.LOGGING: [],
    ModuleType.MODERATION: [ModuleType.LOGGING],
    # Future dependencies will be added here
}


class ModuleManager:
    """Manages module configuration and state for guilds."""

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def is_enabled(self, guild_id: int, module: ModuleType) -> bool:
        """Check if a module is enabled for a guild."""
        try:
            config = await self.repo.get_module_config(guild_id, module)
            return config.is_enabled
        except Exception as e:
            logger.error(f"Error checking if module {module} is enabled for guild {guild_id}: {e}")
            return False

    async def enable_module(self, guild_id: int, module: ModuleType) -> None:
        """Enable a module for a guild, checking dependencies first"""
        dependencies = MODULE_DEPENDENCIES.get(module, [])
        for dep in dependencies:
            if not await self.is_enabled(guild_id, dep):
                raise ConfigurationError(
                    f"Cannot enable {module.value} module; dependency {dep.value} is not enabled."
                )
        await self.repo.set_module_enabled(guild_id, module, True)
        logger.info(f"Enabled module {module.value} for guild {guild_id}.")

    async def disable_module(self, guild_id: int, module: ModuleType) -> None:
        """Disable a module for a guild, checking if other modules depend on it"""

        enabled_modules = await self.get_enabled_modules(guild_id)

        for enabled_module in enabled_modules:
            if enabled_module == module:
                continue
            dependencies = MODULE_DEPENDENCIES.get(enabled_module, [])
            if module in dependencies:
                raise ConfigurationError(
                    f"Cannot disable {module.value} module; it is a dependency for enabled module {enabled_module.value}."
                )

        await self.repo.set_module_enabled(guild_id, module, False)
        logger.info(f"Disabled module {module.value} for guild {guild_id}.")

    async def get_enabled_modules(self, guild_id: int) -> List[ModuleType]:
        """Get a list of enabled modules for a guild."""
        return await self.repo.get_enabled_modules(guild_id)

    async def get_module_config(self, guild_id: int, module_type: ModuleType) -> ModuleConfig:
        """Get the configuration for a specific module in a guild."""
        return await self.repo.get_module_config(guild_id, module_type)

    async def update_module_settings(
        self, guild_id: int, module_type: ModuleType, settings: Dict[str, Any]
    ) -> None:
        """Update settings for a module"""
        await self.repo.update_module_settings(guild_id, module_type, settings)
        logger.info(f"Updated settings for module {module_type.value} in guild {guild_id}.")

    def get_dependencies(self, module: ModuleType) -> List[ModuleType]:
        """Get the list of dependencies for a given module."""
        return MODULE_DEPENDENCIES.get(module, [])


class ModuleNotEnabledError(Exception):
    """Raised when attempting to use a module that is not enabled."""

    def __init__(self, module: ModuleType):
        self.module = module
        super().__init__(f"The module '{module.value}' is not enabled.")
