"""GitHub-backed release approval for the Windows desktop client."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_GITHUB_REPOSITORY = "UnicornisIT/manticore"
APPROVAL_FILENAME = "desktop_release_approval.json"
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_INSTALLER_SIZE = 256 * 1024 * 1024


class DesktopReleaseError(ValueError):
    pass


def normalize_version(value: str) -> str:
    version = str(value or "").strip()
    if version[:1] in {"v", "V"}:
        version = version[1:]
    if not VERSION_PATTERN.fullmatch(version):
        raise DesktopReleaseError("GitHub Release должен иметь тег версии в формате v1.2.3.")
    return version


def installer_version_from_tag(value: str) -> str:
    """Return the installer version represented by a release tag.

    A rebuild tag identifies a new immutable build of the same application
    version, so ``v1.1.3-rebuild`` and ``v1.1.3-rebuild.2`` still contain
    ``Manticore-Setup-1.1.3.exe``.
    """
    tag_version = normalize_version(value)
    match = re.fullmatch(r"(\d+\.\d+\.\d+)-rebuild(?:\.\d+)?", tag_version, flags=re.IGNORECASE)
    return match.group(1) if match else tag_version


def is_rebuild_tag(value: str) -> bool:
    return installer_version_from_tag(value) != normalize_version(value)


def normalize_repository(value: str) -> str:
    repository = str(value or "").strip().strip("/")
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise DesktopReleaseError("Некорректное имя GitHub-репозитория.")
    return repository


def version_key(value: str):
    version = normalize_version(value)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?", version)
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), 1 if suffix is None else 0, suffix or ""


def is_newer(candidate: str, current: str) -> bool:
    try:
        return version_key(candidate) > version_key(current)
    except DesktopReleaseError:
        return normalize_version(candidate) != str(current or "").strip().lstrip("vV")


def approval_path(upload_folder: str | os.PathLike[str]) -> Path:
    return Path(upload_folder).resolve() / APPROVAL_FILENAME


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _validated_release_payload(payload: dict, repository: str) -> dict:
    if payload.get("draft") or payload.get("prerelease"):
        raise DesktopReleaseError("Черновик или prerelease нельзя публиковать для Windows-клиентов.")
    if payload.get("immutable") is not True:
        raise DesktopReleaseError(
            "GitHub Release не помечен как Immutable. Включите immutable releases в настройках репозитория."
        )
    tag_name = str(payload.get("tag_name") or "").strip()
    version = installer_version_from_tag(tag_name)
    expected_names = {
        f"Manticore-Setup-{version}.exe",
        f"Manticore-Setup-v{version}.exe",
    }
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise DesktopReleaseError("GitHub не вернул список файлов релиза.")
    matching_assets = [
        asset for asset in payload.get("assets", [])
        if isinstance(asset, dict) and str(asset.get("name") or "") in expected_names
    ]
    if len(matching_assets) != 1:
        raise DesktopReleaseError(
            f"В релизе должен быть ровно один установщик Manticore-Setup-{version}.exe."
        )
    asset = matching_assets[0]
    digest = str(asset.get("digest") or "").strip().lower()
    if not digest.startswith("sha256:") or not SHA256_PATTERN.fullmatch(digest[7:]):
        raise DesktopReleaseError("GitHub не вернул SHA-256 digest установщика.")
    try:
        size = int(asset.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise DesktopReleaseError("GitHub вернул некорректный размер установщика.") from exc
    if size <= 0 or size > MAX_INSTALLER_SIZE:
        raise DesktopReleaseError("Размер установщика в GitHub Release недопустим.")

    download_url = str(asset.get("browser_download_url") or "").strip()
    parsed_url = urllib.parse.urlsplit(download_url)
    expected_prefix = f"/{repository}/releases/download/".casefold()
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise DesktopReleaseError("Установщик должен скачиваться непосредственно с github.com по HTTPS.")
    if not urllib.parse.unquote(parsed_url.path).casefold().startswith(expected_prefix):
        raise DesktopReleaseError("Ссылка установщика не принадлежит настроенному GitHub-репозиторию.")

    try:
        release_id = int(payload.get("id") or 0)
        asset_id = int(asset.get("id") or 0)
    except (TypeError, ValueError) as exc:
        raise DesktopReleaseError("GitHub вернул некорректные идентификаторы релиза.") from exc
    if release_id <= 0 or asset_id <= 0:
        raise DesktopReleaseError("GitHub вернул некорректные идентификаторы релиза.")
    return {
        "repository": repository,
        "release_id": release_id,
        "tag_name": tag_name,
        "version": version,
        "is_rebuild": is_rebuild_tag(tag_name),
        "name": str(payload.get("name") or tag_name).strip()[:300],
        "notes": str(payload.get("body") or "")[:4000],
        "html_url": str(payload.get("html_url") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "immutable": True,
        "asset_id": asset_id,
        "asset_name": str(asset.get("name") or ""),
        "download_url": download_url,
        "sha256": digest[7:],
        "size": size,
    }


def _fetch_release_api(api_url: str, repository: str, timeout: float) -> dict:
    repository = normalize_repository(repository)
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "manticore-desktop-release-checker",
            "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_payload = response.read(2 * 1024 * 1024 + 1)
        if len(raw_payload) > 2 * 1024 * 1024:
            raise DesktopReleaseError("Ответ GitHub API слишком большой.")
        payload = json.loads(raw_payload.decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise DesktopReleaseError(f"Не удалось проверить последний GitHub Release: {exc}") from exc
    if not isinstance(payload, dict):
        raise DesktopReleaseError("GitHub API вернул некорректный ответ.")
    return _validated_release_payload(payload, repository)


def fetch_latest_release(repository: str = DEFAULT_GITHUB_REPOSITORY, timeout: float = 8.0) -> dict:
    repository = normalize_repository(repository)
    api_url = f"https://api.github.com/repos/{repository}/releases/latest"
    return _fetch_release_api(api_url, repository, timeout)


def fetch_release_by_tag(repository: str, tag_name: str, timeout: float = 8.0) -> dict:
    repository = normalize_repository(repository)
    tag_name = str(tag_name or "").strip()
    normalize_version(tag_name)
    encoded_tag = urllib.parse.quote(tag_name, safe="")
    api_url = f"https://api.github.com/repos/{repository}/releases/tags/{encoded_tag}"
    return _fetch_release_api(api_url, repository, timeout)


def approve_release(
    upload_folder: str | os.PathLike[str],
    release: dict,
    approved_by: str = "",
) -> dict:
    repository = normalize_repository(release.get("repository", ""))
    version = normalize_version(release.get("version", ""))
    tag_name = str(release.get("tag_name") or "").strip()
    if installer_version_from_tag(tag_name) != version:
        raise DesktopReleaseError("Тег GitHub Release не совпадает с версией установщика.")
    validated = {
        "repository": repository,
        "release_id": int(release["release_id"]),
        "tag_name": tag_name,
        "version": version,
        "is_rebuild": is_rebuild_tag(tag_name),
        "asset_id": int(release["asset_id"]),
        "asset_name": str(release["asset_name"]),
        "sha256": str(release["sha256"]).lower(),
        "size": int(release["size"]),
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": str(approved_by or "")[:200],
    }
    if not SHA256_PATTERN.fullmatch(validated["sha256"]):
        raise DesktopReleaseError("Некорректный SHA-256 установщика.")
    expected_names = {f"Manticore-Setup-{version}.exe", f"Manticore-Setup-v{version}.exe"}
    if validated["asset_name"] not in expected_names:
        raise DesktopReleaseError("Некорректное имя Windows-установщика.")
    if validated["release_id"] <= 0 or validated["asset_id"] <= 0:
        raise DesktopReleaseError("Некорректные идентификаторы GitHub Release.")
    if validated["size"] <= 0 or validated["size"] > MAX_INSTALLER_SIZE:
        raise DesktopReleaseError("Некорректный размер Windows-установщика.")
    _write_json_atomic(approval_path(upload_folder), validated)
    return validated


def load_approval(upload_folder: str | os.PathLike[str]) -> dict:
    try:
        payload = json.loads(approval_path(upload_folder).read_text(encoding="utf-8-sig"))
        repository = normalize_repository(payload.get("repository", ""))
        version = normalize_version(payload.get("version", ""))
        tag_name = str(payload.get("tag_name") or "").strip()
        release_id = int(payload.get("release_id") or 0)
        asset_id = int(payload.get("asset_id") or 0)
        asset_name = str(payload.get("asset_name") or "")
        size = int(payload.get("size") or 0)
        sha256 = str(payload.get("sha256") or "").lower()
        expected_names = {f"Manticore-Setup-{version}.exe", f"Manticore-Setup-v{version}.exe"}
        if (
            not SHA256_PATTERN.fullmatch(sha256)
            or installer_version_from_tag(tag_name) != version
            or release_id <= 0
            or asset_id <= 0
            or asset_name not in expected_names
            or size <= 0
            or size > MAX_INSTALLER_SIZE
        ):
            return {}
        return {
            "repository": repository,
            "release_id": release_id,
            "tag_name": tag_name,
            "version": version,
            "is_rebuild": is_rebuild_tag(tag_name),
            "asset_id": asset_id,
            "asset_name": asset_name,
            "sha256": sha256,
            "size": size,
            "approved_at": str(payload.get("approved_at") or ""),
            "approved_by": str(payload.get("approved_by") or "")[:200],
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError, DesktopReleaseError):
        return {}
