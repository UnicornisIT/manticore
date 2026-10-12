import sqlite3
import unittest

from db.migrations import MIGRATIONS, apply_schema_migrations


class DatabaseMigrationRegistryTests(unittest.TestCase):
    def test_registry_is_strictly_ordered_and_unique(self):
        versions = [migration.version for migration in MIGRATIONS]

        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(versions[0], 6)

    def test_legacy_schema_is_normalized_before_ordered_migrations(self):
        connection = sqlite3.connect(":memory:")
        events = []

        def compatibility_migration(conn):
            events.append("compatibility")
            conn.execute("CREATE TABLE legacy_data (value TEXT)")

        apply_schema_migrations(connection, 5, compatibility_migration)

        self.assertEqual(events, ["compatibility"])
        self.assertIsNotNone(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
        )
        self.assertEqual(
            connection.execute(
                "SELECT version, name FROM schema_version ORDER BY version"
            ).fetchall(),
            [(6, "create_schema_version_ledger")],
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
