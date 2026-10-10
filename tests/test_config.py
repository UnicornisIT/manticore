import unittest

from config import load_runtime_config, setting_bool


class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_config_parses_and_clamps_environment(self):
        config = load_runtime_config(
            "owner/default",
            {
                "SECRET_KEY": "fixed-test-key",
                "UPLOAD_FOLDER": "private-data",
                "DB_FILENAME": "records.db",
                "MAX_UPLOAD_SIZE_MB": "2",
                "MAX_CONTENT_LENGTH": "10",
                "SESSION_COOKIE_SECURE": "yes",
                "APP_UPDATE_STALE_SECONDS": "5",
                "DESKTOP_GITHUB_REPOSITORY": " owner/custom ",
                "HSTS_MAX_AGE": "-1",
                "BACKUP_RETENTION_DAYS": "0",
                "MANTICORE_FALLBACK_SESSION_ID": " session ",
            },
        )

        self.assertEqual(config.secret_key, "fixed-test-key")
        self.assertEqual(config.upload_folder, "private-data")
        self.assertEqual(config.db_filename, "records.db")
        self.assertEqual(config.max_content_length, 1024)
        self.assertTrue(config.session_cookie_secure)
        self.assertEqual(config.app_update_stale_seconds, 60)
        self.assertEqual(config.desktop_github_repository, "owner/custom")
        self.assertEqual(config.hsts_max_age, 0)
        self.assertEqual(config.backup_retention_days, 1)
        self.assertEqual(config.fallback_session_id, "session")

    def test_setting_bool_keeps_legacy_database_value_semantics(self):
        self.assertTrue(setting_bool("ON"))
        self.assertFalse(setting_bool("off", True))
        self.assertTrue(setting_bool(1))
        self.assertFalse(setting_bool("unknown"))


if __name__ == "__main__":
    unittest.main()
