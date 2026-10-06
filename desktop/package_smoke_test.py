"""Verify metadata and read-only SQLite preflight through the packaged EXE."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_safety import CURRENT_SCHEMA_VERSION


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_executable(executable: Path, *arguments: str, expected_exit: int) -> None:
    completed = subprocess.run(
        [str(executable), *arguments],
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != expected_exit:
        raise RuntimeError(
            f"Packaged executable returned {completed.returncode}; expected {expected_exit}: {arguments}"
        )


def create_versioned_database(path: Path, metadata_version: int, pragma_version: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE schema_metadata ("
            "id INTEGER PRIMARY KEY CHECK (id=1), version INTEGER NOT NULL, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_metadata (id, version, updated_at) VALUES (1, ?, datetime('now'))",
            (metadata_version,),
        )
        connection.execute(f"PRAGMA user_version={int(pragma_version)}")
        connection.commit()
    finally:
        connection.close()


def read_result(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)

    with tempfile.TemporaryDirectory(prefix="manticore-packaged-smoke-") as directory:
        root = Path(directory)
        build_info_path = root / "build-info.json"
        run_executable(executable, "--build-info-file", str(build_info_path), expected_exit=0)
        build_info = read_result(build_info_path)
        expected_metadata = {
            "version": args.expected_version,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "commit": args.expected_commit,
        }
        if any(build_info.get(key) != value for key, value in expected_metadata.items()):
            raise RuntimeError(f"Packaged build metadata mismatch: {build_info!r} != {expected_metadata!r}")

        current = root / "current.db"
        create_versioned_database(current, CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION)
        current_before = sha256(current)
        current_result_path = root / "current-result.json"
        run_executable(
            executable,
            "--preflight-database", str(current),
            "--preflight-result", str(current_result_path),
            expected_exit=0,
        )
        current_result = read_result(current_result_path)
        current_after = sha256(current)
        if current_result.get("code") != "SOURCE_OK" or not current_result.get("allowed"):
            raise RuntimeError(f"Current schema was not accepted: {current_result!r}")
        if current_before != current_after:
            raise RuntimeError("Current-schema database changed during packaged preflight")

        startup_database = root / "startup.db"
        startup_result_path = root / "startup-result.json"
        run_executable(
            executable,
            "--startup-smoke-database", str(startup_database),
            "--startup-smoke-result", str(startup_result_path),
            "--create-if-missing",
            expected_exit=0,
        )
        startup_result = read_result(startup_result_path)
        if startup_result.get("startup") != "ok" or startup_result.get("http_status") != 200:
            raise RuntimeError(f"Packaged application startup failed: {startup_result!r}")
        connection = sqlite3.connect(startup_database)
        try:
            metadata_version = connection.execute(
                "SELECT version FROM schema_metadata WHERE id=1"
            ).fetchone()[0]
            pragma_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
        if metadata_version != CURRENT_SCHEMA_VERSION or pragma_version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"Packaged startup created schema {metadata_version}/{pragma_version}; "
                f"expected {CURRENT_SCHEMA_VERSION}"
            )
        startup_existing_result_path = root / "startup-existing-result.json"
        run_executable(
            executable,
            "--startup-smoke-database", str(startup_database),
            "--startup-smoke-result", str(startup_existing_result_path),
            expected_exit=0,
        )
        startup_existing_result = read_result(startup_existing_result_path)
        if startup_existing_result.get("startup") != "ok":
            raise RuntimeError(f"Current packaged database did not reopen: {startup_existing_result!r}")

        newer = root / "newer.db"
        create_versioned_database(newer, CURRENT_SCHEMA_VERSION + 1, CURRENT_SCHEMA_VERSION + 1)
        newer_before = sha256(newer)
        newer_result_path = root / "newer-result.json"
        run_executable(
            executable,
            "--startup-smoke-database", str(newer),
            "--startup-smoke-result", str(newer_result_path),
            "--create-if-missing",
            expected_exit=3,
        )
        newer_result = read_result(newer_result_path)
        newer_after = sha256(newer)
        if newer_result.get("code") != "SOURCE_SCHEMA_TOO_NEW" or newer_result.get("allowed"):
            raise RuntimeError(f"Newer schema was not blocked: {newer_result!r}")
        if newer_before != newer_after:
            raise RuntimeError("Newer-schema database changed during packaged preflight")

        mismatch = root / "mismatch.db"
        create_versioned_database(mismatch, CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION - 1)
        mismatch_before = sha256(mismatch)
        mismatch_result_path = root / "mismatch-result.json"
        run_executable(
            executable,
            "--startup-smoke-database", str(mismatch),
            "--startup-smoke-result", str(mismatch_result_path),
            expected_exit=3,
        )
        mismatch_result = read_result(mismatch_result_path)
        mismatch_after = sha256(mismatch)
        if mismatch_result.get("code") != "SOURCE_SCHEMA_MISMATCH" or mismatch_result.get("allowed"):
            raise RuntimeError(f"Schema mismatch was not blocked: {mismatch_result!r}")
        if mismatch_before != mismatch_after:
            raise RuntimeError("Mismatched-schema database changed during packaged preflight")

        print(f"PACKAGED VERSION: {build_info['version']}")
        print(f"PACKAGED COMMIT: {build_info['commit']}")
        print(f"PACKAGED CURRENT_SCHEMA_VERSION: {build_info['schema_version']}")
        print(f"CURRENT DB SHA256 BEFORE/AFTER: {current_before} / {current_after}")
        print(f"NEWER DB SHA256 BEFORE/AFTER: {newer_before} / {newer_after}")
        print(f"MISMATCH DB SHA256 BEFORE/AFTER: {mismatch_before} / {mismatch_after}")
        print("PACKAGED APP CREATE/START/HEALTH/REOPEN: PASS")
        print("PACKAGED SQLITE PREFLIGHT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
