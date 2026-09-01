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


def show_configuration_dialog(existing: dict) -> dict | None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    result: dict | None = None
    root = tk.Tk()
    root.title("Настройка Manticore")
    root.resizable(False, False)
    root.columnconfigure(0, weight=1)

    frame = ttk.Frame(root, padding=20)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)

    ttk.Label(frame, text="Как использовать приложение?", font=("Segoe UI", 13, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
    )
    mode = tk.StringVar(value=existing.get("mode") if existing.get("mode") in {"remote", "local"} else "remote")
    ttk.Radiobutton(frame, text="Подключиться к существующему серверу", variable=mode, value="remote").grid(
        row=1, column=0, columnspan=3, sticky="w", pady=3
    )
    ttk.Radiobutton(frame, text="Работать с локальной базой на этом ПК", variable=mode, value="local").grid(
        row=2, column=0, columnspan=3, sticky="w", pady=3
    )

    ttk.Label(frame, text="Адрес сервера").grid(row=3, column=0, sticky="w", padx=(22, 10), pady=(14, 4))
    server_url = tk.StringVar(value=existing.get("server_url", ""))
    server_entry = ttk.Entry(frame, width=54, textvariable=server_url)
    server_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(14, 4))

    ttk.Label(frame, text="Локальная база").grid(row=4, column=0, sticky="w", padx=(22, 10), pady=4)
    database_path = tk.StringVar(value=existing.get("database_path") or default_database_path())
    database_entry = ttk.Entry(frame, width=46, textvariable=database_path)
    database_entry.grid(row=4, column=1, sticky="ew", pady=4)

    def browse_database() -> None:
        selected = filedialog.askopenfilename(
            parent=root,
            title="Выберите существующую базу Manticore",
            filetypes=(("SQLite", "*.db *.sqlite *.sqlite3"), ("Все файлы", "*.*")),
        )
        if selected:
            database_path.set(selected)

    ttk.Button(frame, text="Выбрать…", command=browse_database).grid(row=4, column=2, padx=(8, 0), pady=4)

    ttk.Label(frame, text="Сервер обновлений").grid(row=5, column=0, sticky="w", padx=(22, 10), pady=4)
    update_url = tk.StringVar(value=existing.get("update_server_url", ""))
    update_entry = ttk.Entry(frame, width=54, textvariable=update_url)
    update_entry.grid(row=5, column=1, columnspan=2, sticky="ew", pady=4)
    ttk.Label(
        frame,
        text="Для локального режима необязательно. Если оставить пустым, обновления берутся из локальной админки.",
        foreground="#555555",
        wraplength=520,
    ).grid(row=6, column=0, columnspan=3, sticky="w", padx=(22, 0), pady=(0, 14))

    def refresh_fields(*_args) -> None:
        remote = mode.get() == "remote"
        server_entry.configure(state="normal" if remote else "disabled")
        database_entry.configure(state="disabled" if remote else "normal")
        update_entry.configure(state="disabled" if remote else "normal")

    def submit() -> None:
        nonlocal result
        try:
            if mode.get() == "remote":
                normalized_server = normalize_server_url(server_url.get())
                result = {
                    "mode": "remote",
                    "server_url": normalized_server,
                    "update_server_url": normalized_server,
                    "database_path": database_path.get().strip(),
                }
            else:
                result = {
                    "mode": "local",
                    "server_url": "",
                    "database_path": normalize_database_path(database_path.get()),
                    "update_server_url": normalize_server_url(update_url.get(), optional=True),
                }
        except (OSError, ValueError) as exc:
            messagebox.showerror("Проверьте настройки", str(exc), parent=root)
            return
        root.destroy()

    mode.trace_add("write", refresh_fields)
    refresh_fields()
    buttons = ttk.Frame(frame)
    buttons.grid(row=7, column=0, columnspan=3, sticky="e", pady=(5, 0))
    ttk.Button(buttons, text="Отмена", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="Сохранить и открыть", command=submit).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.update_idletasks()
    root.geometry(f"+{max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)}+{max(0, (root.winfo_screenheight() - root.winfo_reqheight()) // 2)}")
    root.mainloop()
    return result


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
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "Локальная база",
        "В выбранной базе нет администратора. Создайте пароль для пользователя admin.",
        parent=root,
    )
    first = simpledialog.askstring("Пароль администратора", "Новый пароль (не менее 8 символов):", show="*", parent=root)
    if first is None:
        root.destroy()
        return None
    second = simpledialog.askstring("Пароль администратора", "Повторите пароль:", show="*", parent=root)
    if len(first) < 8:
        messagebox.showerror("Пароль не сохранён", "Пароль должен содержать не менее 8 символов.", parent=root)
        root.destroy()
        return None
    if first != second:
        messagebox.showerror("Пароль не сохранён", "Пароли не совпадают.", parent=root)
        root.destroy()
        return None
    root.destroy()
    return first


def version_key(value: str):
    value = str(value or "").strip().lstrip("vV")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?", value)
    if not match:
        return None
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), 1 if suffix is None else 0, suffix or ""


def fetch_update_manifest(server_url: str) -> dict:
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
        return {}
    return {
        "version": release["version"],
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


def launch_installer_after_exit(installer_path: Path) -> None:
    """Wait for this process to exit so Inno Setup can replace the executable."""
    path_literal = "'" + str(installer_path).replace("'", "''") + "'"
    arguments = "@('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER','/CLOSEAPPLICATIONS')"
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


def verify_authenticode_signature(installer_path: Path, expected_signer_sha256: str) -> None:
    path_literal = "'" + str(installer_path).replace("'", "''") + "'"
    command = (
        f"$signature = Get-AuthenticodeSignature -LiteralPath {path_literal}; "
        "if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) { exit 2 }; "
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
        raise ValueError("Цифровая подпись Windows-установщика недействительна или не пользуется доверием.")
    if not hmac.compare_digest(actual_hash, expected_signer_sha256.lower()):
        raise ValueError("Установщик подписан не тем сертификатом издателя.")


def offer_and_install_update(
    server_url: str,
    *,
    ask_confirmation: bool = True,
    show_check_errors: bool = False,
) -> bool:
    if not server_url:
        if show_check_errors:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Не удалось проверить обновление",
                "Сервер обновлений не настроен.",
                parent=root,
            )
            root.destroy()
        return False
    try:
        manifest = fetch_update_manifest(server_url)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        logging.warning("Update check failed: %s", exc)
        if show_check_errors:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Не удалось проверить обновление", str(exc), parent=root)
            root.destroy()
        return False
    if not manifest:
        if show_check_errors:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                "Обновление Manticore",
                "Разрешённая новая версия не найдена. На этом компьютере уже установлена последняя разрешённая версия либо администратор ещё не разрешил новый релиз.",
                parent=root,
            )
            root.destroy()
        return False

    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.withdraw()
    details = f"Доступна версия {manifest['version']} (установлена {current_version()})."
    if manifest["notes"]:
        details += f"\n\n{manifest['notes']}"
    details += "\n\nСкачать и установить обновление сейчас?"
    if ask_confirmation and not messagebox.askyesno("Обновление Manticore", details, parent=root):
        root.destroy()
        return False

    progress_window = tk.Toplevel(root)
    progress_window.title("Обновление Manticore")
    progress_window.resizable(False, False)
    ttk.Label(progress_window, text=f"Загрузка версии {manifest['version']}…").pack(padx=24, pady=(20, 10))
    progress_bar = ttk.Progressbar(progress_window, length=360, maximum=manifest["size"], mode="determinate")
    progress_bar.pack(padx=24, pady=(0, 20))
    progress_window.protocol("WM_DELETE_WINDOW", lambda: None)

    def update_progress(downloaded: int, _total: int) -> None:
        progress_bar["value"] = downloaded
        progress_window.update_idletasks()
        progress_window.update()

    try:
        installer_path = download_installer(manifest, update_progress)
        verify_authenticode_signature(installer_path, manifest["signer_certificate_sha256"])
        progress_window.destroy()
        launch_installer_after_exit(installer_path)
        root.destroy()
        return True
    except (OSError, ValueError, urllib.error.URLError) as exc:
        logging.exception("Update installation failed")
        if 'installer_path' in locals():
            try:
                installer_path.unlink()
            except OSError:
                pass
        progress_window.destroy()
        messagebox.showerror("Не удалось обновить Manticore", str(exc), parent=root)
        root.destroy()
        return False


class DesktopApi:
    """Operations that are safe to expose to pages opened in the desktop shell."""

    def __init__(self, update_server_url: str):
        self.update_server_url = update_server_url

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

    def install_approved_update(self) -> dict:
        started = offer_and_install_update(
            self.update_server_url,
            ask_confirmation=False,
            show_check_errors=True,
        )
        if started:
            timer = threading.Timer(0.25, self._close_windows)
            timer.daemon = True
            timer.start()
        return {"started": started, "current_version": current_version()}


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
    webview.create_window(
        f"Manticore {current_version()}",
        url,
        width=1360,
        height=860,
        min_size=(960, 640),
        text_select=True,
        js_api=DesktopApi(update_server_url or url),
    )
    webview.start(private_mode=False, storage_path=str(storage_path))


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manticore Windows desktop client")
    parser.add_argument("--configure", action="store_true", help="show connection settings")
    parser.add_argument("--skip-update", action="store_true", help="skip the update check for this launch")
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
    if not acquire_single_instance():
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Manticore", "Приложение уже запущено.", parent=root)
        root.destroy()
        return 0
    args = parse_arguments()
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
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(
                "Manticore",
                f"Не удалось запустить приложение:\n{exc}\n\nПодробности записаны в журнал клиента.",
                parent=root,
            )
            root.destroy()
        finally:
            raise SystemExit(1)
