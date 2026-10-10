"""Security-sensitive application constants shared by web and update entrypoints."""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass


MOODLE_PASSWORD_PLACEHOLDER = "cron"
TRUSTED_GITHUB_REPOSITORY = "UnicornisIT/manticore"
TRUSTED_RELEASE_API_URL = (
    f"https://api.github.com/repos/{TRUSTED_GITHUB_REPOSITORY}/releases/latest"
)
RELEASE_TAG_PATTERN = re.compile(r"v?\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?")
DEFAULT_BACKUP_RETENTION_DAYS = 30

TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSY_SETTING_VALUES = frozenset({"0", "false", "no", "off"})


def env_bool(
    name: str,
    default: bool = False,
    environ: Mapping[str, str] | None = None,
) -> bool:
    source = os.environ if environ is None else environ
    value = source.get(name)
    if value is None:
        return default
    return value.strip().casefold() in TRUTHY_ENV_VALUES


def env_int(
    name: str,
    default: int,
    minimum: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    source = os.environ if environ is None else environ
    try:
        value = int(source.get(name, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def setting_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in TRUTHY_ENV_VALUES:
        return True
    if normalized in FALSY_SETTING_VALUES:
        return False
    return default


@dataclass(frozen=True)
class RuntimeConfig:
    secret_key: str
    upload_folder: str
    db_filename: str
    max_content_length: int
    session_cookie_samesite: str
    session_cookie_secure: bool
    app_update_enabled: bool
    app_update_stale_seconds: int
    desktop_github_repository: str
    enable_hsts: bool
    hsts_max_age: int
    hsts_include_subdomains: bool
    hsts_preload: bool
    backup_retention_days: int
    fallback_session_id: str
    fallback_primary_id: str


def load_runtime_config(
    default_desktop_repository: str,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    source = os.environ if environ is None else environ
    max_upload_mb = env_int("MAX_UPLOAD_SIZE_MB", 16, minimum=1, environ=source)
    max_content_length = env_int(
        "MAX_CONTENT_LENGTH",
        max_upload_mb * 1024 * 1024,
        minimum=1024,
        environ=source,
    )
    return RuntimeConfig(
        secret_key=source.get("SECRET_KEY") or secrets.token_hex(32),
        upload_folder=source.get("UPLOAD_FOLDER", "uploads"),
        db_filename=source.get("DB_FILENAME", "baze.db"),
        max_content_length=max_content_length,
        session_cookie_samesite=source.get("SESSION_COOKIE_SAMESITE", "Lax"),
        session_cookie_secure=env_bool("SESSION_COOKIE_SECURE", False, source),
        app_update_enabled=env_bool("APP_UPDATE_ENABLED", os.name != "nt", source),
        app_update_stale_seconds=env_int(
            "APP_UPDATE_STALE_SECONDS", 3600, minimum=60, environ=source
        ),
        desktop_github_repository=source.get(
            "DESKTOP_GITHUB_REPOSITORY", default_desktop_repository
        ).strip(),
        enable_hsts=env_bool("ENABLE_HSTS", False, source),
        hsts_max_age=env_int("HSTS_MAX_AGE", 31536000, minimum=0, environ=source),
        hsts_include_subdomains=env_bool("HSTS_INCLUDE_SUBDOMAINS", False, source),
        hsts_preload=env_bool("HSTS_PRELOAD", False, source),
        backup_retention_days=env_int(
            "BACKUP_RETENTION_DAYS",
            DEFAULT_BACKUP_RETENTION_DAYS,
            minimum=1,
            environ=source,
        ),
        fallback_session_id=source.get("MANTICORE_FALLBACK_SESSION_ID", "").strip(),
        fallback_primary_id=source.get("MANTICORE_PRIMARY_SOURCE_ID", "").strip(),
    )
