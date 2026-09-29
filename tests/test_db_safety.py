import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db_safety


class DatabaseSafetyTests(unittest.TestCase):
    def make_legacy_database(self, directory):
        path = Path(directory) / 'legacy.db'
        connection = sqlite3.connect(path)
        connection.execute('CREATE TABLE preserved (value TEXT)')
        connection.execute("INSERT INTO preserved VALUES ('исходные данные')")
        connection.commit()
        connection.close()
        return path

    def test_successful_migration_is_versioned_backed_up_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_legacy_database(directory)

            def migration(connection):
                connection.execute('ALTER TABLE preserved ADD COLUMN extra TEXT')

            backup = db_safety.migrate_database(path, migration)
            self.assertTrue(backup.is_file())
            db_safety.check_database_file(backup, full=True)
            self.assertEqual(db_safety.read_schema_version(path), db_safety.CURRENT_SCHEMA_VERSION)
            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute('SELECT value FROM preserved').fetchone()[0], 'исходные данные')
            connection.close()

    def test_failed_migration_rolls_back_and_restores_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_legacy_database(directory)

            def migration(connection):
                connection.execute('ALTER TABLE preserved ADD COLUMN partial TEXT')
                connection.execute("UPDATE preserved SET value='повреждено'")
                raise RuntimeError('fault injection')

            with self.assertRaises(RuntimeError):
                db_safety.migrate_database(path, migration)
            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute('SELECT value FROM preserved').fetchone()[0], 'исходные данные')
            self.assertNotIn('partial', [row[1] for row in connection.execute('PRAGMA table_info(preserved)')])
            connection.close()
            self.assertTrue(list(Path(directory).glob('legacy.backup-before-migration-*.db')))

    def test_newer_schema_is_never_opened_for_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_legacy_database(directory)
            connection = sqlite3.connect(path)
            db_safety.set_schema_version(connection, db_safety.CURRENT_SCHEMA_VERSION + 1)
            connection.commit()
            connection.close()
            callback = mock.Mock()
            with self.assertRaises(db_safety.DatabaseSchemaTooNewError):
                db_safety.migrate_database(path, callback)
            callback.assert_not_called()

    def test_locked_database_is_not_treated_as_corrupt_or_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_legacy_database(directory)
            writer = sqlite3.connect(path, timeout=0.1, isolation_level=None)
            writer.execute('BEGIN EXCLUSIVE')
            try:
                with self.assertRaises(db_safety.DatabaseLockedError):
                    db_safety.migrate_database(path, mock.Mock())
            finally:
                writer.execute('ROLLBACK')
                writer.close()
            self.assertFalse(list(Path(directory).glob('legacy.corrupt-detected-*.db')))

    def test_corrupt_database_is_untouched_and_forensic_copy_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.db'
            original = b'not a sqlite database\x00\xff' * 20
            path.write_bytes(original)
            with self.assertRaises(db_safety.DatabaseIntegrityError):
                db_safety.migrate_database(path, lambda connection: None)
            self.assertEqual(path.read_bytes(), original)
            forensic = list(Path(directory).glob('broken.corrupt-detected-*.db'))
            self.assertEqual(len(forensic), 1)
            self.assertEqual(forensic[0].read_bytes(), original)

    def test_disk_full_stops_before_backup_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.make_legacy_database(directory)
            with mock.patch('db_safety.shutil.disk_usage') as usage:
                usage.return_value = mock.Mock(free=0)
                with self.assertRaises(db_safety.InsufficientDiskSpaceError):
                    db_safety.sqlite_backup(path, Path(directory) / 'backup.db')
            self.assertFalse((Path(directory) / 'backup.db').exists())

    def test_migration_backup_retention_keeps_latest_five(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'base.db'
            database.touch()
            for index in range(7):
                backup = Path(directory) / f'base.backup-before-migration-20260929-12000{index}.db'
                backup.write_bytes(str(index).encode())
                backup.touch()
                # Deterministic ordering even on filesystems with coarse timestamps.
                import os
                os.utime(backup, (1000 + index, 1000 + index))
            db_safety.retain_migration_backups(database)
            remaining = sorted(Path(directory).glob('base.backup-before-migration-*.db'))
            self.assertEqual(len(remaining), 5)


if __name__ == '__main__':
    unittest.main()
