import os
import re
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.app_fixture import manticore
from config import MOODLE_PASSWORD_PLACEHOLDER


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        manticore.app.config["TESTING"] = True
        manticore.save_login_generation_settings(
            manticore.get_default_login_generation_rules(),
            setup_completed=True,
            updated_by="security-test",
        )

    @staticmethod
    def csrf_from_response(response):
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        if not match:
            raise AssertionError("CSRF token was not rendered")
        return match.group(1)

    def test_default_admin_requires_initial_password_change(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            row = conn.execute(
                "SELECT approved, must_change_password FROM users WHERE username='admin'"
            ).fetchone()
        self.assertIn("must_change_password", columns)
        self.assertEqual(row, (1, 1))

    def test_initial_password_change_is_forced_and_does_not_log_passwords(self):
        username = "forced-change-security-test"
        initial_password = "initial-secret-value"
        replacement = "replacement-secret-value"
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute("DELETE FROM users WHERE username=?", (username,))
            conn.execute(
                """INSERT INTO users
                       (username, password, role, approved, must_change_password)
                   VALUES (?, ?, 'admin', 1, 1)""",
                (username, manticore.hash_user_password(initial_password)),
            )

        try:
            client = manticore.app.test_client()
            token = self.csrf_from_response(client.get("/login"))
            response = client.post(
                "/login",
                data={"username": username, "password": initial_password, "csrf_token": token},
            )
            self.assertTrue(response.headers["Location"].endswith("/change-password"))
            self.assertTrue(client.get("/").headers["Location"].endswith("/change-password"))

            token = self.csrf_from_response(client.get("/change-password"))
            response = client.post(
                "/change-password",
                data={
                    "current_password": initial_password,
                    "new_password": replacement,
                    "confirm_password": replacement,
                    "csrf_token": token,
                },
            )
            self.assertEqual(response.status_code, 303)
            with sqlite3.connect(manticore.DB_PATH) as conn:
                stored, must_change = conn.execute(
                    "SELECT password, must_change_password FROM users WHERE username=?",
                    (username,),
                ).fetchone()
                details = "\n".join(
                    row[0] or ""
                    for row in conn.execute(
                        "SELECT details FROM audit_logs WHERE entity_type='user' AND entity_id=?",
                        (username,),
                    )
                )
            self.assertTrue(manticore.verify_user_password(stored, replacement))
            self.assertEqual(must_change, 0)
            self.assertNotIn(initial_password, details)
            self.assertNotIn(replacement, details)
        finally:
            with sqlite3.connect(manticore.DB_PATH) as conn:
                conn.execute("DELETE FROM users WHERE username=?", (username,))
                conn.execute(
                    "DELETE FROM audit_logs WHERE entity_type='user' AND entity_id=?",
                    (username,),
                )

    def test_student_import_and_duplicate_discard_uploaded_passwords(self):
        username = "security-student-password"
        upload_path = Path(manticore.app.config["UPLOAD_FOLDER"]) / "security-students.xlsx"
        pd.DataFrame(
            [
                {
                    "username": username,
                    "password": "real-secret-must-not-survive",
                    "email": "security-student@example.com",
                    "firstname": "Security",
                    "lastname": "Student",
                    "cohort1": "26ФМ-9-1",
                },
                {
                    "username": username,
                    "password": "another-real-secret",
                    "email": "security-duplicate@example.com",
                    "firstname": "Security",
                    "lastname": "Duplicate",
                    "cohort1": "26ФМ-9-1",
                },
            ]
        ).to_excel(upload_path, index=False)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute("DELETE FROM students WHERE username=?", (username,))
            conn.execute("DELETE FROM students_duplicates WHERE username=?", (username,))
        try:
            result = manticore.apply_students_import(str(upload_path))
            self.assertEqual(result["inserted_count"], 1)
            self.assertEqual(result["duplicate_count"], 1)
            with sqlite3.connect(manticore.DB_PATH) as conn:
                values = [
                    row[0]
                    for table in ("students", "students_duplicates")
                    for row in conn.execute(f"SELECT password FROM {table} WHERE username=?", (username,))
                ]
            self.assertEqual(values, [MOODLE_PASSWORD_PLACEHOLDER, MOODLE_PASSWORD_PLACEHOLDER])
        finally:
            upload_path.unlink(missing_ok=True)
            with sqlite3.connect(manticore.DB_PATH) as conn:
                conn.execute("DELETE FROM students WHERE username=?", (username,))
                conn.execute("DELETE FROM students_duplicates WHERE username=?", (username,))

    def test_existing_and_edited_student_passwords_are_normalized(self):
        username = "security-edit-student"
        with sqlite3.connect(":memory:") as legacy:
            legacy.execute("CREATE TABLE students (password TEXT)")
            legacy.execute("CREATE TABLE students_duplicates (password TEXT)")
            legacy.execute("INSERT INTO students VALUES ('legacy-real-secret')")
            legacy.execute("INSERT INTO students_duplicates VALUES ('duplicate-real-secret')")
            manticore.normalize_student_password_placeholders(legacy)
            self.assertEqual(
                legacy.execute("SELECT password FROM students").fetchone()[0],
                MOODLE_PASSWORD_PLACEHOLDER,
            )
            self.assertEqual(
                legacy.execute("SELECT password FROM students_duplicates").fetchone()[0],
                MOODLE_PASSWORD_PLACEHOLDER,
            )
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute("DELETE FROM students WHERE username=?", (username,))
            conn.execute(
                """INSERT INTO students
                       (username, password, email, firstname, lastname, cohort1)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (username, MOODLE_PASSWORD_PLACEHOLDER, "security-edit@example.com", "Edit", "Student", "26ФМ-9-1"),
            )
        client = manticore.app.test_client()
        with client.session_transaction() as session:
            session["user"] = "admin"
            session["role"] = "admin"
            session["must_change_password"] = False
        try:
            token = self.csrf_from_response(client.get(f"/edit_student/{username}"))
            client.post(
                f"/edit_student/{username}",
                data={
                    "password": "submitted-real-secret",
                    "email": "security-edit@example.com",
                    "firstname": "Edit",
                    "lastname": "Student",
                    "csrf_token": token,
                },
            )
            with sqlite3.connect(manticore.DB_PATH) as conn:
                stored = conn.execute(
                    "SELECT password FROM students WHERE username=?", (username,)
                ).fetchone()[0]
            self.assertEqual(stored, MOODLE_PASSWORD_PLACEHOLDER)
        finally:
            with sqlite3.connect(manticore.DB_PATH) as conn:
                conn.execute("DELETE FROM students WHERE username=?", (username,))

    def test_database_guard_rejects_real_student_password_before_write(self):
        username = "security-rejected-password"
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute("DELETE FROM students WHERE username=?", (username,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO students (username, password) VALUES (?, ?)",
                    (username, "must-never-reach-storage"),
                )
            self.assertIsNone(
                conn.execute("SELECT password FROM students WHERE username=?", (username,)).fetchone()
            )

    def test_admin_update_command_ignores_arbitrary_command_environment(self):
        with mock.patch.dict(os.environ, {"APP_UPDATE_COMMAND": "malicious --argument"}), \
             mock.patch.object(manticore.os, "name", "posix"), \
             mock.patch.object(manticore.shutil, "which", side_effect=["/usr/bin/sudo", "/usr/bin/systemctl"]):
            command = manticore.build_app_update_command()
        self.assertEqual(
            command,
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", "manticore-update.service"],
        )

    def test_network_binding_emits_http_exposure_warning(self):
        self.assertFalse(manticore.warn_if_insecure_network_binding("127.0.0.1"))
        with self.assertLogs("app", level="WARNING") as captured:
            self.assertTrue(manticore.warn_if_insecure_network_binding("0.0.0.0"))
        self.assertIn("HTTPS reverse proxy", "\n".join(captured.output))

    def test_installers_never_print_initial_admin_password(self):
        powershell = (PROJECT_ROOT / "scripts" / "install_and_run_windows.ps1").read_text(encoding="utf-8")
        deploy = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("-AsSecureString", powershell)
        self.assertNotIn("Admin password:", powershell)
        self.assertNotIn("Admin password:", deploy)
        self.assertNotIn('show_admin_password_notice "$ADMIN_PASSWORD"', deploy)

    def test_ci_audits_server_and_desktop_dependency_sets(self):
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
        self.assertIn("pip-audit -r requirements-prod.lock --no-deps --disable-pip", workflow)
        self.assertIn("pip-audit -r requirements-desktop.lock --no-deps --disable-pip", workflow)
        self.assertFalse((PROJECT_ROOT / ".github" / "workflows" / "pip-audit.yml").exists())

    def test_audited_lock_files_pin_every_requirement(self):
        # pip-audit --no-deps --disable-pip is only sound for fully pinned files.
        for name in ("requirements-prod.lock", "requirements-desktop.lock"):
            for line in (PROJECT_ROOT / name).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    self.assertRegex(line, r"^[A-Za-z0-9._-]+==[^\s;]+$", f"{name}: {line}")


if __name__ == "__main__":
    unittest.main()
