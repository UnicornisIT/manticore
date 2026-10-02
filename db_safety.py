"""SQLite integrity, versioning and backup primitives used by web and desktop."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path


CURRENT_SCHEMA_VERSION = 3
MIGRATION_BACKUP_KEEP = 5
LARGE_DATABASE_BYTES = 512 * 1024 * 1024


class DatabaseSafetyError(RuntimeError):
    """Base class for failures that must stop database writes."""


class DatabaseIntegrityError(DatabaseSafetyError):
    pass


class DatabaseSchemaTooNewError(DatabaseSafetyError):
    pass


class DatabaseLockedError(DatabaseSafetyError):
    pass


class InsufficientDiskSpaceError(DatabaseSafetyError):
    pass


def ensure_free_space(directory: str | os.PathLike, required_bytes: int) -> None:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    # Keep a small reserve so a successful backup cannot consume the last bytes.
    required = max(int(required_bytes), 1024 * 1024) + 8 * 1024 * 1024
    if free < required:
        raise InsufficientDiskSpaceError(
            f"Недостаточно свободного места: требуется не менее {required} байт, доступно {free}."
        )


def integrity_check(connection: sqlite3.Connection, *, full: bool = True) -> None:
    pragma = "integrity_check" if full else "quick_check"
    rows = connection.execute(f"PRAGMA {pragma}").fetchall()
    if not rows or any(str(row[0]).casefold() != "ok" for row in rows):
        details = "; ".join(str(row[0]) for row in rows[:5]) or "нет результата"
        raise DatabaseIntegrityError(f"SQLite {pragma} failed: {details}")


def check_database_file(path: str | os.PathLike, *, full: bool | None = None) -> None:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if full is None:
        full = path.stat().st_size <= LARGE_DATABASE_BYTES
    uri = path.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        connection.execute("PRAGMA query_only=ON")
        try:
            integrity_check(connection, full=full)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise DatabaseLockedError(
                    "База данных временно занята другим процессом. Изменения структуры не выполнялись."
                ) from exc
            raise DatabaseIntegrityError("База данных требует проверки. Изменения структуры не выполнялись.") from exc
        except sqlite3.DatabaseError as exc:
            raise DatabaseIntegrityError("База данных требует проверки. Изменения структуры не выполнялись.") from exc
    finally:
        connection.close()


def sqlite_backup(source_path: str | os.PathLike, destination_path: str | os.PathLike) -> Path:
    source_path = Path(source_path).resolve()
    destination_path = Path(destination_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    ensure_free_space(destination_path.parent, source_path.stat().st_size)
    if destination_path.exists():
        raise FileExistsError(destination_path)

    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    destination = sqlite3.connect(str(destination_path))
    try:
        source.backup(destination)
        integrity_check(destination, full=True)
        destination.commit()
    except Exception:
        destination.close()
        source.close()
        try:
            destination_path.unlink()
        except OSError:
            pass
        raise
    else:
        destination.close()
        source.close()
    return destination_path


def restore_sqlite_backup(source_path: str | os.PathLike, destination_path: str | os.PathLike) -> Path:
    """Restore through SQLite into a verified temporary file, then atomically replace."""
    source_path = Path(source_path).resolve()
    destination_path = Path(destination_path).resolve()
    temporary = destination_path.with_name(f".{destination_path.name}.restore-{os.getpid()}-{time.time_ns()}.tmp")
    try:
        sqlite_backup(source_path, temporary)
        os.replace(temporary, destination_path)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    check_database_file(destination_path, full=True)
    return destination_path


def schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
    ).fetchone()
    if not table:
        return 0
    row = connection.execute("SELECT version FROM schema_metadata WHERE id=1").fetchone()
    return int(row[0]) if row else 0


def set_schema_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            id INTEGER PRIMARY KEY CHECK (id=1),
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    connection.execute(
        """
        INSERT INTO schema_metadata (id, version, updated_at)
        VALUES (1, ?, datetime('now', 'localtime'))
        ON CONFLICT(id) DO UPDATE SET
            version=excluded.version,
            updated_at=excluded.updated_at
        """,
        (version,),
    )
    connection.execute(f"PRAGMA user_version={int(version)}")


def read_schema_version(path: str | os.PathLike) -> int:
    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
    try:
        return schema_version(connection)
    finally:
        connection.close()


def _migration_backup_path(database_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return database_path.with_name(
        f"{database_path.stem}.backup-before-migration-{timestamp}{database_path.suffix or '.db'}"
    )


def retain_migration_backups(database_path: str | os.PathLike, keep: int = MIGRATION_BACKUP_KEEP) -> None:
    database_path = Path(database_path).resolve()
    pattern = f"{database_path.stem}.backup-before-migration-*{database_path.suffix or '.db'}"
    backups = sorted(
        (item for item in database_path.parent.glob(pattern) if item.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in backups[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass


def migrate_database(database_path: str | os.PathLike, migration_callback) -> Path | None:
    """Run the current schema migration atomically, with a verified SQLite backup."""
    database_path = Path(database_path).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = database_path.is_file() and database_path.stat().st_size > 0
    current = 0
    if existed:
        try:
            current = read_schema_version(database_path)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise DatabaseLockedError(
                    "База данных временно занята другим процессом. Изменения структуры не выполнялись."
                ) from exc
            raise
        except (DatabaseIntegrityError, sqlite3.DatabaseError):
            forensic = database_path.with_name(
                f"{database_path.stem}.corrupt-detected-{timestamp_token()}{database_path.suffix or '.db'}"
            )
            try:
                ensure_free_space(forensic.parent, database_path.stat().st_size)
                shutil.copy2(database_path, forensic)
            except OSError:
                pass
            raise DatabaseIntegrityError(
                "База данных требует проверки. Изменения структуры не выполнялись. Исходный файл не изменён."
            )
    if current > CURRENT_SCHEMA_VERSION:
        raise DatabaseSchemaTooNewError(
            "Эта база данных была обновлена более новой версией Manticore. "
            "Для защиты данных откройте её новой версией программы."
        )

    # Version zero is either a legacy Manticore database or a new empty file.
    needs_migration = not existed or current < CURRENT_SCHEMA_VERSION
    if not needs_migration:
        check_database_file(database_path, full=False)
        return None

    backup_path = None
    if existed:
        try:
            check_database_file(database_path, full=None)
        except DatabaseIntegrityError:
            forensic = database_path.with_name(
                f"{database_path.stem}.corrupt-detected-{timestamp_token()}{database_path.suffix or '.db'}"
            )
            try:
                ensure_free_space(forensic.parent, database_path.stat().st_size)
                shutil.copy2(database_path, forensic)
            except OSError:
                pass
            raise DatabaseIntegrityError(
                "База данных требует проверки. Изменения структуры не выполнялись. Исходный файл не изменён."
            )
        backup_path = sqlite_backup(database_path, _migration_backup_path(database_path))

    connection = sqlite3.connect(str(database_path), timeout=5.0, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        migration_callback(connection)
        set_schema_version(connection, CURRENT_SCHEMA_VERSION)
        connection.execute("COMMIT")
        integrity_check(connection, full=True)
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        connection.close()
        if backup_path:
            forensic = database_path.with_name(
                f"{database_path.stem}.failed-migration-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}{database_path.suffix or '.db'}"
            )
            try:
                shutil.copy2(database_path, forensic)
            except Exception:
                pass
            try:
                restore_sqlite_backup(backup_path, database_path)
            except Exception as restore_error:
                raise DatabaseSafetyError(
                    "Миграция не завершена, автоматическое восстановление исходной базы также не удалось. "
                    f"Безопасная копия: {backup_path}"
                ) from restore_error
        raise
    else:
        connection.close()
        if backup_path:
            retain_migration_backups(database_path)
        return backup_path


def timestamp_token() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")
