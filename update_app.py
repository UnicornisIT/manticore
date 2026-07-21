#!/usr/bin/env python3
"""Safe cross-platform updater for the manticore app."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import venv
from datetime import datetime
from pathlib import Path


DEFAULT_UPLOAD_FOLDER = "uploads"
DEFAULT_DB_FILENAME = "baze.db"
DB_BACKUP_PREFIX = "baze_backup_"


class UpdateError(RuntimeError):
    pass


def step(message: str) -> None:
    print()
    print(f"== {message} ==")


def load_env_file(app_dir: Path) -> dict[str, str]:
    env_path = app_dir / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def resolve_upload_folder(app_dir: Path, env_values: dict[str, str]) -> Path:
    raw_upload_folder = env_values.get("UPLOAD_FOLDER") or DEFAULT_UPLOAD_FOLDER
    upload_folder = Path(os.path.expandvars(os.path.expanduser(raw_upload_folder)))
    if not upload_folder.is_absolute():
        upload_folder = app_dir / upload_folder
    return upload_folder.resolve()


def resolve_database_path(app_dir: Path, env_values: dict[str, str]) -> Path:
    upload_folder = resolve_upload_folder(app_dir, env_values)
    db_filename = env_values.get("DB_FILENAME") or DEFAULT_DB_FILENAME
    return (upload_folder / db_filename).resolve()


def quick_check_sqlite(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        result = conn.execute("PRAGMA quick_check").fetchone()
    if not result or str(result[0]).lower() != "ok":
        raise UpdateError(f"Backup quick_check failed for {db_path}")


def backup_database(app_dir: Path) -> Path | None:
    env_values = load_env_file(app_dir)
    db_path = resolve_database_path(app_dir, env_values)
    upload_folder = db_path.parent

    if not db_path.exists():
        print(f"Database was not found, backup skipped: {db_path}")
        return None

    upload_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = upload_folder / f"{DB_BACKUP_PREFIX}before_update_{timestamp}.db"
    suffix = 2
    while backup_path.exists():
        backup_path = upload_folder / f"{DB_BACKUP_PREFIX}before_update_{timestamp}_{suffix}.db"
        suffix += 1

    source = None
    target = None
    try:
        source = sqlite3.connect(str(db_path))
        target = sqlite3.connect(str(backup_path))
        source.backup(target)
    except sqlite3.Error as exc:
        print(f"SQLite backup failed, falling back to file copy: {exc}")
        if source is not None:
            source.close()
            source = None
        if target is not None:
            target.close()
            target = None
        shutil.copy2(db_path, backup_path)
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()

    quick_check_sqlite(backup_path)
    print(f"Database backup created: {backup_path}")
    return backup_path


def run_command(args: list[str], cwd: Path, allow_failure: bool = False) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(part) for part in args))
    completed = subprocess.run(args, cwd=str(cwd))
    if completed.returncode != 0 and not allow_failure:
        raise UpdateError(f"Command failed with exit code {completed.returncode}: {' '.join(args)}")
    return completed


def run_command_capture(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=str(cwd), text=True, capture_output=True)


def update_git_repository(app_dir: Path) -> None:
    if not (app_dir / ".git").exists():
        print("Git repository was not found, code update skipped.")
        return
    if not shutil.which("git"):
        raise UpdateError("Git was not found. Install Git or update the program files manually.")

    status = run_command_capture(["git", "status", "--short"], app_dir)
    if status.returncode == 0 and status.stdout.strip():
        print("Local file changes were detected. Git will keep them unless they conflict with the update.")
    run_command(["git", "pull", "--ff-only"], app_dir)


def venv_python_path(app_dir: Path) -> Path:
    if os.name == "nt":
        return app_dir / ".venv" / "Scripts" / "python.exe"
    return app_dir / ".venv" / "bin" / "python"


def ensure_virtual_environment(app_dir: Path) -> Path:
    python_path = venv_python_path(app_dir)
    if python_path.exists():
        return python_path

    step("Creating virtual environment")
    venv.EnvBuilder(with_pip=True).create(str(app_dir / ".venv"))
    if not python_path.exists():
        raise UpdateError(f"Virtual environment was not created: {python_path}")
    return python_path


def install_dependencies(app_dir: Path, requirements_name: str) -> None:
    requirements_path = Path(requirements_name)
    if not requirements_path.is_absolute():
        requirements_path = app_dir / requirements_path
    if not requirements_path.exists():
        raise UpdateError(f"Requirements file was not found: {requirements_path}")

    python_path = ensure_virtual_environment(app_dir)
    run_command([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], app_dir)
    run_command([str(python_path), "-m", "pip", "install", "-r", str(requirements_path)], app_dir)


def restart_systemd_service(service_name: str, reload_nginx: bool, app_dir: Path) -> None:
    if not shutil.which("systemctl"):
        print("systemctl was not found, service restart skipped.")
        return

    run_command(["systemctl", "restart", service_name], app_dir)
    if reload_nginx and shutil.which("nginx"):
        nginx_check = run_command(["nginx", "-t"], app_dir, allow_failure=True)
        if nginx_check.returncode == 0:
            run_command(["systemctl", "reload", "nginx"], app_dir)
        else:
            print("Nginx config test failed, nginx reload skipped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely update manticore without touching local data.")
    parser.add_argument("app_dir", nargs="?", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--requirements", default="requirements.txt")
    parser.add_argument("--skip-backup", action="store_true")
    parser.add_argument("--skip-git", action="store_true")
    parser.add_argument("--skip-deps", action="store_true")
    parser.add_argument("--restart-systemd", action="store_true")
    parser.add_argument("--service-name", default="manticore")
    parser.add_argument("--reload-nginx", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app_dir = Path(args.app_dir).resolve()
    if not app_dir.exists():
        print(f"ERROR: app directory was not found: {app_dir}", file=sys.stderr)
        return 1

    try:
        step("Preparing update")
        print(f"App directory: {app_dir}")

        if not args.skip_backup:
            step("Backing up database")
            backup_database(app_dir)

        if not args.skip_git:
            step("Updating program files")
            update_git_repository(app_dir)

        if not args.skip_deps:
            step("Updating dependencies")
            install_dependencies(app_dir, args.requirements)

        if args.restart_systemd:
            step("Restarting service")
            restart_systemd_service(args.service_name, args.reload_nginx, app_dir)

        step("Update completed")
        print("Local .env, uploads, and database files were not overwritten.")
        return 0
    except Exception as exc:
        print()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
