import importlib
import time
from pathlib import Path
from typing import List, Tuple

import aiosqlite

from serenity.utils.logging import get_logger

logger = get_logger(__name__)


class MigrationManager:
    """Manages database schema migrations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.migrations_dir = Path(__file__).parent

    def _get_migrations(self) -> List[Tuple[int, Path]]:
        """Get all migration files"""
        migrations = []
        for file in sorted(self.migrations_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue
            version = int(file.stem.split("_")[0])
            migrations.append((version, file))
        return migrations

    async def run_migrations(self) -> None:
        """Run all pending migrations"""
        async with aiosqlite.connect(self.db_path) as db:
            await self._run_migrations_internal(db)

    async def run_migrations_with_connection(self, db: aiosqlite.Connection) -> None:
        """Run all pending migrations using an existing connection"""
        await self._run_migrations_internal(db)

    async def _run_migrations_internal(self, db: aiosqlite.Connection) -> None:
        """Internal method to run migrations"""
        current = await self._get_current_version(db)
        migrations = self._get_migrations()

        for version, migration_file in migrations:
            if version > current:
                await self._run_migration(db, version, migration_file)

    async def get_current_version(self) -> int:
        """Get current database version"""
        async with aiosqlite.connect(self.db_path) as db:
            return await self._get_current_version(db)

    async def _get_current_version(self, db: aiosqlite.Connection) -> int:
        """Internal method to get current version"""
        try:
            async with db.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except aiosqlite.OperationalError:
            # Table doesn't exist, version 0
            return 0

    async def _run_migration(
        self, db: aiosqlite.Connection, version: int, migration_file: Path
    ) -> None:
        """Run a single migration"""
        logger.info(f"Running migration {version}: {migration_file.stem}")

        module = importlib.import_module(f"serenity.database.migrations.{migration_file.stem}")

        # Run migration
        await module.upgrade(db)

        # Record migration
        await db.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, int(time.time())),
        )
        await db.commit()
