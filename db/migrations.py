"""Ordered SQLite migrations.

Schema versions 1-5 predate this registry and are still normalized by the
idempotent compatibility callback supplied by the application. Version 6
introduces the durable migration ledger; every later schema change must be an
explicit entry in ``MIGRATIONS``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


MigrationCallback = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: MigrationCallback


def create_schema_version_ledger(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )


MIGRATIONS = (
    Migration(6, "create_schema_version_ledger", create_schema_version_ledger),
)


def record_schema_version(
    connection: sqlite3.Connection,
    version: int,
    name: str,
) -> None:
    create_schema_version_ledger(connection)
    connection.execute(
        """
        INSERT OR IGNORE INTO schema_version (version, name)
        VALUES (?, ?)
        """,
        (int(version), str(name)),
    )


def apply_schema_migrations(
    connection: sqlite3.Connection,
    current_version: int,
    compatibility_migration: MigrationCallback,
) -> None:
    """Normalize legacy schema once, then apply newer migrations in order."""
    compatibility_migration(connection)
    for migration in MIGRATIONS:
        if migration.version <= int(current_version):
            continue
        migration.apply(connection)
        record_schema_version(connection, migration.version, migration.name)
