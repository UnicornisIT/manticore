import unittest

from scripts.check_forbidden_files import is_forbidden


class ForbiddenFilesTests(unittest.TestCase):
    def test_runtime_artifacts_are_rejected(self):
        for path in (
            ".env",
            "uploads/baze.db",
            "fallback.db",
            "fallback_changes_20261012_120000.csv",
            "students.xlsx",
            "desktop-config.json",
        ):
            self.assertTrue(is_forbidden(path), path)

    def test_tracked_sources_are_allowed(self):
        for path in (
            ".env.example",
            "static/template.xlsx",
            "templates/fallback_changes.html",
            "app.py",
        ):
            self.assertFalse(is_forbidden(path), path)


if __name__ == "__main__":
    unittest.main()
