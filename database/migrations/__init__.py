import importlib
import time
from pathlib import Path
from typing import List, Tuple

import aiosqlite

from utils.logging import get_logger

logger = get_logger(__name__)


class MigrationManager:
    """Manages database schema migrations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.migrations_dir = Path(__file__).parent

    async def get_current_version(self) -> int:
        """Get the current database version"""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                async with db.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
            except aiosqlite.OperationalError:
                return 0

    async def run_migrations(self) -> None:
        """Run all pending migrations."""
        current = await self.get_current_version()
        migrations = self._get_migrations()

        for version, migration_file in migrations:
            if version > current:
                await self._run_migration(version, migration_file)

    def _get_migrations(self) -> List[Tuple[int, Path]]:
        """Get all migration files"""
        migrations = []
        for file in sorted(self.migrations_dir.glob("*.py")):
            if file.name.startswith("_"):
                continue
            version = int(file.stem.split("_")[0])
            migrations.append((version, file))
        return migrations

    async def _run_migration(self, version: int, migration_file: Path) -> None:
        """Run a single migration"""
        logger.info(f"Running migration {version}: {migration_file.stem}")

        module = importlib.import_module(f"database.migrations.{migration_file.stem}")

        async with aiosqlite.connect(self.db_path) as db:
            await module.upgrade(db)
            await db.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, int(time.time())),
            )
            await db.commit()
