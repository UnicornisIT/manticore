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
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

import desktop_releases


APP_NAME = "Manticore"
CONFIG_FILENAME = "desktop-config.json"
UPDATE_ENDPOINT = "/api/desktop/releases/windows"
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
MAX_INSTALLER_SIZE = 256 * 1024 * 1024
TRUST_POLICY_PATH = Path("desktop") / "trusted_update.json"
WINDOW_ICON_PATH = Path("desktop") / "manticore.ico"
UNINSTALL_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{815471B3-D4A7-49C8-9F25-BEACF00E37B8}_is1"
WINTRUST_SUCCESS = 0x00000000
WINTRUST_UNTRUSTED_ROOT = 0x800B0109
_INSTANCE_MUTEX = None


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
    if not re.fullmatch(r"[0-9a-f]{64}", signer_sha256) or signer_sha256 == "0" * 64:
        raise ValueError("В клиенте не настроен сертификат издателя обновлений.")
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

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "ManticoreDesktopClient")
    if not handle:
        return False
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _INSTANCE_MUTEX = handle
    return True


def load_config() -> dict:
    try:
        payload = json.loads(config_path().read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_config(config: dict) -> None:
    path = config_path()
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary_path, path)


def is_loopback_host(hostname: str | None) -> bool:
    return (hostname or "").casefold() in {"127.0.0.1", "localhost", "::1"}


def normalize_server_url(value: str, *, optional: bool = False) -> str:
    value = str(value or "").strip().rstrip("/")
    if optional and not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Укажите полный адрес сервера, например https://manticore.example.ru.")
    if parsed.scheme != "https" and not is_loopback_host(parsed.hostname):
        raise ValueError("Для удалённого сервера требуется HTTPS.")
    if parsed.query or parsed.fragment:
        raise ValueError("Адрес сервера не должен содержать параметры или якорь.")
    return value


def normalize_database_path(value: str) -> str:
    path = Path(str(value or "").strip().strip('"')).expanduser()
    if path.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("Выберите файл базы с расширением .db, .sqlite или .sqlite3.")
    path = path.resolve()
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
        return {
            "page": self.page,
            "version": current_version(),
            "mode": self.existing.get("mode") if self.existing.get("mode") in {"remote", "local"} else "remote",
            "server_url": str(self.existing.get("server_url") or ""),
            "database_path": str(self.existing.get("database_path") or default_database_path()),
            "update_server_url": str(self.existing.get("update_server_url") or ""),
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
                }
            elif mode == "local":
                configured = {
                    "mode": "local",
                    "server_url": "",
                    "database_path": normalize_database_path(payload.get("database_path", "")),
                    "update_server_url": normalize_server_url(payload.get("update_server_url", ""), optional=True),
                }
            else:
                raise ValueError("Выберите режим работы.")
            configured["local_secret_key"] = self.existing.get("local_secret_key") or secrets.token_urlsafe(48)
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
        with sqlite3.connect(str(path)) as connection:
            row = connection.execute("SELECT 1 FROM users WHERE username='admin' LIMIT 1").fetchone()
        return bool(row)
    except sqlite3.Error:
        return False


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


def version_key(value: str):
    value = str(value or "").strip().lstrip("vV")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?", value)
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), 1 if suffix is None else 0, suffix or ""


def fetch_update_manifest(server_url: str, *, allow_same_version_rebuild: bool = False) -> dict:
    trust_policy = load_trust_policy()
    endpoint = server_url.rstrip("/") + UPDATE_ENDPOINT
    request = urllib.request.Request(
        endpoint,
        headers={"Accept": "application/json", "User-Agent": f"Manticore-Desktop/{current_version()}"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        raw_payload = response.read(1024 * 1024 + 1)
    if len(raw_payload) > 1024 * 1024:
        raise ValueError("Ответ сервера обновлений слишком большой.")
    payload = json.loads(raw_payload.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Сервер вернул некорректную политику обновлений.")
    if payload.get("repository") != trust_policy["github_repository"]:
        raise ValueError("Сервер обновлений указал недоверенный GitHub-репозиторий.")
    if not payload.get("approved") or not isinstance(payload.get("approval"), dict):
        return {}
    approval = payload["approval"]
    if approval.get("repository") != trust_policy["github_repository"]:
        raise ValueError("Разрешение обновления относится к другому репозиторию.")

    release = desktop_releases.fetch_release_by_tag(
        trust_policy["github_repository"],
        str(approval.get("tag_name") or ""),
        timeout=8,
    )
    exact_fields = ("release_id", "asset_id", "asset_name", "version", "size")
    if any(approval.get(field) != release.get(field) for field in exact_fields):
        raise ValueError("Данные разрешённого релиза не совпадают с Immutable Release в GitHub.")
    if not hmac.compare_digest(str(approval.get("sha256") or ""), release["sha256"]):
        raise ValueError("SHA-256 разрешённого установщика не совпадает с GitHub.")

    latest_key = version_key(release["version"])
    installed_key = version_key(current_version())
    if latest_key is not None and installed_key is not None and latest_key <= installed_key:
        same_version_rebuild = bool(release.get("is_rebuild")) and latest_key == installed_key
        if not (allow_same_version_rebuild and same_version_rebuild):
            return {}
    return {
        "version": release["version"],
        "tag_name": release["tag_name"],
        "is_rebuild": bool(release.get("is_rebuild")),
        "sha256": release["sha256"],
        "size": release["size"],
        "notes": release["notes"],
        "download_url": release["download_url"],
        "signer_certificate_sha256": trust_policy["signer_certificate_sha256"],
    }


def download_installer(manifest: dict, progress=None) -> Path:
    update_directory = application_data_directory() / "updates"
    update_directory.mkdir(parents=True, exist_ok=True)
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
                with winreg.OpenKey(hive, UNINSTALL_REGISTRY_KEY, 0, winreg.KEY_READ | view):
                    return switch
            except OSError:
                continue
    return "/CURRENTUSER"


def launch_installer_after_exit(installer_path: Path) -> None:
    """Wait for this process to exit, then replace the existing installation safely."""
    path_literal = "'" + str(installer_path).replace("'", "''") + "'"
    scope_switch = installed_scope_switch()
    arguments = (
        "@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"
        f"'{scope_switch}','/CLOSEAPPLICATIONS','/RESTARTAPPLICATIONS')"
    )
    command = (
        f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
        f"Start-Process -FilePath {path_literal} -ArgumentList {arguments}"
    )
    encoded_command = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [powershell_executable(), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_command],
        close_fds=True,
        creationflags=creation_flags,
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


def offer_and_install_update(
    server_url: str,
    *,
    ask_confirmation: bool = True,
    show_check_errors: bool = False,
    allow_same_version_rebuild: bool = False,
) -> bool:
    if not server_url:
        if show_check_errors:
            show_native_message("Не удалось проверить обновление", "Сервер обновлений не настроен.", error=True)
        return False
    try:
        manifest = fetch_update_manifest(
            server_url,
            allow_same_version_rebuild=allow_same_version_rebuild,
        )
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        logging.warning("Update check failed: %s", exc)
        if show_check_errors:
            show_native_message("Не удалось проверить обновление", str(exc), error=True)
        return False
    if not manifest:
        if show_check_errors:
            show_native_message(
                "Обновление Manticore",
                "Разрешённая новая версия не найдена. На этом компьютере уже установлена последняя разрешённая версия либо администратор ещё не разрешил новый релиз.",
            )
        return False

    if manifest.get("is_rebuild") and manifest["version"] == current_version():
        details = f"Доступна исправленная сборка версии {manifest['version']}."
    else:
        details = f"Доступна версия {manifest['version']} (установлена {current_version()})."
    if manifest["notes"]:
        details += f"\n\n{manifest['notes']}"
    details += "\n\nСкачать и установить обновление сейчас?"
    if ask_confirmation and not confirm_native_message("Обновление Manticore", details):
        return False

    try:
        installer_path = download_installer(manifest)
        verify_authenticode_signature(installer_path, manifest["signer_certificate_sha256"])
        launch_installer_after_exit(installer_path)
        return True
    except (OSError, ValueError, urllib.error.URLError) as exc:
        logging.exception("Update installation failed")
        if 'installer_path' in locals():
            try:
                installer_path.unlink()
            except OSError:
                pass
        if show_check_errors:
            show_native_message("Не удалось обновить Manticore", str(exc), error=True)
        return False


class DesktopApi:
    """Operations that are safe to expose to pages opened in the desktop shell."""

    def __init__(self, update_server_url: str, target_url: str = ""):
        self.update_server_url = update_server_url
        self.target_url = target_url
        self.connection_error = ""

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
        config = load_config()
        return {
            "desktop": True,
            "version": current_version(),
            "mode": config.get("mode", ""),
            "server_url": config.get("server_url", ""),
            "database_path": config.get("database_path", ""),
            "update_server_url": config.get("update_server_url", ""),
            "log_path": str(application_data_directory() / "logs" / "client.log"),
        }

    def check_for_update(self) -> dict:
        try:
            manifest = fetch_update_manifest(self.update_server_url, allow_same_version_rebuild=True)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            logging.warning("Manual update check failed: %s", exc)
            return {"ok": False, "error": str(exc), "current_version": current_version()}
        if not manifest:
            return {"ok": True, "available": False, "current_version": current_version()}
        return {
            "ok": True,
            "available": True,
            "current_version": current_version(),
            "version": manifest["version"],
            "notes": manifest.get("notes", ""),
            "is_rebuild": bool(manifest.get("is_rebuild")),
        }

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

    def install_approved_update(self) -> dict:
        started = offer_and_install_update(
            self.update_server_url,
            ask_confirmation=False,
            show_check_errors=True,
            allow_same_version_rebuild=True,
        )
        if started:
            timer = threading.Timer(0.25, self._close_windows)
            timer.daemon = True
            timer.start()
        return {"started": started, "current_version": current_version()}


def check_server_connection(url: str, timeout: float = 5.0) -> str:
    parsed = urllib.parse.urlsplit(url)
    if is_loopback_host(parsed.hostname):
        return ""
    request = urllib.request.Request(url, headers={"User-Agent": f"Manticore-Desktop/{current_version()}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return ""
    except urllib.error.HTTPError:
        return ""  # An HTTP response proves the server and TLS connection are reachable.
    except urllib.error.URLError as exc:
        logging.warning("Remote server connection failed for %s: %s", url, exc)
        reason = str(getattr(exc, "reason", exc))
        return f"Сервер не ответил. Проверьте подключение к сети, адрес сервера и сертификат TLS. ({reason})"
    except (OSError, TimeoutError) as exc:
        logging.warning("Remote server connection failed for %s: %s", url, exc)
        return "Сервер не ответил вовремя. Проверьте сеть и повторите попытку."


class LocalServer:
    def __init__(self, database_path: str, admin_password: str | None, secret_key: str):
        database = Path(database_path).resolve()
        os.environ["UPLOAD_FOLDER"] = str(database.parent)
        os.environ["DB_FILENAME"] = database.name
        os.environ["SECRET_KEY"] = secret_key
        os.environ["APP_HOST"] = "127.0.0.1"
        os.environ["APP_DEBUG"] = "false"
        os.environ["SESSION_COOKIE_SECURE"] = "false"
        os.environ["APP_UPDATE_ENABLED"] = "false"
        if admin_password:
            os.environ["ADMIN_DEFAULT_PASSWORD"] = admin_password

        import app as manticore_app
        from werkzeug.serving import make_server

        os.environ.pop("ADMIN_DEFAULT_PASSWORD", None)
        self._server = make_server("127.0.0.1", 0, manticore_app.app, threaded=True)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, name="manticore-local-server", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)


def open_desktop_window(url: str, update_server_url: str | None = None) -> None:
    import webview

    storage_path = application_data_directory() / "webview"
    storage_path.mkdir(parents=True, exist_ok=True)
    api = DesktopApi(update_server_url or url, url)
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

    def finish_startup() -> None:
        connection_error = check_server_connection(url)
        if connection_error:
            api.connection_error = connection_error
            window.load_url(str(error_page))
            return
        window.load_url(url)

    webview.start(
        finish_startup,
        private_mode=False,
        storage_path=str(storage_path),
        icon=str(bundle_root() / WINDOW_ICON_PATH),
    )


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manticore Windows desktop client")
    parser.add_argument("--configure", action="store_true", help="show connection settings")
    parser.add_argument("--skip-update", action="store_true", help="skip the update check for this launch")
    parser.add_argument("--configuration-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--admin-password-child", metavar="RESULT_PATH", help=argparse.SUPPRESS)
    return parser.parse_args()


def run_configured_client(config: dict, args: argparse.Namespace) -> int:
    if config["mode"] == "remote":
        try:
            target_url = normalize_server_url(config.get("server_url", ""))
        except ValueError:
            configured = show_configuration_dialog(config)
            if configured is None:
                return 1
            configured["local_secret_key"] = config.get("local_secret_key") or secrets.token_urlsafe(48)
            config = configured
            save_config(config)
            return run_configured_client(config, args)
        if not args.skip_update and offer_and_install_update(target_url):
            return 0
        open_desktop_window(target_url, target_url)
        return 0

    database_path = normalize_database_path(config.get("database_path") or default_database_path())
    admin_password = None
    if not database_has_admin(database_path):
        admin_password = prompt_initial_admin_password()
        if not admin_password:
            return 1
    secret_key = config.get("local_secret_key") or secrets.token_urlsafe(48)
    config.update({"database_path": database_path, "local_secret_key": secret_key})
    save_config(config)

    local_server = LocalServer(database_path, admin_password, secret_key)
    local_server.start()
    try:
        update_server = config.get("update_server_url") or local_server.url
        if not args.skip_update and offer_and_install_update(update_server):
            return 0
        open_desktop_window(local_server.url, update_server)
    finally:
        local_server.stop()
    return 0


def main() -> int:
    configure_logging()
    cleanup_old_installers()
    args = parse_arguments()
    if args.configuration_child:
        return run_setup_child("configuration")
    if args.admin_password_child:
        return run_setup_child("admin-password", args.admin_password_child)
    if not acquire_single_instance():
        show_native_message("Manticore", "Приложение уже запущено.")
        return 0
    config = load_config()
    if args.configure or config.get("mode") not in {"remote", "local"}:
        configured = show_configuration_dialog(config)
        if configured is None:
            return 0
        configured["local_secret_key"] = config.get("local_secret_key") or secrets.token_urlsafe(48)
        config = configured
        save_config(config)
    return run_configured_client(config, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception("Desktop client stopped unexpectedly")
        show_native_message(
            "Manticore",
            f"Не удалось запустить приложение.\n\n{exc}\n\nПодробности записаны в журнал клиента.",
            error=True,
        )
        raise SystemExit(1)
