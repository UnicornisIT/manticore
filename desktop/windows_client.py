"""Native Windows shell for Manticore with remote and local database modes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import desktop_releases
from db_safety import (
    CURRENT_SCHEMA_VERSION,
    DatabaseSafetyError,
    DatabaseIntegrityError,
    DatabaseLockedError,
    DatabaseSchemaMismatchError,
    DatabaseSchemaTooNewError,
    InsufficientDiskSpaceError,
    ensure_free_space,
    read_schema_state,
    schema_mismatch_message,
    schema_too_new_message,
    sqlite_backup,
    timestamp_token,
)


APP_NAME = "Manticore"
CONFIG_FILENAME = "desktop-config.json"
UPDATE_ENDPOINT = "/api/desktop/releases/windows"
VERSION_PATTERN = desktop_releases.VERSION_PATTERN
MAX_INSTALLER_SIZE = 256 * 1024 * 1024
TRUST_POLICY_PATH = Path("desktop") / "trusted_update.json"
WINDOW_ICON_PATH = Path("desktop") / "manticore.ico"
UNINSTALL_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{815471B3-D4A7-49C8-9F25-BEACF00E37B8}_is1"
WINTRUST_SUCCESS = 0x00000000
WINTRUST_UNTRUSTED_ROOT = 0x800B0109
_INSTANCE_MUTEX = None
_INSTANCE_ACTIVATION_EVENT = None
_INSTANCE_LISTENER_STOP = threading.Event()
_INSTANCE_LISTENER_THREAD = None
INSTANCE_MUTEX_NAME = r"Local\ManticoreDesktopClient"
INSTANCE_ACTIVATION_EVENT_NAME = r"Local\ManticoreDesktopClient.Activate"
SOURCE_PREFERENCES = {"primary", "fallback", "ask"}
SOURCE_OK = "SOURCE_OK"
SOURCE_NETWORK_UNAVAILABLE = "SOURCE_NETWORK_UNAVAILABLE"
SOURCE_PERMISSION_DENIED = "SOURCE_PERMISSION_DENIED"
SOURCE_FILE_NOT_FOUND = "SOURCE_FILE_NOT_FOUND"
SOURCE_DATABASE_CORRUPT = "SOURCE_DATABASE_CORRUPT"
SOURCE_LOCKED = "SOURCE_LOCKED"
SOURCE_SCHEMA_TOO_NEW = "SOURCE_SCHEMA_TOO_NEW"
SOURCE_SCHEMA_MISMATCH = "SOURCE_SCHEMA_MISMATCH"
SOURCE_UNKNOWN_ERROR = "SOURCE_UNKNOWN_ERROR"
# Compatibility aliases for integrations built against the first fallback release.
SOURCE_MISSING = SOURCE_FILE_NOT_FOUND
SOURCE_CORRUPT = SOURCE_DATABASE_CORRUPT


class AmbiguousSourceError(ValueError):
    """A source value needs an explicit filesystem/server choice."""


def _instance_kernel32():
    """Return handle-safe Win32 declarations for single-instance IPC."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def classify_source_value(value: str) -> str:
    """Classify input without ever treating a Windows path as a URI."""
    value = str(value or "").strip().strip('"')
    lowered = value.casefold()
    if value.startswith(("\\\\", "//")) or re.match(r"^[a-zA-Z]:[\\/]", value):
        return "filesystem_path"
    if lowered.startswith(("http://", "https://")):
        return "server_url"
    raise AmbiguousSourceError(
        "Источник неоднозначен. Укажите, является ли значение сетевым путём к SQLite "
        "или полным серверным URL с http:// либо https://."
    )


def bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])).resolve()


def powershell_executable() -> str:
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    path = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not path.is_file():
        raise OSError("Системный Windows PowerShell не найден.")
    return str(path)


def current_version() -> str:
    try:
        value = (bundle_root() / "VERSION").read_text(encoding="utf-8-sig").strip().lstrip("vV")
    except OSError:
        value = "0.0.0"
    return value if VERSION_PATTERN.fullmatch(value) else "0.0.0"


def load_trust_policy() -> dict:
    try:
        payload = json.loads((bundle_root() / TRUST_POLICY_PATH).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("В клиент не встроена политика доверенных обновлений.") from exc
    repository = desktop_releases.normalize_repository(payload.get("github_repository", ""))
    signer_sha256 = str(payload.get("signer_certificate_sha256") or "").strip().lower()
    if repository != desktop_releases.DEFAULT_GITHUB_REPOSITORY:
        raise ValueError("Недоверенный источник обновлений.")
    if signer_sha256 and (not re.fullmatch(r"[0-9a-f]{64}", signer_sha256) or signer_sha256 == "0" * 64):
        raise ValueError("Некорректный сертификат издателя обновлений.")
    if not signer_sha256 and payload.get("allow_unsigned_updates") is not True:
        raise ValueError("В сборке не настроена политика подписи обновлений.")
    return {"github_repository": repository, "signer_certificate_sha256": signer_sha256}



def application_data_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_old_installers() -> None:
    update_directory = application_data_directory() / "updates"
    if not update_directory.is_dir():
        return
    cutoff = time.time() - 24 * 60 * 60
    for path in update_directory.glob("Manticore-Setup-*.exe"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def config_path() -> Path:
    return application_data_directory() / CONFIG_FILENAME


def configure_logging() -> None:
    log_directory = application_data_directory() / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_directory / "client.log",
        maxBytes=1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)


def acquire_single_instance() -> bool:
    """Keep the local database and executable update safe from parallel launches."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return True
    import ctypes

    kernel32 = _instance_kernel32()
    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if not handle:
        return False
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


def _windows_for_process(process_id: int | None = None) -> list[int]:
    """Return top-level Manticore windows owned by a process."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    expected_pid = int(process_id or os.getpid())
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def collect(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != expected_pid:
            return True
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value.startswith(APP_NAME):
            handles.append(int(hwnd))
        return True

    ctypes.windll.user32.EnumWindows(collect, 0)
    return handles


def activate_desktop_window() -> bool:
    """Restore and foreground the existing WebView without touching pywebview threads."""
    if os.name != "nt":
        return False
    import ctypes

    handles = _windows_for_process()
    if not handles:
        return False
    hwnd = handles[0]
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    return True


def close_desktop_windows() -> bool:
    """Ask all current-process Manticore windows to close on their UI threads."""
    if os.name != "nt":
        return False
    import ctypes

    handles = _windows_for_process()
    for hwnd in handles:
        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return bool(handles)


def signal_existing_instance() -> bool:
    """Request activation from the process that owns the named mutex."""
    if os.name != "nt":
        return False
    import ctypes

    kernel32 = _instance_kernel32()
    handle = kernel32.OpenEventW(0x0002, False, INSTANCE_ACTIVATION_EVENT_NAME)  # EVENT_MODIFY_STATE
    if not handle:
        return False
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def start_instance_activation_listener() -> None:
    """Start the first instance's lightweight activation IPC listener."""
    global _INSTANCE_ACTIVATION_EVENT, _INSTANCE_LISTENER_THREAD
    if os.name != "nt" or _INSTANCE_LISTENER_THREAD is not None:
        return
    import ctypes

    kernel32 = _instance_kernel32()
    event_handle = kernel32.CreateEventW(None, False, False, INSTANCE_ACTIVATION_EVENT_NAME)
    if not event_handle:
        logging.error("Could not create the single-instance activation event")
        return
    _INSTANCE_ACTIVATION_EVENT = event_handle
    _INSTANCE_LISTENER_STOP.clear()

    def listen() -> None:
        while not _INSTANCE_LISTENER_STOP.is_set():
            result = kernel32.WaitForSingleObject(event_handle, 500)
            if result == 0 and not _INSTANCE_LISTENER_STOP.is_set():  # WAIT_OBJECT_0
                logging.info("Existing instance activation requested")
                if not activate_desktop_window():
                    logging.warning("Existing instance has no available Manticore window")

    _INSTANCE_LISTENER_THREAD = threading.Thread(
        target=listen,
        name="manticore-instance-activation",
        daemon=True,
    )
    _INSTANCE_LISTENER_THREAD.start()


def stop_instance_activation_listener() -> None:
    global _INSTANCE_MUTEX, _INSTANCE_ACTIVATION_EVENT, _INSTANCE_LISTENER_THREAD
    _INSTANCE_LISTENER_STOP.set()
    if os.name == "nt":
        import ctypes

        kernel32 = _instance_kernel32()
        if _INSTANCE_ACTIVATION_EVENT:
            kernel32.SetEvent(_INSTANCE_ACTIVATION_EVENT)
        if _INSTANCE_LISTENER_THREAD and _INSTANCE_LISTENER_THREAD is not threading.current_thread():
            _INSTANCE_LISTENER_THREAD.join(timeout=2)
            if _INSTANCE_LISTENER_THREAD.is_alive():
                logging.warning("Single-instance activation listener did not stop within timeout")
        if _INSTANCE_ACTIVATION_EVENT:
            kernel32.CloseHandle(_INSTANCE_ACTIVATION_EVENT)
        if _INSTANCE_MUTEX:
            kernel32.CloseHandle(_INSTANCE_MUTEX)
    _INSTANCE_MUTEX = None
    _INSTANCE_ACTIVATION_EVENT = None
    _INSTANCE_LISTENER_THREAD = None


def load_config() -> dict:
    path = config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("Desktop config is unreadable: %s", exc)
        try:
            diagnostic = path.with_name(f"{path.name}.corrupt-{timestamp_token()}")
            shutil.copy2(path, diagnostic)
            logging.error("Unreadable desktop config preserved at %s", diagnostic)
        except OSError:
            logging.exception("Could not preserve unreadable desktop config")
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            backup_bytes = backup.read_bytes()
            payload = json.loads(backup_bytes.decode("utf-8-sig"))
            if isinstance(payload, dict):
                restore_tmp = path.with_name(f".{path.name}.recovery-{os.getpid()}.tmp")
                with restore_tmp.open("wb") as stream:
                    stream.write(backup_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(restore_tmp, path)
                logging.warning("Desktop config restored from %s", backup)
                return payload
        except (OSError, json.JSONDecodeError):
            pass
        return {}


def save_config(config: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
    with temporary_path.open("wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if path.is_file():
        backup = path.with_suffix(path.suffix + ".bak")
        backup_tmp = backup.with_name(f".{backup.name}.{os.getpid()}.tmp")
        try:
            with path.open("rb") as source, backup_tmp.open("wb") as destination:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(backup_tmp, backup)
        finally:
            try:
                backup_tmp.unlink()
            except OSError:
                pass
    try:
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink()
        except OSError:
            pass


def source_identifier(source: dict) -> str:
    source_type = source.get("type")
    value = source.get("url") if source_type == "server" else source.get("path")
    return f"{source_type}:{str(value or '').strip().casefold()}"


def configured_source(config: dict) -> dict:
    """Return the active primary definition from strictly separated fields."""
    database_source = config.get("database_source")
    if config.get("mode") == "local" and isinstance(database_source, dict) and database_source.get("path"):
        return {"type": "sqlite", **database_source}
    server_source = config.get("server_source")
    if config.get("mode") == "remote" and isinstance(server_source, dict) and server_source.get("url"):
        return {"type": "server", **server_source}
    raise ValueError("Источник базы данных не настроен.")


def fallback_database_path(primary_source: dict) -> str:
    digest = hashlib.sha256(source_identifier(primary_source).encode("utf-8")).hexdigest()[:20]
    return str(application_data_directory() / "fallback" / digest / "fallback.db")


def is_safe_fallback_path(value: str) -> bool:
    try:
        root = (application_data_directory() / "fallback").resolve()
        candidate = Path(str(value or "")).resolve()
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except (OSError, ValueError):
        return False


def migrate_config(config: dict) -> dict:
    """Upgrade the flat legacy config without ever discarding its primary source."""
    migrated = dict(config or {})
    primary = None
    database_source = migrated.get("database_source")
    server_source = migrated.get("server_source")
    if isinstance(database_source, dict) and database_source.get("path"):
        primary = {"type": "sqlite", **database_source}
    elif isinstance(server_source, dict) and server_source.get("url"):
        primary = {"type": "server", **server_source}
    # Migrate the short-lived union model as well as older flat configs.
    elif isinstance(database_source, dict) and database_source.get("type") in {"server", "sqlite"}:
        primary = database_source
    if not isinstance(primary, dict) or primary.get("type") not in {"server", "sqlite"}:
        primary = migrated.get("primary_source")
    if not isinstance(primary, dict) or primary.get("type") not in {"server", "sqlite"}:
        # Old database_path values include local, mapped-drive and UNC SQLite
        # paths. They take precedence even if an old `mode` value is wrong.
        if migrated.get("database_path"):
            primary = {"type": "sqlite", "path": str(migrated["database_path"])}
        elif migrated.get("server_url"):
            server_value = str(migrated["server_url"]).strip()
            if classify_source_value(server_value) == "filesystem_path":
                primary = {"type": "sqlite", "path": server_value}
            else:
                primary = {"type": "server", "url": server_value}
        else:
            return migrated
    if primary["type"] == "sqlite":
        primary = {**primary, "path": normalize_database_path(primary.get("path", ""), create_parent=False)}
        primary.pop("url", None)
    else:
        primary = {**primary, "url": normalize_server_url(primary.get("url", ""))}
        primary.pop("path", None)
    if primary["type"] == "sqlite":
        migrated["database_source"] = {key: value for key, value in primary.items() if key != "type"}
        migrated.pop("server_source", None)
    else:
        migrated["server_source"] = {key: value for key, value in primary.items() if key != "type"}
        migrated.pop("database_source", None)
    migrated.pop("primary_source", None)
    fallback_sources = migrated.get("fallback_sources")
    if not isinstance(fallback_sources, dict):
        fallback_sources = {}
    key = source_identifier(primary)
    mapped = fallback_sources.get(key)
    if not mapped or not is_safe_fallback_path(mapped):
        if mapped:
            logging.error("Unsafe fallback path ignored for primary source %s", key)
        fallback_sources[key] = fallback_database_path(primary)
    migrated["fallback_sources"] = fallback_sources
    if not isinstance(migrated.get("fallback_sessions"), dict):
        migrated["fallback_sessions"] = {}
    if migrated.get("active_source") not in {"primary", "fallback"}:
        migrated["active_source"] = "primary"
    if migrated.get("preferred_source") not in SOURCE_PREFERENCES:
        migrated["preferred_source"] = "ask"
    # Keep legacy mirrors for older installed clients and rollback compatibility.
    migrated["mode"] = "remote" if primary["type"] == "server" else "local"
    migrated["server_url"] = primary.get("url", "")
    migrated["database_path"] = primary.get("path", "")
    # A persisted port would be stale. webview_url exists only in the runtime
    # model after Flask has bound its loopback port.
    migrated.pop("webview_url", None)
    return migrated


def get_fallback_path(config: dict) -> str:
    primary = configured_source(config)
    path = config["fallback_sources"][source_identifier(primary)]
    if not is_safe_fallback_path(path):
        raise ValueError("Некорректный путь fallback-базы.")
    return path


def fallback_state(database_path: str) -> dict:
    path = Path(database_path)
    default = {"state": "clean", "dirty": False, "session_id": "", "changed_at": "", "changes": 0}
    if not path.is_file():
        return default
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
        try:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fallback_state'"
            ).fetchone():
                return default
            row = connection.execute(
                "SELECT state, session_id, changed_at FROM fallback_state WHERE id=1"
            ).fetchone()
            count = connection.execute("SELECT COUNT(*) FROM fallback_change_log").fetchone()[0]
        finally:
            connection.close()
        if not row:
            return default
        return {
            "state": row[0], "dirty": row[0] in {"dirty", "archived_with_unsynced_changes"},
            "session_id": row[1] or "", "changed_at": row[2] or "", "changes": int(count),
        }
    except sqlite3.Error as exc:
        logging.warning("Could not inspect fallback state for %s: %s", database_path, exc)
        return {**default, "state": "unknown", "dirty": True}


def archive_dirty_fallback(config: dict) -> dict:
    fallback_path = Path(get_fallback_path(config)).resolve()
    state = fallback_state(str(fallback_path))
    if not state["dirty"] or state["state"] == "archived_with_unsynced_changes":
        return {"ok": True, "backup": "", "state": state["state"]}
    primary_key = hashlib.sha256(source_identifier(configured_source(config)).encode("utf-8")).hexdigest()[:20]
    session_id = re.sub(r"[^a-zA-Z0-9-]", "", state["session_id"] or "unknown")[:64]
    archive_directory = application_data_directory() / "fallback-backups" / primary_key
    archive_path = archive_directory / f"fallback-{timestamp_token()}-{session_id}.db"
    sqlite_backup(fallback_path, archive_path)
    connection = sqlite3.connect(str(fallback_path), timeout=5.0, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE fallback_state SET state='archived_with_unsynced_changes', archived_at=datetime('now', 'localtime') WHERE id=1"
        )
        connection.execute("COMMIT")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        connection.close()
    logging.warning("Dirty fallback archived before switching to primary: %s", archive_path)
    return {"ok": True, "backup": str(archive_path), "state": "archived_with_unsynced_changes"}


def ensure_fallback_session(config: dict) -> str:
    sessions = config.get("fallback_sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    key = source_identifier(configured_source(config))
    state = fallback_state(get_fallback_path(config))
    if not sessions.get(key) or state["state"] == "archived_with_unsynced_changes":
        sessions[key] = str(uuid.uuid4())
    config["fallback_sessions"] = sessions
    return sessions[key]


def prepare_fallback_database(config: dict) -> str:
    """Prepare fallback only while the primary source is confirmed available."""
    primary = configured_source(config)
    primary_check = inspect_source(primary)
    if primary_check["code"] != SOURCE_OK:
        raise OSError(primary_check["message"] or "Основной источник недоступен.")
    fallback = Path(get_fallback_path(config))
    if fallback.is_file():
        return str(fallback)
    fallback.parent.mkdir(parents=True, exist_ok=True)
    ensure_free_space(fallback.parent, 16 * 1024 * 1024)
    if primary["type"] == "sqlite":
        # sqlite_backup intentionally uses file: URIs for local maintenance
        # operations. A configured source may be UNC, so copy it using the raw
        # filesystem path instead.
        source_connection = sqlite3.connect(str(primary["path"]), timeout=5.0)
        destination_connection = sqlite3.connect(str(fallback), timeout=5.0)
        try:
            source_connection.execute("PRAGMA query_only=ON")
            source_connection.backup(destination_connection)
            result = destination_connection.execute("PRAGMA quick_check;").fetchone()
            if not result or str(result[0]).casefold() != "ok":
                raise sqlite3.DatabaseError("Fallback quick_check failed")
            destination_connection.commit()
        except Exception:
            destination_connection.close()
            source_connection.close()
            try:
                fallback.unlink()
            except OSError:
                pass
            raise
        else:
            destination_connection.close()
            source_connection.close()
    else:
        # Remote HTTP sources cannot be copied directly. Creating the SQLite
        # container while online is safe; the local server initializes schema.
        sqlite3.connect(str(fallback)).close()
    return str(fallback)


def _source_result(code: str, message: str = "", *, available: bool = False, **details) -> dict:
    return {"code": code, "message": message, "available": available, **details}


def _missing_sqlite_result(path: Path, original: str, exc: OSError | None = None) -> dict:
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, PermissionError) or winerror == 5:
        return _source_result(SOURCE_PERMISSION_DENIED, "Нет прав для чтения базы данных.")
    network_errors = {53, 64, 67, 121, 1231, 1232}
    if original.startswith(("\\\\", "//")) and (winerror in network_errors or not path.parent.exists()):
        return _source_result(SOURCE_NETWORK_UNAVAILABLE, "Сетевой ресурс недоступен.")
    return _source_result(SOURCE_FILE_NOT_FOUND, "Файл базы данных не найден.")


def inspect_sqlite_source(database_path: str, timeout: float = 2.0) -> dict:
    original = str(database_path or "").strip().strip('"')
    path = Path(original)
    try:
        path.stat()
        if not path.is_file():
            return _source_result(SOURCE_FILE_NOT_FOUND, "Файл базы данных не найден.")
        # Check ACLs before SQLite. UNC remains a filesystem path throughout;
        # it is never quoted, parsed, joined or converted to a file: URI.
        with path.open("rb") as stream:
            stream.read(1)
    except OSError as exc:
        return _missing_sqlite_result(path, original, exc)
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
        try:
            connection.execute("PRAGMA query_only=ON")
            result = connection.execute("PRAGMA quick_check;").fetchone()
            if not result or str(result[0]).casefold() != "ok":
                return _source_result(SOURCE_DATABASE_CORRUPT, "База данных требует проверки. Изменения структуры не выполнялись.")
            state = read_schema_state(path)
            details = {
                "database_schema": state.effective_version,
                "schema_metadata_version": state.metadata_version,
                "pragma_user_version": state.pragma_user_version,
            }
            if state.mismatch:
                return _source_result(
                    SOURCE_SCHEMA_MISMATCH,
                    schema_mismatch_message(state),
                    **details,
                )
            version = state.effective_version
            if version > CURRENT_SCHEMA_VERSION:
                return _source_result(
                    SOURCE_SCHEMA_TOO_NEW,
                    schema_too_new_message(version),
                    **details,
                )
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        finally:
            connection.close()
        return _source_result(SOURCE_OK, available=True, **details)
    except PermissionError as exc:
        logging.warning("Primary SQLite permission denied for %s: %s", database_path, exc)
        return _source_result(SOURCE_PERMISSION_DENIED, "Нет прав для чтения базы данных.")
    except sqlite3.OperationalError as exc:
        logging.warning("Primary SQLite operational error for %s: %s", database_path, exc)
        error_text = str(exc).casefold()
        if "malformed" in error_text or "not a database" in error_text:
            return _source_result(SOURCE_DATABASE_CORRUPT, "База данных повреждена или имеет неизвестный формат.")
        if "permission denied" in error_text or "access is denied" in error_text or "readonly" in error_text:
            return _source_result(SOURCE_PERMISSION_DENIED, "Нет прав для чтения базы данных.")
        if "locked" in error_text or "busy" in error_text:
            return _source_result(
                SOURCE_LOCKED,
                "База данных временно занята другим процессом. Повторите операцию позднее.",
            )
        if original.startswith(("\\\\", "//")):
            return _source_result(SOURCE_NETWORK_UNAVAILABLE, "Сетевой ресурс недоступен.")
        return _source_result(SOURCE_UNKNOWN_ERROR, "Не удалось открыть базу данных.")
    except sqlite3.DatabaseError as exc:
        logging.warning("Primary SQLite corruption detected for %s: %s", database_path, exc)
        return _source_result(SOURCE_DATABASE_CORRUPT, "База данных повреждена или имеет неизвестный формат.")
    except OSError as exc:
        logging.warning("Primary SQLite connection failed for %s: %s", database_path, exc)
        return _missing_sqlite_result(path, original, exc)


def check_sqlite_connection(database_path: str, timeout: float = 2.0) -> str:
    result = inspect_sqlite_source(database_path, timeout)
    return "" if result["available"] else result["message"]


def inspect_source(source: dict, timeout: float = 3.0) -> dict:
    if source.get("type") == "server":
        error = check_server_connection(str(source.get("url") or ""), timeout=timeout)
        return _source_result(SOURCE_NETWORK_UNAVAILABLE, error) if error else _source_result(SOURCE_OK, available=True)
    return inspect_sqlite_source(str(source.get("path") or ""), timeout=timeout)


def check_source_connection(source: dict, timeout: float = 3.0) -> str:
    result = inspect_source(source, timeout)
    return "" if result["available"] else result["message"]


def is_loopback_host(hostname: str | None) -> bool:
    return (hostname or "").casefold() in {"127.0.0.1", "localhost", "::1"}


def normalize_server_url(value: str, *, optional: bool = False) -> str:
    value = str(value or "").strip().rstrip("/")
    if optional and not value:
        return ""
    if classify_source_value(value) != "server_url":
        raise AmbiguousSourceError("Укажите полный серверный URL с http:// либо https://.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Укажите полный адрес сервера, например https://manticore.example.ru.")
    if parsed.scheme != "https" and not is_loopback_host(parsed.hostname):
        raise ValueError("Для удалённого сервера требуется HTTPS.")
    if parsed.query or parsed.fragment:
        raise ValueError("Адрес сервера не должен содержать параметры или якорь.")
    return value


def normalize_database_path(value: str, *, create_parent: bool = True) -> str:
    raw_value = str(value or "").strip().strip('"')
    if classify_source_value(raw_value) != "filesystem_path":
        raise AmbiguousSourceError("Укажите абсолютный локальный, подключённый или UNC-путь к SQLite.")
    path = Path(raw_value).expanduser()
    if path.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("Выберите файл базы с расширением .db, .sqlite или .sqlite3.")
    if create_parent and not raw_value.startswith(("\\\\", "//")):
        path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def default_database_path() -> str:
    path = application_data_directory() / "data" / "baze.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def desktop_client_command(*arguments: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve()), *arguments]


def show_native_message(title: str, message: str, *, error: bool = False) -> None:
    """Show a dependency-free Windows message when the WebView cannot be used."""
    if os.name == "nt":
        import ctypes

        flags = 0x00000010 if error else 0x00000040  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), flags)
        return
    print(f"{title}: {message}", file=sys.stderr if error else sys.stdout)


def confirm_native_message(title: str, message: str) -> bool:
    if os.name == "nt":
        import ctypes

        return ctypes.windll.user32.MessageBoxW(None, str(message), str(title), 0x00000024) == 6
    return False


class SetupApi:
    """Narrow bridge exposed only to the bundled onboarding page."""

    def __init__(self, existing: dict, page: str, password_result_path: str = ""):
        self.existing = dict(existing)
        self.page = page
        self.password_result_path = password_result_path
        self.saved = False

    def get_state(self) -> dict:
        configuration_error = str(self.existing.get("configuration_error") or "")
        try:
            existing = migrate_config(self.existing)
        except AmbiguousSourceError as exc:
            existing = dict(self.existing)
            configuration_error = str(exc)
        try:
            source = configured_source(existing)
        except ValueError:
            source = {}
        return {
            "page": self.page,
            "version": current_version(),
            "mode": "remote" if source.get("type") != "sqlite" else "local",
            "server_url": str(source.get("url") or existing.get("server_url") or ""),
            "database_path": str(source.get("path") or existing.get("database_path") or default_database_path()),
            "update_server_url": str(existing.get("update_server_url") or ""),
            "configuration_error": configuration_error,
        }

    @staticmethod
    def _close_window() -> None:
        import webview

        if webview.windows:
            webview.windows[0].destroy()

    def browse_database(self) -> str:
        import webview

        if not webview.windows:
            return ""
        dialog_type = getattr(webview, "OPEN_DIALOG", None)
        if dialog_type is None and hasattr(webview, "FileDialog"):
            dialog_type = webview.FileDialog.OPEN
        selection = webview.windows[0].create_file_dialog(
            dialog_type,
            allow_multiple=False,
            file_types=("SQLite (*.db;*.sqlite;*.sqlite3)", "Все файлы (*.*)"),
        )
        return str(selection[0]) if selection else ""

    def submit_configuration(self, payload: dict) -> dict:
        try:
            mode = str(payload.get("mode") or "")
            if mode == "remote":
                server_url = normalize_server_url(payload.get("server_url", ""))
                configured = {
                    "mode": "remote",
                    "server_url": server_url,
                    "update_server_url": server_url,
                    "database_path": str(payload.get("database_path") or "").strip(),
                    "server_source": {"url": server_url},
                }
            elif mode == "local":
                configured = {
                    "mode": "local",
                    "server_url": "",
                    "database_path": normalize_database_path(payload.get("database_path", "")),
                    "update_server_url": normalize_server_url(payload.get("update_server_url", ""), optional=True),
                    "database_source": {"path": normalize_database_path(payload.get("database_path", "")), "create_if_missing": True},
                }
            else:
                raise ValueError("Выберите режим работы.")
            configured["local_secret_key"] = self.existing.get("local_secret_key") or secrets.token_urlsafe(48)
            configured["fallback_sources"] = self.existing.get("fallback_sources", {})
            configured.update({"active_source": "primary", "preferred_source": "ask"})
            configured = migrate_config(configured)
            save_config(configured)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self.saved = True
        threading.Timer(0.05, self._close_window).start()
        return {"ok": True}

    def submit_admin_password(self, first: str, second: str) -> dict:
        if len(first or "") < 8:
            return {"ok": False, "error": "Пароль должен содержать не менее 8 символов."}
        if not secrets.compare_digest(first, second):
            return {"ok": False, "error": "Пароли не совпадают."}
        try:
            Path(self.password_result_path).write_text(first, encoding="utf-8")
        except OSError:
            logging.exception("Could not store the one-time admin password")
            return {"ok": False, "error": "Не удалось безопасно передать пароль приложению."}
        self.saved = True
        threading.Timer(0.05, self._close_window).start()
        return {"ok": True}

    def cancel(self) -> None:
        threading.Timer(0.05, self._close_window).start()


def run_setup_window(existing: dict, page: str = "configuration", password_result_path: str = "") -> bool:
    import webview

    api = SetupApi(existing, page, password_result_path)
    setup_page = bundle_root() / "desktop" / "ui" / "setup.html"
    storage_path = application_data_directory() / "setup-webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    webview.create_window(
        "Manticore — настройка рабочего места" if page == "configuration" else "Manticore — локальная база",
        str(setup_page),
        width=760,
        height=650,
        min_size=(680, 560),
        resizable=True,
        text_select=False,
        js_api=api,
    )
    webview.start(
        private_mode=False,
        storage_path=str(storage_path),
        icon=str(bundle_root() / WINDOW_ICON_PATH),
    )
    return api.saved


def run_setup_child(page: str, password_result_path: str = "") -> int:
    return 0 if run_setup_window(load_config(), page, password_result_path) else 2


def show_configuration_dialog(existing: dict) -> dict | None:
    completed = subprocess.run(
        desktop_client_command("--configuration-child"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return load_config() if completed.returncode == 0 else None


def database_has_admin(database_path: str) -> bool:
    path = Path(database_path)
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("SELECT 1 FROM users WHERE username='admin' LIMIT 1").fetchone()
        return bool(row)
    except sqlite3.OperationalError as exc:
        if any(token in str(exc).casefold() for token in ("locked", "busy")):
            raise DatabaseLockedError(
                "База данных временно занята другим процессом. Запуск остановлен до освобождения файла."
            ) from exc
        raise DatabaseSafetyError(f"Не удалось безопасно прочитать пользователей базы: {exc}") from exc
    except sqlite3.DatabaseError as exc:
        raise DatabaseIntegrityError(
            "База данных повреждена или имеет неизвестный формат. Исходный файл не изменён."
        ) from exc


def prompt_initial_admin_password() -> str | None:
    descriptor, result_name = tempfile.mkstemp(prefix="manticore-admin-", suffix=".secret")
    os.close(descriptor)
    result_path = Path(result_name)
    try:
        result_path.unlink(missing_ok=True)
        completed = subprocess.run(
            desktop_client_command("--admin-password-child", str(result_path)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return None
        password = result_path.read_text(encoding="utf-8")
        return password if len(password) >= 8 else None
    except OSError:
        logging.exception("Admin password onboarding failed")
        return None
    finally:
        result_path.unlink(missing_ok=True)


class SourceDialogApi:
    def __init__(self, state: dict, result_path: str):
        self.state = state
        self.result_path = Path(result_path)

    def get_state(self) -> dict:
        return self.state

    def choose(self, action: str, remember: bool = False) -> dict:
        if action not in {"retry", "fallback", "primary", "database", "server", "settings", "cancel"}:
            return {"ok": False}
        self.result_path.write_text(
            json.dumps({"action": action, "remember": bool(remember)}, ensure_ascii=False),
            encoding="utf-8",
        )
        import webview
        if webview.windows:
            webview.windows[0].destroy()
        return {"ok": True}


def run_source_dialog_child(state_path: str, result_path: str) -> int:
    import webview

    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 2
    api = SourceDialogApi(state, result_path)
    webview.create_window(
        "Manticore — источник данных",
        str(bundle_root() / "desktop" / "ui" / "source_dialog.html"),
        width=680,
        height=560,
        min_size=(600, 500),
        resizable=True,
        text_select=True,
        js_api=api,
    )
    webview.start(
        private_mode=False,
        storage_path=str(application_data_directory() / "source-dialog-webview"),
        icon=str(bundle_root() / WINDOW_ICON_PATH),
    )
    return 0


def show_source_dialog(state: dict) -> dict:
    descriptor, state_name = tempfile.mkstemp(prefix="manticore-source-state-", suffix=".json")
    os.close(descriptor)
    descriptor, result_name = tempfile.mkstemp(prefix="manticore-source-result-", suffix=".json")
    os.close(descriptor)
    state_path, result_path = Path(state_name), Path(result_name)
    try:
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        result_path.unlink(missing_ok=True)
        completed = subprocess.run(
            desktop_client_command("--source-dialog-child", str(state_path), str(result_path)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 0 and result_path.is_file():
            return json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        logging.exception("Source selection dialog failed")
    finally:
        state_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
    return {"action": "cancel", "remember": False}


def source_dialog_state(config: dict, error: str = "", *, recovered: bool = False) -> dict:
    primary = configured_source(config)
    primary_value = primary.get("url") if primary["type"] == "server" else primary.get("path")
    fallback_path = get_fallback_path(config)
    local_state = fallback_state(fallback_path)
    return {
        "recovered": recovered,
        "source_type": primary["type"],
        "primary": primary_value,
        "fallback": fallback_path,
        "fallback_exists": Path(fallback_path).is_file(),
        "fallback_state": local_state["state"],
        "fallback_dirty": local_state["dirty"],
        "fallback_changes": local_state["changes"],
        "error": error,
    }


def database_compatibility_message(source: dict, result: dict) -> str:
    path = str(source.get("path") or "")
    database_schema = result.get("database_schema", "не определена")
    metadata_schema = result.get("schema_metadata_version")
    pragma_schema = result.get("pragma_user_version")
    if result.get("code") == SOURCE_SCHEMA_TOO_NEW:
        return (
            "Версия базы данных несовместима с этой сборкой Manticore.\n\n"
            f"Версия приложения: {current_version()}\n"
            f"Версия схемы базы: {database_schema}\n"
            f"Максимальная поддерживаемая схема: {CURRENT_SCHEMA_VERSION}\n\n"
            f"Файл:\n{path}\n\n"
            "База не изменялась. Установите версию Manticore, поддерживающую эту схему, "
            "либо выберите другую базу данных."
        )
    if result.get("code") == SOURCE_SCHEMA_MISMATCH:
        metadata_label = "отсутствует" if metadata_schema is None else metadata_schema
        return (
            "Версии схемы базы данных не согласованы.\n\n"
            f"Версия приложения: {current_version()}\n"
            f"schema_metadata.version: {metadata_label}\n"
            f"PRAGMA user_version: {pragma_schema}\n"
            f"Максимальная поддерживаемая схема: {CURRENT_SCHEMA_VERSION}\n\n"
            f"Файл:\n{path}\n\n"
            "База не изменялась. Автоматическое исправление заблокировано; выберите другую базу "
            "или передайте этот файл для диагностики."
        )
    return str(result.get("message") or "Не удалось открыть базу данных.")


def log_startup_diagnostics(config: dict, source: dict, result: dict) -> None:
    path_text = str(source.get("path") or "") if source.get("type") == "sqlite" else ""
    exists = False
    size = None
    if path_text:
        try:
            path = Path(path_text)
            exists = path.is_file()
            size = path.stat().st_size if exists else None
        except OSError:
            pass
    logging.info(
        "Desktop startup diagnostics: app_version=%s executable=%s frozen=%s bundle_root=%s "
        "database_path=%s exists=%s size=%s active_source=%s preferred_source=%s "
        "create_if_missing=%s supported_schema=%s schema_metadata_version=%s "
        "pragma_user_version=%s inspect_code=%s",
        current_version(),
        sys.executable,
        bool(getattr(sys, "frozen", False)),
        bundle_root(),
        path_text,
        exists,
        size,
        config.get("active_source", "primary"),
        config.get("preferred_source", "ask"),
        bool(source.get("create_if_missing")),
        CURRENT_SCHEMA_VERSION,
        result.get("schema_metadata_version"),
        result.get("pragma_user_version"),
        result.get("code"),
    )
    if result.get("code") in {SOURCE_SCHEMA_TOO_NEW, SOURCE_SCHEMA_MISMATCH}:
        logging.error(
            "Database compatibility check failed: app_version=%s supported_schema=%s "
            "database_schema=%s schema_metadata_version=%s pragma_user_version=%s "
            "path=%s action=blocked_before_write",
            current_version(),
            CURRENT_SCHEMA_VERSION,
            result.get("database_schema"),
            result.get("schema_metadata_version"),
            result.get("pragma_user_version"),
            path_text,
        )


def resolve_active_source(config: dict) -> tuple[dict | None, dict]:
    """Resolve startup source interactively while retaining the primary definition."""
    config = migrate_config(config)
    while True:
        primary = configured_source(config)
        primary_result = inspect_source(primary)
        log_startup_diagnostics(config, primary, primary_result)
        primary_error = "" if primary_result["code"] == SOURCE_OK else (
            database_compatibility_message(primary, primary_result)
            if primary_result["code"] in {SOURCE_SCHEMA_TOO_NEW, SOURCE_SCHEMA_MISMATCH}
            else primary_result["message"]
        )
        fallback_path = get_fallback_path(config)
        fallback_exists = Path(fallback_path).is_file()
        fallback_result = inspect_sqlite_source(fallback_path) if fallback_exists else _source_result(SOURCE_FILE_NOT_FOUND)
        fallback_error = "" if fallback_result["code"] == SOURCE_OK else fallback_result["message"]
        active = config.get("active_source", "primary")
        preferred = config.get("preferred_source", "ask")
        primary_blocked = primary_result["code"] in {SOURCE_SCHEMA_TOO_NEW, SOURCE_SCHEMA_MISMATCH}

        if active == "primary" and primary_result["code"] == SOURCE_OK:
            return primary, config
        if (
            active == "primary"
            and primary.get("type") == "sqlite"
            and primary.get("create_if_missing")
            and primary_result["code"] == SOURCE_FILE_NOT_FOUND
        ):
            return primary, config
        if active == "fallback" and fallback_exists and fallback_result["code"] == SOURCE_OK:
            if primary_error or preferred == "fallback":
                ensure_fallback_session(config)
                save_config(config)
                return {"type": "sqlite", "path": fallback_path, "fallback": True}, config
            if preferred == "primary":
                if not fallback_state(fallback_path)["dirty"]:
                    config["active_source"] = "primary"
                    save_config(config)
                    return primary, config
            choice = show_source_dialog(source_dialog_state(config, recovered=True))
        elif primary_error and not primary_blocked and preferred == "fallback" and fallback_exists:
            config["active_source"] = "fallback"
            ensure_fallback_session(config)
            save_config(config)
            return {"type": "sqlite", "path": fallback_path, "fallback": True}, config
        elif active == "fallback" and fallback_error:
            logging.warning("Fallback source unavailable: %s", fallback_error)
            if not primary_error:
                choice = show_source_dialog(source_dialog_state(config, fallback_error, recovered=True))
            else:
                choice = show_source_dialog(source_dialog_state(config, fallback_error))
        else:
            logging.warning("Primary source unavailable: type=%s; error=%s", primary.get("type"), primary_error)
            choice = show_source_dialog(source_dialog_state(config, primary_error))

        action = choice.get("action")
        remember = bool(choice.get("remember"))
        if action == "retry":
            continue
        if action == "primary" and primary_result["code"] == SOURCE_OK:
            archive_dirty_fallback(config)
            config["active_source"] = "primary"
            config["preferred_source"] = "primary" if remember else "ask"
            save_config(config)
            return primary, config
        if action == "fallback":
            if fallback_exists and fallback_error:
                continue
            # A temporary network/ACL/lock failure must never manufacture an
            # empty fallback and present it as recovered data.
            if not fallback_exists:
                if primary_error:
                    logging.warning("Fallback creation refused while primary source is unavailable")
                    return None, config
                try:
                    fallback_path = prepare_fallback_database(config)
                except (OSError, sqlite3.Error) as exc:
                    logging.warning("Could not prepare fallback database: %s", exc)
                    return None, config
            path = Path(fallback_path)
            config["active_source"] = "fallback"
            config["preferred_source"] = "fallback" if remember else "ask"
            ensure_fallback_session(config)
            save_config(config)
            return {"type": "sqlite", "path": str(path), "fallback": True}, config
        if action in {"database", "server", "settings"}:
            configured = show_configuration_dialog(config)
            if configured is None:
                continue
            config = migrate_config(configured)
            save_config(config)
            continue
        return None, config


def version_key(value: str):
    return desktop_releases.version_key(value)


def update_channel() -> str:
    return "preview" if VERSION_PATTERN.fullmatch(current_version()).group(4) is not None else "stable"


def fetch_update_manifest(server_url: str = "", *, allow_same_version_rebuild: bool = False, channel: str = "") -> dict:
    # Legacy arguments are retained for old callers, never used as update sources.
    trust_policy = load_trust_policy()
    release = (desktop_releases.fetch_channel_release(channel) if channel else
               desktop_releases.fetch_preview_release() if update_channel() == "preview"
               else desktop_releases.fetch_stable_release())
    if not release or version_key(release["version"]) <= version_key(current_version()):
        return {}
    return {**release, "signer_certificate_sha256": trust_policy["signer_certificate_sha256"]}


def download_installer(manifest: dict, progress=None) -> Path:
    update_directory = application_data_directory() / "updates"
    update_directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(update_directory).free < manifest["size"] * 3 + 64 * 1024 * 1024:
        raise OSError("Недостаточно свободного места для скачивания и распаковки обновления.")
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"Manticore-Setup-{manifest['version']}-",
        suffix=".exe",
        dir=update_directory,
    )
    digest = hashlib.sha256()
    downloaded = 0
    try:
        request = urllib.request.Request(
            manifest["download_url"],
            headers={"User-Agent": f"Manticore-Desktop/{current_version()}"},
        )
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=30) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_INSTALLER_SIZE or downloaded > manifest["size"]:
                    raise ValueError("Размер загруженного установщика не совпадает с опубликованным.")
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(downloaded, manifest["size"])
        if downloaded != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise ValueError("Проверка целостности установщика не пройдена.")
        return Path(temporary_name)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def installed_scope_switch() -> str:
    """Keep an update in the same per-user or all-users scope as the old version."""
    if os.name != "nt":
        return "/CURRENTUSER"
    try:
        import winreg
    except ImportError:
        return "/CURRENTUSER"

    registry_views = (getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for hive, switch in ((winreg.HKEY_LOCAL_MACHINE, "/ALLUSERS"), (winreg.HKEY_CURRENT_USER, "/CURRENTUSER")):
        for view in registry_views:
            try:
                with winreg.OpenKey(hive, UNINSTALL_REGISTRY_KEY, 0, winreg.KEY_READ | view) as key:
                    location, _ = winreg.QueryValueEx(key, "InstallLocation")
                    if Path(location).resolve() == Path(sys.executable).resolve().parent:
                        return switch
            except OSError:
                continue
    return "/CURRENTUSER"


def launch_installer_after_exit(installer_path: Path, version: str = "") -> None:
    """Wait for this process to exit, then replace the existing installation safely."""
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    path_literal = literal(installer_path)
    executable = Path(sys.executable).resolve()
    log_path = application_data_directory() / "logs" / "installer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result_path = application_data_directory() / "updates" / "install-result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    scope_switch = installed_scope_switch()
    directory_argument = literal('/DIR="' + str(executable.parent) + '"')
    log_argument = literal('/LOG="' + str(log_path) + '"')
    parent_wait = ""
    if getattr(sys, "frozen", False):
        parent_wait = f"if (Get-Process -Id {os.getppid()} -ErrorAction SilentlyContinue) {{ Wait-Process -Id {os.getppid()} -Timeout 120 }}; "
    arguments = (
        "@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"
        f"'{scope_switch}','/CLOSEAPPLICATIONS','/NORESTARTAPPLICATIONS',"
        f"{directory_argument},{log_argument})"
    )
    command = (
        f"$ErrorActionPreference='Stop'; $result=@{{ok=$false;exit_code=-1;version={literal(version)}}}; "
        "try { "
        f"if (Get-Process -Id {os.getpid()} -ErrorAction SilentlyContinue) {{ Wait-Process -Id {os.getpid()} -Timeout 120 }}; "
        + parent_wait +
        f"$installer = Start-Process -FilePath {path_literal} -ArgumentList {arguments} -WindowStyle Hidden -PassThru -Wait; "
        "$result.exit_code=$installer.ExitCode; $result.ok=($installer.ExitCode -eq 0); "
        "} catch { $result.message=$_.Exception.Message }; "
        f"$result | ConvertTo-Json | Set-Content -LiteralPath {literal(result_path)} -Encoding UTF8; "
        f"Start-Process -FilePath {literal(executable)} -ArgumentList '--skip-update' -WindowStyle Hidden"
    )
    encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # PowerShell outlives this onefile process and launches the updated EXE.
    # Its child must unpack as a fresh application, not reuse the old process's
    # _PYI_* state and temporary directory after that process has exited.
    restart_environment = dict(os.environ)
    restart_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        [powershell_executable(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
        close_fds=True,
        creationflags=creation_flags,
        env=restart_environment,
    )


def win_verify_trust(installer_path: Path) -> int:
    """Return the unsigned WinVerifyTrust result for an Authenticode-signed file."""
    if os.name != "nt":
        raise OSError("Проверка Authenticode доступна только в Windows.")

    import ctypes
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    class WinTrustFileInfo(ctypes.Structure):
        _fields_ = [
            ("cb_struct", wintypes.DWORD),
            ("file_path", wintypes.LPCWSTR),
            ("file_handle", wintypes.HANDLE),
            ("known_subject", ctypes.POINTER(Guid)),
        ]

    class WinTrustData(ctypes.Structure):
        _fields_ = [
            ("cb_struct", wintypes.DWORD),
            ("policy_callback_data", wintypes.LPVOID),
            ("sip_client_data", wintypes.LPVOID),
            ("ui_choice", wintypes.DWORD),
            ("revocation_checks", wintypes.DWORD),
            ("union_choice", wintypes.DWORD),
            ("file_info", ctypes.POINTER(WinTrustFileInfo)),
            ("state_action", wintypes.DWORD),
            ("state_data", wintypes.HANDLE),
            ("url_reference", wintypes.LPCWSTR),
            ("provider_flags", wintypes.DWORD),
            ("ui_context", wintypes.DWORD),
        ]

    verify_action = Guid(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )
    file_path = str(Path(installer_path).resolve())
    file_info = WinTrustFileInfo(ctypes.sizeof(WinTrustFileInfo), file_path, None, None)
    trust_data = WinTrustData(
        ctypes.sizeof(WinTrustData),
        None,
        None,
        2,  # WTD_UI_NONE
        0,
        1,  # WTD_CHOICE_FILE
        ctypes.pointer(file_info),
        0,
        None,
        None,
        0,
        0,
    )
    verify = ctypes.WinDLL("wintrust", use_last_error=True).WinVerifyTrust
    verify.argtypes = [wintypes.HWND, ctypes.POINTER(Guid), ctypes.POINTER(WinTrustData)]
    verify.restype = ctypes.c_long
    result = verify(None, ctypes.byref(verify_action), ctypes.byref(trust_data))
    return ctypes.c_uint32(result).value


def verify_authenticode_signature(installer_path: Path, expected_signer_sha256: str) -> None:
    path_literal = "'" + str(installer_path).replace("'", "''") + "'"
    command = (
        "$securityModule = Join-Path $env:SystemRoot "
        "'System32\\WindowsPowerShell\\v1.0\\Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1'; "
        "Import-Module $securityModule -ErrorAction Stop; "
        f"$signature = Get-AuthenticodeSignature -LiteralPath {path_literal}; "
        "if ($null -eq $signature.SignerCertificate) { exit 2 }; "
        "$sha = [System.Security.Cryptography.SHA256]::Create(); "
        "try { $hash = [BitConverter]::ToString($sha.ComputeHash($signature.SignerCertificate.RawData)).Replace('-', '') } "
        "finally { $sha.Dispose() }; Write-Output $hash"
    )
    encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        [powershell_executable(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
        text=True,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output_lines = (completed.stdout or "").strip().splitlines()
    actual_hash = output_lines[-1].strip().lower() if output_lines else ""
    if completed.returncode != 0:
        raise ValueError("Windows-установщик не содержит проверяемой цифровой подписи.")
    if not hmac.compare_digest(actual_hash, expected_signer_sha256.lower()):
        raise ValueError("Установщик подписан не тем сертификатом издателя.")
    trust_result = win_verify_trust(installer_path)
    if trust_result not in {WINTRUST_SUCCESS, WINTRUST_UNTRUSTED_ROOT}:
        raise ValueError(
            "Цифровая подпись Windows-установщика повреждена или недействительна "
            f"(WinVerifyTrust 0x{trust_result:08X})."
        )


class DesktopUpdater:
    """Single serialized updater; renderer receives status, never executable paths."""

    def __init__(self, close_windows, channel=""):
        self._lock = threading.RLock()
        self._channel = channel
        self._close_windows = close_windows
        self._manifest = None
        self._installer = None
        self._state = {"state": "idle" if getattr(sys, "frozen", False) else "disabled",
                       "current_version": current_version(), "channel": channel or update_channel(), "version": "", "notes": "",
                       "downloaded": 0, "total": 0, "percent": 0, "error": ""}

    def status(self):
        with self._lock:
            return dict(self._state)

    def _set(self, **values):
        with self._lock:
            self._state.update(values)

    def _error(self, exc):
        logging.exception("[Updater] Operation failed")
        message = str(exc) if isinstance(exc, ValueError) else "Проверьте подключение к интернету и свободное место. Подробности — в журнале клиента."
        self._set(state="error", error=message)

    def check(self):
        with self._lock:
            if self._state["state"] in {"disabled", "checking", "downloading", "downloaded", "installing"}:
                return self.status()
            self._set(state="checking", error="", version="", notes="")
            threading.Thread(target=self._check, name="manticore-update-check", daemon=True).start()
            return self.status()

    def _check(self):
        try:
            logging.info("[Updater] Checking %s GitHub releases; current=%s", self._channel or update_channel(), current_version())
            manifest = fetch_update_manifest(channel=self._channel) if self._channel else fetch_update_manifest()
            with self._lock:
                self._manifest = manifest or None
                self._set(state="available" if manifest else "current", version=manifest.get("version", ""), notes=manifest.get("notes", ""))
            logging.info("[Updater] %s version=%s", self.status()["state"], self.status()["version"])
        except Exception as exc:
            self._error(exc)

    def download(self):
        with self._lock:
            if self._state["state"] != "available" or not self._manifest:
                return self.status()
            self._set(state="downloading", downloaded=0, total=self._manifest["size"], percent=0, error="")
            threading.Thread(target=self._download, name="manticore-update-download", daemon=True).start()
            return self.status()

    def _verify(self, path):
        manifest = self._manifest
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if path.stat().st_size != manifest["size"] or not hmac.compare_digest(digest, manifest["sha256"]):
            raise ValueError("Проверка целостности установщика не пройдена. Повторите проверку и скачивание.")
        if manifest["signer_certificate_sha256"]:
            verify_authenticode_signature(path, manifest["signer_certificate_sha256"])

    def _download(self):
        path = None
        try:
            last_logged = -1
            def progress(done, total):
                nonlocal last_logged
                percent = min(100, int(done * 100 / total))
                self._set(downloaded=done, total=total, percent=percent)
                if percent // 10 != last_logged:
                    logging.info("[Updater] Download %s%%", percent)
                    last_logged = percent // 10
            path = download_installer(self._manifest, progress)
            self._verify(path)
            self._installer = path
            self._set(state="downloaded", percent=100)
            logging.info("[Updater] Update downloaded and verified")
        except Exception as exc:
            if path:
                path.unlink(missing_ok=True)
            self._error(exc)

    def install(self):
        with self._lock:
            if self._state["state"] != "downloaded" or not self._installer:
                return self.status()
            self._set(state="installing")
        try:
            # Native confirmation also protects against unsolicited renderer calls.
            if not confirm_native_message("Обновление Manticore", f"Установить версию {self._manifest['version']} и перезапустить приложение? Сохраните незавершённую работу."):
                self._set(state="downloaded")
                return self.status()
            self._verify(self._installer)
            launch_installer_after_exit(self._installer, self._manifest["version"])
            logging.info("[Updater] Installing %s", self._manifest["version"])
            timer = threading.Timer(0.5, self._close_windows)
            timer.daemon = True
            timer.start()
        except Exception as exc:
            self._error(exc)
        return self.status()


class DesktopApi:
    """Operations that are safe to expose to pages opened in the desktop shell."""

    def __init__(self, update_server_url: str, target_url: str = "", source_config: dict | None = None):
        self.update_server_url = update_server_url
        self.target_url = target_url
        self.connection_error = ""
        self._channel_updaters = {channel: DesktopUpdater(self._close_windows, channel)
                                  for channel in ("stable", "preview")}
        self._updater = self._channel_updaters[update_channel()]
        self._update_action_lock = threading.RLock()
        self.source_config = migrate_config(source_config or load_config())
        self.source_switch_requested = False

    @staticmethod
    def _close_windows() -> None:
        import webview

        for window in list(webview.windows):
            try:
                window.destroy()
            except Exception:
                logging.exception("Could not close a desktop window for the update")

    def get_current_version(self) -> str:
        return current_version()

    def get_client_info(self) -> dict:
        config = migrate_config(load_config())
        try:
            primary = configured_source(config)
        except ValueError:
            primary = {}
        primary_value = primary.get("url") if primary.get("type") == "server" else primary.get("path")
        fallback_path = get_fallback_path(config) if primary else ""
        primary_check = inspect_source(primary) if primary else _source_result(SOURCE_UNKNOWN_ERROR, "Источник не настроен.")
        local_state = fallback_state(fallback_path) if fallback_path else {"state": "clean", "dirty": False, "changes": 0}
        return {
            "desktop": True,
            "version": current_version(),
            "webview_url": self.target_url,
            "mode": config.get("mode", ""),
            "server_url": config.get("server_url", ""),
            "database_path": config.get("database_path", ""),
            "update_server_url": config.get("update_server_url", ""),
            "log_path": str(application_data_directory() / "logs" / "client.log"),
            "primary_source": primary_value or "",
            "active_source": config.get("active_source", "primary"),
            "fallback_source": fallback_path,
            "preferred_source": config.get("preferred_source", "ask"),
            "primary_available": primary_check["available"],
            "primary_status": "Доступна" if primary_check["code"] == SOURCE_OK else primary_check["message"],
            "primary_status_code": primary_check["code"],
            "fallback_state": local_state["state"],
            "fallback_dirty": local_state["dirty"],
            "fallback_changes": local_state["changes"],
        }

    def check_primary_source(self) -> dict:
        config = migrate_config(load_config())
        result = inspect_source(configured_source(config))
        return {**result, "error": result["message"], "active_source": config.get("active_source", "primary")}

    def switch_source(self, target: str, remember: bool = False, confirm_unsynced: bool = False) -> dict:
        if target not in {"primary", "fallback"}:
            return {"ok": False, "error": "Неизвестный источник."}
        config = migrate_config(load_config())
        if target == "primary":
            error = check_source_connection(configured_source(config))
            if error:
                return {"ok": False, "error": error}
            local_state = fallback_state(get_fallback_path(config))
            if config.get("active_source") == "fallback" and local_state["dirty"]:
                if not confirm_unsynced:
                    return {
                        "ok": False,
                        "requires_confirmation": True,
                        "error": "В локальной базе имеются несинхронизированные изменения.",
                    }
                try:
                    archived = archive_dirty_fallback(config)
                except Exception as exc:
                    logging.exception("Could not archive dirty fallback")
                    return {"ok": False, "error": f"Не удалось создать резервную копию локальной базы: {exc}"}
        else:
            try:
                prepare_fallback_database(config)
            except (OSError, sqlite3.Error) as exc:
                return {"ok": False, "error": f"Fallback нельзя подготовить: {exc}"}
            ensure_fallback_session(config)
        config["active_source"] = target
        config["preferred_source"] = target if remember else "ask"
        save_config(config)
        logging.info("Desktop source switched to %s; remember=%s", target, remember)
        self.source_switch_requested = True
        self._close_windows()
        return {"ok": True, "restart_required": True}

    def get_fallback_changes(self, limit: int = 200) -> dict:
        config = migrate_config(load_config())
        path = Path(get_fallback_path(config))
        if not path.is_file():
            return {"ok": True, "state": "clean", "rows": []}
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    """
                    SELECT created_at, action, entity_type, entity_id, record_uuid, label
                    FROM fallback_change_log ORDER BY id DESC LIMIT ?
                    """,
                    (max(1, min(int(limit or 200), 1000)),),
                ).fetchall()
            finally:
                connection.close()
            return {"ok": True, "state": fallback_state(str(path))["state"], "rows": [dict(row) for row in rows]}
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc), "rows": []}

    def reset_source_preference(self) -> dict:
        config = migrate_config(load_config())
        config["preferred_source"] = "ask"
        save_config(config)
        return {"ok": True}

    def _selected_updater(self, channel):
        if channel == "":
            return self._updater
        if not isinstance(channel, str) or channel not in self._channel_updaters:
            raise ValueError("Неизвестный канал обновлений.")
        return self._channel_updaters[channel]

    def get_update_status(self, channel="") -> dict:
        return self._selected_updater(channel).status()

    def get_update_channels(self) -> dict:
        return {channel: updater.status() for channel, updater in self._channel_updaters.items()}

    def _update_action(self, channel, action):
        with self._update_action_lock:
            selected = self._selected_updater(channel)
            if any(updater.status()["state"] == "installing"
                   for updater in self._channel_updaters.values()):
                return selected.status()
            return getattr(selected, action)()

    def check_for_update(self, channel="") -> dict:
        return self._update_action(channel, "check")

    def download_update(self, channel="") -> dict:
        return self._update_action(channel, "download")

    def open_log(self) -> dict:
        log_path = application_data_directory() / "logs" / "client.log"
        try:
            if os.name == "nt":
                os.startfile(str(log_path))
            else:
                return {"ok": False, "error": str(log_path)}
        except OSError as exc:
            logging.warning("Could not open client log: %s", exc)
            return {"ok": False, "error": "Не удалось открыть журнал клиента."}
        return {"ok": True}

    def reconfigure(self) -> dict:
        configured = show_configuration_dialog(load_config())
        return {"saved": configured is not None, "restart_required": configured is not None}

    def get_connection_state(self) -> dict:
        return {"url": self.target_url, "error": self.connection_error}

    def retry_connection(self) -> dict:
        error = check_server_connection(self.target_url)
        if error:
            self.connection_error = error
            return {"ok": False, "error": error}
        self.connection_error = ""
        import webview

        if webview.windows:
            webview.windows[0].load_url(self.target_url)
        return {"ok": True}

    def install_approved_update(self, channel="") -> dict:
        return self._update_action(channel, "install")


def check_server_connection(url: str, timeout: float = 5.0) -> str:
    parsed = urllib.parse.urlsplit(url)
    if is_loopback_host(parsed.hostname):
        return ""
    health_url = url.rstrip("/") + "/healthz"
    request = urllib.request.Request(health_url, headers={"User-Agent": f"Manticore-Desktop/{current_version()}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if getattr(response, "status", 200) >= 500:
                return "Сервер отвечает ошибкой. Повторите подключение позднее."
            return ""
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": f"Manticore-Desktop/{current_version()}"}), timeout=timeout) as response:
                    return "" if getattr(response, "status", 200) < 500 else "Сервер отвечает ошибкой."
            except Exception as fallback_exc:
                logging.warning("Remote server compatibility check failed for %s: %s", url, fallback_exc)
                return "Сервер не прошёл проверку доступности."
        logging.warning("Remote server health check returned HTTP %s for %s", exc.code, url)
        return "Сервер не прошёл проверку доступности."
    except urllib.error.URLError as exc:
        logging.warning("Remote server connection failed for %s: %s", url, exc)
        reason = str(getattr(exc, "reason", exc))
        return f"Сервер не ответил. Проверьте подключение к сети, адрес сервера и сертификат TLS. ({reason})"
    except (OSError, TimeoutError) as exc:
        logging.warning("Remote server connection failed for %s: %s", url, exc)
        return "Сервер не ответил вовремя. Проверьте сеть и повторите попытку."


class LocalServer:
    def __init__(self, database_path: str, admin_password: str | None, secret_key: str,
                 *, fallback_session_id: str = "", primary_source_id: str = ""):
        database = Path(database_path)
        os.environ["UPLOAD_FOLDER"] = str(database.parent)
        os.environ["DB_FILENAME"] = database.name
        os.environ["SECRET_KEY"] = secret_key
        os.environ["APP_HOST"] = "127.0.0.1"
        os.environ["APP_DEBUG"] = "false"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        os.environ["APP_UPDATE_ENABLED"] = "false"
        if fallback_session_id:
            os.environ["MANTICORE_FALLBACK_SESSION_ID"] = fallback_session_id
            os.environ["MANTICORE_PRIMARY_SOURCE_ID"] = primary_source_id
        else:
            os.environ.pop("MANTICORE_FALLBACK_SESSION_ID", None)
            os.environ.pop("MANTICORE_PRIMARY_SOURCE_ID", None)
        if admin_password:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = admin_password

        import app as manticore_app
        from werkzeug.serving import make_server

        os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        self._server = make_server("127.0.0.1", 0, manticore_app.app, threaded=True)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, name="manticore-local-server", daemon=True)
        self._started = False

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        shutdown_thread = threading.Thread(
            target=self._server.shutdown,
            name="manticore-local-server-shutdown",
            daemon=True,
        )
        shutdown_thread.start()
        shutdown_thread.join(timeout=5)
        if shutdown_thread.is_alive():
            logging.warning("Local Flask server shutdown call did not finish within timeout")
        else:
            self._server.server_close()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            logging.warning("Local Flask server did not stop within timeout")
        else:
            logging.info("Local Flask server stopped")
        self._started = False

    def is_alive(self) -> bool:
        return self._started and self._thread.is_alive()


class DesktopLifecycle:
    """Own the tray, window shutdown, and local server as one lifecycle."""

    def __init__(self, config: dict, local_server: LocalServer | None = None):
        self.config = dict(config)
        self.local_server = local_server
        self._lock = threading.RLock()
        self._state = "STARTING"
        self._shutdown_requested = False
        self._cleanup_started = False
        self._tray = None
        self._timers: list[threading.Timer] = []

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def mark_running(self) -> None:
        with self._lock:
            if not self._shutdown_requested:
                self._state = "RUNNING"

    def register_timer(self, timer: threading.Timer) -> None:
        with self._lock:
            if self._shutdown_requested:
                timer.cancel()
            else:
                self._timers.append(timer)

    def start_tray(self) -> bool:
        """Show the bundled Manticore icon; failures never abort the desktop."""
        try:
            import pystray
            from PIL import Image

            icon_path = bundle_root() / WINDOW_ICON_PATH
            image = Image.open(icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("Открыть Manticore", self._tray_open, default=True),
                pystray.MenuItem("Состояние", self._tray_status),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Завершить Manticore", self._tray_shutdown),
            )
            tooltip = f"Manticore {current_version()} — запущено"
            self._tray = pystray.Icon("Manticore", image, tooltip, menu)
            logging.info("Tray initialized")
            self._tray.run_detached()
            logging.info("Tray icon visible")
            return True
        except Exception:
            self._tray = None
            logging.exception("Tray initialization failed; desktop will continue without tray")
            return False

    def _tray_open(self, _icon=None, _item=None) -> None:
        logging.info("Tray open requested")
        if not activate_desktop_window():
            show_native_message(
                "Manticore",
                "Интерфейс Manticore недоступен. Завершите приложение и запустите его снова.",
                error=True,
            )

    def _tray_status(self, _icon=None, _item=None) -> None:
        with self._lock:
            state = self._state
        source = "Fallback" if self.config.get("active_source") == "fallback" else "Primary"
        lines = ["Manticore работает", f"Версия: {current_version()}", f"Источник: {source}"]
        if self.local_server is not None:
            lines.append(
                "Внутренний сервер: доступен"
                if self.local_server.is_alive()
                else "Внутренний сервер Manticore недоступен."
            )
        if state == "SHUTTING_DOWN":
            lines[0] = "Manticore завершает работу"
        show_native_message("Состояние Manticore", "\n".join(lines), error=state == "ERROR")

    def _tray_shutdown(self, _icon=None, _item=None) -> None:
        logging.info("Tray shutdown requested")
        self.shutdown_application(close_window=True)

    def shutdown_application(self, *, close_window: bool = False) -> bool:
        """Idempotently stop every resource owned by the desktop process."""
        with self._lock:
            first_request = not self._shutdown_requested
            self._shutdown_requested = True
            self._state = "SHUTTING_DOWN"
            if close_window:
                if not first_request:
                    return False
            elif self._cleanup_started:
                return False
            else:
                self._cleanup_started = True
        if first_request:
            logging.info("Desktop shutdown started")
        if close_window:
            if not close_desktop_windows():
                logging.warning("Shutdown requested with no available Manticore window")
            # The main/UI thread completes cleanup after WebView's loop exits.
            return True
        with self._lock:
            timers, self._timers = self._timers, []
        for timer in timers:
            timer.cancel()
        if self.local_server is not None:
            try:
                self.local_server.stop()
            except Exception:
                logging.exception("Could not stop the local Flask server")
        tray = self._tray
        self._tray = None
        if tray is not None:
            try:
                tray.stop()
                # pystray creates a non-daemon setup_handler before the native
                # backend reports ready. If the backend exits (or shutdown wins
                # that race), Icon.stop() is a no-op and Python waits forever
                # for setup_handler at interpreter shutdown. Release the private
                # readiness queue only during teardown, then verify the thread
                # has actually exited.
                setup_thread = getattr(tray, "_setup_thread", None)
                if isinstance(setup_thread, threading.Thread) and setup_thread.is_alive():
                    readiness_queue = getattr(tray, "_Icon__queue", None)
                    if readiness_queue is not None:
                        readiness_queue.put(False)
                    setup_thread.join(timeout=2)
                if isinstance(setup_thread, threading.Thread) and setup_thread.is_alive():
                    logging.error("Tray setup thread did not stop during shutdown")
                else:
                    logging.info("Tray stopped")
            except Exception:
                logging.exception("Could not stop tray")
        return True


def open_desktop_window(url: str, update_server_url: str | None = None, *, skip_update: bool = False,
                        source_config: dict | None = None, lifecycle: DesktopLifecycle | None = None) -> bool:
    import webview

    # The renderer boundary accepts HTTP(S) only. A filesystem source can
    # therefore never accidentally reach WebView2 as its initial URL.
    webview_url = normalize_server_url(url)
    if source_config:
        try:
            source = configured_source(source_config)
        except ValueError:
            source = {}
        if source.get("type") == "sqlite" and not is_loopback_host(urllib.parse.urlsplit(webview_url).hostname):
            raise ValueError("Для SQLite WebView может открывать только локальный Flask на 127.0.0.1.")

    # pywebview disables downloads by default. On Windows, enabling this delegates
    # HTTP, authenticated and blob downloads to WebView2's native streaming
    # download handler and Save As dialog, preserving the response filename.
    webview.settings["ALLOW_DOWNLOADS"] = True
    # Links with target="_blank" use pywebview's NewWindowRequested handler:
    # Windows opens HTTPS in the system browser and mailto in the mail app.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True

    storage_path = application_data_directory() / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    api = DesktopApi(update_server_url or webview_url, webview_url, source_config)
    startup_page = bundle_root() / "desktop" / "ui" / "startup.html"
    error_page = bundle_root() / "desktop" / "ui" / "connection_error.html"
    window = webview.create_window(
        f"Manticore {current_version()}",
        str(startup_page),
        width=1360,
        height=860,
        min_size=(1024, 640),
        text_select=True,
        js_api=api,
    )

    def install_updater_ui() -> None:
        # Keep the updater usable with older remote server templates as well.
        try:
            current = window.get_current_url() or ""
            target = urllib.parse.urlsplit(webview_url)
            page = urllib.parse.urlsplit(current)
            # pywebview serves local HTML through its private loopback server.
            # Resolve only our two fixed bundled paths, never a renderer-supplied URL.
            bundled_page = current in {window._resolve_url(str(startup_page)), window._resolve_url(str(error_page))}
            if bundled_page or (page.scheme, page.netloc) == (target.scheme, target.netloc):
                script = (bundle_root() / "static" / "js" / "desktop-updater.js").read_text(encoding="utf-8")
                source_script = (bundle_root() / "static" / "js" / "desktop-source.js").read_text(encoding="utf-8")
                window.evaluate_js(script + "\n" + source_script)
        except Exception:
            logging.exception("[Updater] Could not initialize bundled update UI")

    window.events.loaded += install_updater_ui

    def finish_startup() -> None:
        connection_error = check_server_connection(webview_url)
        if connection_error:
            api.connection_error = connection_error
            window.load_url(str(error_page))
        else:
            window.load_url(webview_url)
        if lifecycle is not None:
            lifecycle.mark_running()
        if not skip_update and getattr(sys, "frozen", False):
            timer = threading.Timer(4, api.check_for_update)
            timer.daemon = True
            if lifecycle is not None:
                lifecycle.register_timer(timer)
            timer.start()

    webview.start(
        finish_startup,
        private_mode=False,
        storage_path=str(storage_path),
        icon=str(bundle_root() / WINDOW_ICON_PATH),
    )
    return api.source_switch_requested


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manticore Windows desktop client")
    parser.add_argument("--configure", action="store_true", help="show connection settings")
    parser.add_argument("--skip-update", action="store_true", help="skip the update check for this launch")
    parser.add_argument("--configuration-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--admin-password-child", metavar="RESULT_PATH", help=argparse.SUPPRESS)
    parser.add_argument("--source-dialog-child", nargs=2, metavar=("STATE_PATH", "RESULT_PATH"), help=argparse.SUPPRESS)
    parser.add_argument("--wait-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--build-info-file", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--preflight-database", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--preflight-result", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--startup-smoke-database", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--startup-smoke-result", metavar="PATH", help=argparse.SUPPRESS)
    parser.add_argument("--create-if-missing", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def bundled_build_metadata() -> dict:
    path = bundle_root() / "build-metadata.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            return payload
    except (OSError, ValueError):
        pass
    return {"version": current_version(), "schema_version": CURRENT_SCHEMA_VERSION, "commit": "development"}


def write_json_result(path: str, payload: dict) -> None:
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def run_packaged_preflight(args: argparse.Namespace) -> int:
    source = {
        "type": "sqlite",
        "path": normalize_database_path(args.preflight_database, create_parent=False),
        "create_if_missing": bool(args.create_if_missing),
    }
    result = inspect_sqlite_source(source["path"])
    allowed = result["code"] == SOURCE_OK or (
        result["code"] == SOURCE_FILE_NOT_FOUND and source["create_if_missing"]
    )
    message = (
        database_compatibility_message(source, result)
        if result["code"] in {SOURCE_SCHEMA_TOO_NEW, SOURCE_SCHEMA_MISMATCH}
        else result.get("message", "")
    )
    payload = {
        **bundled_build_metadata(),
        **result,
        "allowed": allowed,
        "message": message,
        "path": source["path"],
        "create_if_missing": source["create_if_missing"],
    }
    if args.preflight_result:
        write_json_result(args.preflight_result, payload)
    return 0 if allowed else 3


def run_packaged_startup_smoke(args: argparse.Namespace) -> int:
    """Exercise packaged app import, database initialization and HTTP startup."""
    source = {
        "type": "sqlite",
        "path": normalize_database_path(args.startup_smoke_database, create_parent=False),
        "create_if_missing": bool(args.create_if_missing),
    }
    result = inspect_sqlite_source(source["path"])
    allowed = result["code"] == SOURCE_OK or (
        result["code"] == SOURCE_FILE_NOT_FOUND and source["create_if_missing"]
    )
    payload = {
        **bundled_build_metadata(),
        **result,
        "allowed": allowed,
        "message": (
            database_compatibility_message(source, result)
            if result["code"] in {SOURCE_SCHEMA_TOO_NEW, SOURCE_SCHEMA_MISMATCH}
            else result.get("message", "")
        ),
        "path": source["path"],
        "create_if_missing": source["create_if_missing"],
        "startup": "blocked",
    }
    if not allowed:
        if args.startup_smoke_result:
            write_json_result(args.startup_smoke_result, payload)
        return 3

    local_server = None
    try:
        require_safe_local_source(source)
        admin_password = None if database_has_admin(source["path"]) else secrets.token_urlsafe(32)
        require_safe_local_source(source)
        local_server = LocalServer(source["path"], admin_password, secrets.token_urlsafe(48))
        local_server.start()
        with urllib.request.urlopen(local_server.url + "/healthz", timeout=10) as response:
            status = int(getattr(response, "status", 200))
            if status >= 400:
                raise RuntimeError(f"Packaged health check returned HTTP {status}")
        final_result = inspect_sqlite_source(source["path"])
        if final_result["code"] != SOURCE_OK:
            raise DatabaseSafetyError(final_result.get("message") or "Post-startup database check failed.")
        payload.update(final_result)
        payload.update({"allowed": True, "startup": "ok", "http_status": status})
        exit_code = 0
    except DatabaseSafetyError as exc:
        payload.update({"allowed": False, "startup": "blocked", "message": str(exc)})
        exit_code = 3
    except Exception as exc:
        payload.update({"allowed": False, "startup": "failed", "message": str(exc)})
        exit_code = 4
    finally:
        if local_server is not None:
            local_server.stop()
    if args.startup_smoke_result:
        write_json_result(args.startup_smoke_result, payload)
    return exit_code


def wait_for_process_exit(process_id: int) -> None:
    if not process_id or process_id == os.getpid():
        return
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, process_id)
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 15000)
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            os.kill(process_id, 0)
        except OSError:
            break
        time.sleep(0.1)


def relaunch_after_source_switch(args: argparse.Namespace) -> None:
    arguments = ["--wait-pid", str(os.getpid())]
    if args.skip_update:
        arguments.append("--skip-update")
    subprocess.Popen(
        desktop_client_command(*arguments),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        close_fds=True,
    )


def require_safe_local_source(source: dict) -> dict:
    """Repeat the read-only preflight immediately before importing the web app."""
    result = inspect_sqlite_source(str(source.get("path") or ""))
    code = result["code"]
    if code == SOURCE_OK:
        return result
    if code == SOURCE_FILE_NOT_FOUND and source.get("create_if_missing"):
        return result
    message = (
        database_compatibility_message(source, result)
        if code in {SOURCE_SCHEMA_TOO_NEW, SOURCE_SCHEMA_MISMATCH}
        else result.get("message") or "Проверка базы данных не пройдена."
    )
    if code == SOURCE_SCHEMA_TOO_NEW:
        raise DatabaseSchemaTooNewError(message)
    if code == SOURCE_SCHEMA_MISMATCH:
        raise DatabaseSchemaMismatchError(message)
    if code == SOURCE_DATABASE_CORRUPT:
        raise DatabaseIntegrityError(message)
    if code == SOURCE_LOCKED:
        raise DatabaseLockedError(message)
    raise DatabaseSafetyError(message)


def show_database_safety_error(error: DatabaseSafetyError) -> None:
    logging.error("Desktop database startup blocked before write: %s", error)
    show_native_message(
        "Manticore — база данных недоступна",
        f"{error}\n\nБаза данных не изменялась. Подробности записаны в журнал клиента.",
        error=True,
    )


def run_configured_client(config: dict, args: argparse.Namespace) -> int:
    source, config = resolve_active_source(config)
    if source is None:
        return 0
    config["local_secret_key"] = config.get("local_secret_key") or secrets.token_urlsafe(48)
    save_config(config)
    if source["type"] == "server":
        target_url = normalize_server_url(source.get("url", ""))
        lifecycle = DesktopLifecycle(config)
        lifecycle.start_tray()
        try:
            switched = open_desktop_window(
                target_url,
                target_url,
                skip_update=args.skip_update,
                source_config=config,
                lifecycle=lifecycle,
            )
        finally:
            lifecycle.shutdown_application()
        if switched:
            relaunch_after_source_switch(args)
        return 0

    database_path = normalize_database_path(source.get("path") or default_database_path())
    source = {**source, "path": database_path}
    try:
        require_safe_local_source(source)
        admin_password = None
        if not database_has_admin(database_path):
            admin_password = prompt_initial_admin_password()
            if not admin_password:
                return 1
        # The password dialog can remain open for an arbitrary time. Repeat the
        # preflight so a replaced/locked/newer file can never reach app import.
        require_safe_local_source(source)
        secret_key = config.get("local_secret_key") or secrets.token_urlsafe(48)
        config.update({"local_secret_key": secret_key})
        save_config(config)

        is_fallback = bool(source.get("fallback"))
        fallback_session_id = ensure_fallback_session(config) if is_fallback else ""
        if is_fallback:
            save_config(config)
        local_server = LocalServer(
            database_path,
            admin_password,
            secret_key,
            fallback_session_id=fallback_session_id,
            primary_source_id=source_identifier(configured_source(config)) if is_fallback else "",
        )
    except (DatabaseSchemaTooNewError, DatabaseSchemaMismatchError, DatabaseIntegrityError,
            DatabaseLockedError, InsufficientDiskSpaceError, DatabaseSafetyError) as exc:
        show_database_safety_error(exc)
        return 1
    lifecycle = DesktopLifecycle(config, local_server)
    lifecycle.start_tray()
    try:
        local_server.start()
        primary = configured_source(config)
        if source.get("type") == "sqlite" and source.get("path") == primary.get("path"):
            config["database_source"].pop("create_if_missing", None)
            save_config(config)
        update_server = config.get("update_server_url") or local_server.url
        runtime_config = {**config, "webview_url": local_server.url}
        switched = open_desktop_window(
            local_server.url,
            update_server,
            skip_update=args.skip_update,
            source_config=runtime_config,
            lifecycle=lifecycle,
        )
    finally:
        lifecycle.shutdown_application()
    if switched:
        relaunch_after_source_switch(args)
    return 0


def run_primary_instance(args: argparse.Namespace) -> int:
    result_path = application_data_directory() / "updates" / "install-result.json"
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            if not result.get("ok") or result.get("version") != current_version():
                logging.error("[Updater] Installer did not complete: %s", result)
                show_native_message("Обновление Manticore", "Обновление не завершено: установка отменена, заблокирована Windows или требует перезагрузки. Подробности: logs/installer.log в папке данных Manticore.", error=True)
            else:
                logging.info("[Updater] Successfully restarted version %s", current_version())
            result_path.unlink()
        except (OSError, ValueError):
            logging.exception("[Updater] Could not read installation result")
    loaded_config = load_config()
    try:
        config = migrate_config(loaded_config)
    except AmbiguousSourceError as exc:
        logging.warning("Desktop source requires an explicit type choice: %s", exc)
        config = {**loaded_config, "mode": "", "configuration_error": str(exc)}
    if args.configure or config.get("mode") not in {"remote", "local"}:
        configured = show_configuration_dialog(config)
        if configured is None:
            return 0
        configured["local_secret_key"] = config.get("local_secret_key") or secrets.token_urlsafe(48)
        config = migrate_config(configured)
        save_config(config)
    elif config:
        save_config(config)
    return run_configured_client(config, args)


def main() -> int:
    args = parse_arguments()
    if args.build_info_file:
        write_json_result(args.build_info_file, bundled_build_metadata())
        return 0
    if args.preflight_database:
        return run_packaged_preflight(args)
    if args.startup_smoke_database:
        return run_packaged_startup_smoke(args)
    configure_logging()
    cleanup_old_installers()
    if args.source_dialog_child:
        return run_source_dialog_child(*args.source_dialog_child)
    if args.wait_pid:
        wait_for_process_exit(args.wait_pid)
    if args.configuration_child:
        return run_setup_child("configuration")
    if args.admin_password_child:
        return run_setup_child("admin-password", args.admin_password_child)
    if not acquire_single_instance():
        if not signal_existing_instance():
            show_native_message("Manticore", "Приложение уже запущено, но его окно недоступно.")
        return 0
    start_instance_activation_listener()
    try:
        return run_primary_instance(args)
    finally:
        stop_instance_activation_listener()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatabaseSafetyError as exc:
        show_database_safety_error(exc)
        raise SystemExit(1)
    except Exception as exc:
        logging.exception("Desktop client stopped unexpectedly")
        show_native_message(
            "Manticore",
            f"Не удалось запустить приложение.\n\n{exc}\n\nПодробности записаны в журнал клиента.",
            error=True,
        )
        raise SystemExit(1)
