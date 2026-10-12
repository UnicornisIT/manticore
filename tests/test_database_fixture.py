import os
import sqlite3
import unittest
from pathlib import Path

from tests.app_fixture import TEST_UPLOAD_PATH, manticore


class SharedDatabaseFixtureTests(unittest.TestCase):
    def test_shared_database_remains_available_for_later_test_modules(self):
        database_path = Path(manticore.DB_PATH).resolve()

        self.assertEqual(database_path.parent, TEST_UPLOAD_PATH.resolve())
        self.assertEqual(database_path.name, "test.db")
        self.assertTrue(database_path.is_file())
        with sqlite3.connect(database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_environment_cannot_redirect_tests_to_a_production_database(self):
        self.assertEqual(Path(os.environ["UPLOAD_FOLDER"]).resolve(), TEST_UPLOAD_PATH.resolve())
        self.assertEqual(os.environ["DB_FILENAME"], "test.db")


if __name__ == "__main__":
    unittest.main()
