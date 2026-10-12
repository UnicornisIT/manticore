"""Process-scoped, isolated Flask application fixture for integration tests."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT_PATH = Path(__file__).resolve().parents[1]
TEST_UPLOAD_PATH = Path(tempfile.mkdtemp(prefix="manticore_tests_"))


def _cleanup_test_workspace() -> None:
    shutil.rmtree(TEST_UPLOAD_PATH, ignore_errors=True)


atexit.register(_cleanup_test_workspace)

# Force the application to use the process-scoped test directory even when the
# developer shell contains production-like Manticore variables. These values
# must be present before importing app.py because it initializes SQLite then.
os.environ.update(
    {
        "SECRET_KEY": "test-secret-key",
        "ADMIN_DEFAULT_PASSWORD": "test-admin-password",
        "UPLOAD_FOLDER": str(TEST_UPLOAD_PATH),
        "DB_FILENAME": "test.db",
        "DEFAULT_CAMPAIGN_YEAR": "2026",
        "LEGACY_CAMPAIGN_YEAR": "2025",
        "APP_DEBUG": "false",
        "EMAIL_DNS_DISABLE": "1",
    }
)

project_root_text = str(PROJECT_ROOT_PATH)
if project_root_text not in sys.path:
    sys.path.insert(0, project_root_text)

import app as manticore  # noqa: E402


manticore.app.config["TESTING"] = True

PROJECT_ROOT = project_root_text
TEST_UPLOAD_DIR = str(TEST_UPLOAD_PATH)
