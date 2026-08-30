#!/usr/bin/env python3
"""Safe cross-platform updater for the manticore app."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import venv
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_UPLOAD_FOLDER = "uploads"
DEFAULT_DB_FILENAME = "baze.db"
DB_BACKUP_PREFIX = "baze_backup_"
DEFAULT_UPDATE_STATUS_FILENAME = "app_update_status.json"
GITHUB_API_ROOT = "https://api.github.com"
RELEASE_TAG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}")


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


def git_command(app_dir: Path, *args: str) -> list[str]:
    return ["git", "-c", f"safe.directory={app_dir}", *args]


def normalize_version(value: str) -> str:
    return str(value or "").strip().lstrip("vV")


def semantic_version_key(value: str):
    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?",
        normalize_version(value),
    )
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    return (int(major), int(minor), int(patch), 1 if suffix is None else 0, suffix or "")


def is_release_newer(candidate: str, current: str) -> bool:
    candidate_key = semantic_version_key(candidate)
    current_key = semantic_version_key(current)
    if candidate_key is not None and current_key is not None:
        return candidate_key > current_key
    return normalize_version(candidate) != normalize_version(current)


def github_repository_from_remote(remote_url: str) -> str | None:
    value = str(remote_url or "").strip()
    patterns = (
        r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
        r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
        r"ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def get_release_api_url(app_dir: Path, configured_url: str | None = None) -> str:
    if configured_url:
        return configured_url
    if not (app_dir / ".git").exists() or not shutil.which("git"):
        raise UpdateError("Git repository was not found, so the release source cannot be determined.")
    remote = run_command_capture(git_command(app_dir, "remote", "get-url", "origin"), app_dir)
    if remote.returncode != 0:
        raise UpdateError("The origin Git remote was not found.")
    repository = github_repository_from_remote(remote.stdout)
    if not repository:
        raise UpdateError("The origin remote is not a supported GitHub repository.")
    return f"{GITHUB_API_ROOT}/repos/{repository}/releases/latest"


def fetch_latest_release(
    app_dir: Path,
    api_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, str]:
    release_url = get_release_api_url(app_dir, api_url or os.environ.get("APP_UPDATE_RELEASE_API_URL"))
    request = urllib.request.Request(
        release_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "manticore-self-updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Could not check the latest GitHub release: {exc}") from exc

    tag_name = str(payload.get("tag_name") or "").strip()
    if not RELEASE_TAG_PATTERN.fullmatch(tag_name):
        raise UpdateError("The latest GitHub release returned an invalid tag name.")
    return {
        "tag_name": tag_name,
        "name": str(payload.get("name") or tag_name).strip(),
        "html_url": str(payload.get("html_url") or "").strip(),
        "published_at": str(payload.get("published_at") or "").strip(),
    }


def get_installed_version(app_dir: Path) -> str:
    version_file = app_dir / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8-sig").strip()
        if value:
            return normalize_version(value)
    env_version = load_env_file(app_dir).get("APP_VERSION")
    if env_version:
        return normalize_version(env_version)
    return "unknown"


def resolve_update_status_path(app_dir: Path, configured_path: str | None = None) -> Path:
    if configured_path:
        status_path = Path(os.path.expandvars(os.path.expanduser(configured_path)))
        if not status_path.is_absolute():
            status_path = app_dir / status_path
        return status_path.resolve()
    upload_folder = resolve_upload_folder(app_dir, load_env_file(app_dir))
    return upload_folder / DEFAULT_UPDATE_STATUS_FILENAME


def write_update_status(status_path: Path, state: str, message: str, **details) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": state,
        "message": str(message),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update({key: value for key, value in details.items() if value not in (None, "")})
    temporary_path = status_path.with_name(f".{status_path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, status_path)
    if os.name != "nt":
        try:
            os.chmod(status_path, 0o644)
        except OSError:
            pass


def read_update_status(status_path: Path) -> dict:
    if not status_path.exists():
        return {}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def update_git_repository(app_dir: Path, target_ref: str | None = None) -> bool:
    if not (app_dir / ".git").exists():
        raise UpdateError(
            "Program files were not updated because this folder is not a Git repository. "
            f"Expected .git in: {app_dir}. "
            "This usually happens when the app was downloaded as a ZIP archive or copied manually. "
            "Install/update from a Git clone, or replace program files from a fresh release archive "
            "while keeping .env and uploads."
        )
    if not shutil.which("git"):
        raise UpdateError("Git was not found. Install Git or update the program files manually.")

    status = run_command_capture(git_command(app_dir, "status", "--short"), app_dir)
    if status.returncode == 0 and status.stdout.strip():
        print("Local file changes were detected. Git will keep them unless they conflict with the update.")
    if not target_ref:
        run_command(git_command(app_dir, "pull", "--ff-only"), app_dir)
        return True

    if not RELEASE_TAG_PATTERN.fullmatch(target_ref):
        raise UpdateError("The release tag contains unsupported characters.")
    target_full_ref = f"refs/tags/{target_ref}"
    run_command(
        git_command(app_dir, "fetch", "--force", "origin", f"{target_full_ref}:{target_full_ref}"),
        app_dir,
    )
    head = run_command_capture(git_command(app_dir, "rev-parse", "HEAD"), app_dir)
    target = run_command_capture(git_command(app_dir, "rev-parse", f"{target_full_ref}^{{commit}}"), app_dir)
    if head.returncode != 0 or target.returncode != 0:
        raise UpdateError(f"Could not resolve release tag {target_ref}.")
    head_commit = head.stdout.strip()
    target_commit = target.stdout.strip()
    if head_commit == target_commit:
        print(f"Release {target_ref} is already installed.")
        return False

    current_is_ancestor = run_command_capture(
        git_command(app_dir, "merge-base", "--is-ancestor", head_commit, target_commit), app_dir
    )
    if current_is_ancestor.returncode == 0:
        run_command(git_command(app_dir, "merge", "--ff-only", target_full_ref), app_dir)
        return True

    target_is_ancestor = run_command_capture(
        git_command(app_dir, "merge-base", "--is-ancestor", target_commit, head_commit), app_dir
    )
    if target_is_ancestor.returncode == 0:
        print(f"Current code already contains release {target_ref}; downgrade skipped.")
        return False
    raise UpdateError(
        f"Current code and release {target_ref} have diverged. Automatic update was stopped."
    )


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
    parser.add_argument("--latest-release", action="store_true")
    parser.add_argument("--release-api-url")
    parser.add_argument("--status-file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app_dir = Path(args.app_dir).resolve()
    if not app_dir.exists():
        print(f"ERROR: app directory was not found: {app_dir}", file=sys.stderr)
        return 1

    status_path = resolve_update_status_path(app_dir, args.status_file) if args.status_file else None
    current_version = get_installed_version(app_dir)
    release = None
    try:
        step("Preparing update")
        print(f"App directory: {app_dir}")
        if status_path:
            write_update_status(
                status_path,
                "running",
                "Проверяется последний опубликованный релиз.",
                current_version=current_version,
                started_at=datetime.now(timezone.utc).isoformat(),
            )

        if args.latest_release:
            release = fetch_latest_release(app_dir, api_url=args.release_api_url)
            print(f"Latest release: {release['tag_name']}")

        if not args.skip_backup:
            step("Backing up database")
            backup_database(app_dir)

        if not args.skip_git:
            step("Updating program files")
            code_changed = update_git_repository(app_dir, release["tag_name"] if release else None)
            if release and not code_changed:
                message = f"Текущий код уже включает релиз {release['tag_name']}."
                if status_path:
                    write_update_status(
                        status_path,
                        "up_to_date",
                        message,
                        current_version=get_installed_version(app_dir),
                        latest_version=normalize_version(release["tag_name"]),
                        release_url=release.get("html_url"),
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    )
                print(message)
                return 0

        if not args.skip_deps:
            step("Updating dependencies")
            install_dependencies(app_dir, args.requirements)

        if args.restart_systemd:
            step("Restarting service")
            restart_systemd_service(args.service_name, args.reload_nginx, app_dir)

        step("Update completed")
        print("Local .env, uploads, and database files were not overwritten.")
        if status_path:
            write_update_status(
                status_path,
                "completed",
                f"Релиз {release['tag_name']} успешно установлен." if release else "Обновление успешно завершено.",
                current_version=get_installed_version(app_dir),
                latest_version=normalize_version(release["tag_name"]) if release else None,
                release_url=release.get("html_url") if release else None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        return 0
    except Exception as exc:
        print()
        print(f"ERROR: {exc}", file=sys.stderr)
        if status_path:
            try:
                write_update_status(
                    status_path,
                    "failed",
                    str(exc),
                    current_version=get_installed_version(app_dir),
                    latest_version=normalize_version(release["tag_name"]) if release else None,
                    release_url=release.get("html_url") if release else None,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
