"""Security-sensitive application constants shared by web and update entrypoints."""

from __future__ import annotations

import re


MOODLE_PASSWORD_PLACEHOLDER = "cron"
TRUSTED_GITHUB_REPOSITORY = "UnicornisIT/manticore"
TRUSTED_RELEASE_API_URL = (
    f"https://api.github.com/repos/{TRUSTED_GITHUB_REPOSITORY}/releases/latest"
)
RELEASE_TAG_PATTERN = re.compile(
    r"v?\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
)
DEFAULT_BACKUP_RETENTION_DAYS = 30
