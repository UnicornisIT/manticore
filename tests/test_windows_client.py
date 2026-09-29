import base64
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop import windows_client


class WindowsClientTests(unittest.TestCase):
    def test_config_atomic_backup_and_corrupt_recovery(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            windows_client.save_config({'generation': 1})
            windows_client.save_config({'generation': 2})
            path = Path(directory) / windows_client.CONFIG_FILENAME
            backup = path.with_suffix(path.suffix + '.bak')
            self.assertEqual(json.loads(backup.read_text(encoding='utf-8'))['generation'], 1)
            path.write_text('{broken json', encoding='utf-8')
            recovered = windows_client.load_config()
            self.assertEqual(recovered['generation'], 1)
            self.assertTrue(list(Path(directory).glob('desktop-config.json.corrupt-*')))

    def test_interrupted_config_replace_keeps_previous_json(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            windows_client.save_config({'generation': 1})
            path = Path(directory) / windows_client.CONFIG_FILENAME
            real_replace = os.replace

            def fail_main_replace(source, destination):
                if Path(destination) == path:
                    raise OSError('simulated interrupted replace')
                return real_replace(source, destination)

            with mock.patch('desktop.windows_client.os.replace', side_effect=fail_main_replace):
                with self.assertRaises(OSError):
                    windows_client.save_config({'generation': 2})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['generation'], 1)

    def test_fallback_path_cannot_escape_application_directory(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            config = windows_client.migrate_config({
                'primary_source': {'type': 'sqlite', 'path': '..\\server\\base.db'},
                'fallback_sources': {'sqlite:..\\server\\base.db': str(Path(directory).parent / 'escape.db')},
            })
            fallback = Path(windows_client.get_fallback_path(config)).resolve()
            self.assertEqual(os.path.commonpath([str(Path(directory).resolve()), str(fallback)]), str(Path(directory).resolve()))

    def test_sqlite_source_states_locked_corrupt_and_too_new(self):
        with tempfile.TemporaryDirectory() as directory:
            locked = Path(directory) / 'locked.db'
            writer = sqlite3.connect(locked, timeout=0.1, isolation_level=None)
            writer.execute('CREATE TABLE sample (id INTEGER)')
            writer.execute('BEGIN EXCLUSIVE')
            try:
                result = windows_client.inspect_sqlite_source(str(locked), timeout=0.1)
                self.assertEqual(result['code'], windows_client.SOURCE_LOCKED)
                self.assertTrue(result['available'])
            finally:
                writer.execute('ROLLBACK')
                writer.close()

            corrupt = Path(directory) / 'corrupt.db'
            corrupt.write_bytes(b'not sqlite' * 50)
            self.assertEqual(
                windows_client.inspect_sqlite_source(str(corrupt))['code'],
                windows_client.SOURCE_CORRUPT,
            )

            newer = Path(directory) / 'newer.db'
            connection = sqlite3.connect(newer)
            connection.execute('CREATE TABLE schema_metadata (id INTEGER PRIMARY KEY, version INTEGER, updated_at TEXT)')
            connection.execute('INSERT INTO schema_metadata VALUES (1, ?, NULL)', (windows_client.CURRENT_SCHEMA_VERSION + 1,))
            connection.commit()
            connection.close()
            self.assertEqual(
                windows_client.inspect_sqlite_source(str(newer))['code'],
                windows_client.SOURCE_SCHEMA_TOO_NEW,
            )

    def test_dirty_fallback_is_backed_up_and_archived(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            config = windows_client.migrate_config({'mode': 'remote', 'server_url': 'https://primary.example'})
            fallback = Path(windows_client.get_fallback_path(config))
            fallback.parent.mkdir(parents=True)
            connection = sqlite3.connect(fallback)
            connection.execute('CREATE TABLE fallback_state (id INTEGER PRIMARY KEY, primary_source_id TEXT, session_id TEXT, state TEXT, started_at TEXT, changed_at TEXT, archived_at TEXT)')
            connection.execute('CREATE TABLE fallback_change_log (id INTEGER PRIMARY KEY, session_id TEXT)')
            connection.execute("INSERT INTO fallback_state VALUES (1, 'primary', 'session-1', 'dirty', '', '', NULL)")
            connection.execute('CREATE TABLE business_data (value TEXT)')
            connection.execute("INSERT INTO business_data VALUES ('локальные данные')")
            connection.commit()
            connection.close()
            result = windows_client.archive_dirty_fallback(config)
            self.assertTrue(Path(result['backup']).is_file())
            connection = sqlite3.connect(fallback)
            self.assertEqual(connection.execute('SELECT state FROM fallback_state').fetchone()[0], 'archived_with_unsynced_changes')
            self.assertEqual(connection.execute('SELECT value FROM business_data').fetchone()[0], 'локальные данные')
            connection.close()
    def test_legacy_local_config_migrates_without_losing_primary(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            primary = str(Path(directory) / 'network' / 'main.db')
            migrated = windows_client.migrate_config({'mode': 'local', 'database_path': primary})
            self.assertEqual(migrated['primary_source'], {'type': 'sqlite', 'path': primary})
            self.assertEqual(migrated['active_source'], 'primary')
            self.assertEqual(migrated['preferred_source'], 'ask')
            self.assertNotEqual(windows_client.get_fallback_path(migrated), primary)

    def test_fallback_is_stable_per_primary_and_does_not_overwrite_it(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            first = windows_client.migrate_config({'mode': 'remote', 'server_url': 'https://one.example'})
            again = windows_client.migrate_config(first)
            second = windows_client.migrate_config({'mode': 'remote', 'server_url': 'https://two.example'})
            self.assertEqual(windows_client.get_fallback_path(first), windows_client.get_fallback_path(again))
            self.assertNotEqual(windows_client.get_fallback_path(first), windows_client.get_fallback_path(second))
            self.assertEqual(first['primary_source']['url'], 'https://one.example')

    def test_unavailable_primary_can_select_fallback_and_keep_primary(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(windows_client, 'application_data_directory', return_value=Path(directory)), \
             mock.patch.object(windows_client, 'check_source_connection', return_value='Недоступна'), \
             mock.patch.object(windows_client, 'show_source_dialog', return_value={'action': 'fallback', 'remember': True}), \
             mock.patch.object(windows_client, 'save_config') as save:
            config = windows_client.migrate_config({'mode': 'remote', 'server_url': 'https://primary.example'})
            source, resolved = windows_client.resolve_active_source(config)
            self.assertTrue(source['fallback'])
            self.assertEqual(resolved['primary_source']['url'], 'https://primary.example')
            self.assertEqual(resolved['preferred_source'], 'fallback')
            save.assert_called()

    def test_existing_primary_sqlite_is_selected(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ):
            database = Path(directory) / 'primary.db'
            connection = sqlite3.connect(database)
            try:
                connection.execute('CREATE TABLE sample (id INTEGER)')
                connection.commit()
            finally:
                connection.close()
            config = windows_client.migrate_config({'mode': 'local', 'database_path': str(database)})
            source, _ = windows_client.resolve_active_source(config)
            self.assertEqual(source['path'], str(database))
            self.assertFalse(source.get('fallback', False))

    def test_recovered_primary_prompts_without_automatic_switch(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(windows_client, 'application_data_directory', return_value=Path(directory)), \
             mock.patch.object(windows_client, 'check_source_connection', return_value=''), \
             mock.patch.object(windows_client, 'show_source_dialog', return_value={'action': 'primary', 'remember': True}) as dialog, \
             mock.patch.object(windows_client, 'save_config'):
            config = windows_client.migrate_config({'mode': 'remote', 'server_url': 'https://primary.example'})
            fallback = Path(windows_client.get_fallback_path(config))
            fallback.parent.mkdir(parents=True)
            connection = sqlite3.connect(fallback)
            connection.execute('CREATE TABLE kept (value TEXT)')
            connection.execute("INSERT INTO kept VALUES ('independently')")
            connection.commit()
            connection.close()
            config['active_source'] = 'fallback'
            source, resolved = windows_client.resolve_active_source(config)
            self.assertEqual(source['url'], 'https://primary.example')
            self.assertEqual(resolved['preferred_source'], 'primary')
            connection = sqlite3.connect(fallback)
            self.assertEqual(connection.execute('SELECT value FROM kept').fetchone()[0], 'independently')
            connection.close()
            self.assertTrue(dialog.call_args.args[0]['recovered'])

    def test_fallback_preference_never_discards_primary(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            windows_client, 'application_data_directory', return_value=Path(directory)
        ), mock.patch.object(windows_client, 'check_source_connection', return_value=''):
            config = windows_client.migrate_config({'mode': 'remote', 'server_url': 'https://primary.example'})
            fallback = Path(windows_client.get_fallback_path(config))
            fallback.parent.mkdir(parents=True)
            connection = sqlite3.connect(fallback)
            connection.execute('CREATE TABLE fallback_data (id INTEGER)')
            connection.commit()
            connection.close()
            config.update(active_source='fallback', preferred_source='fallback')
            source, resolved = windows_client.resolve_active_source(config)
            self.assertTrue(source['fallback'])
            self.assertEqual(resolved['primary_source']['url'], 'https://primary.example')
    def test_remote_server_requires_https(self):
        self.assertEqual(
            windows_client.normalize_server_url("https://example.test/manticore/"),
            "https://example.test/manticore",
        )
        with self.assertRaises(ValueError):
            windows_client.normalize_server_url("http://example.test")
        self.assertEqual(windows_client.normalize_server_url("http://127.0.0.1:5000"), "http://127.0.0.1:5000")

    def test_database_path_validation(self):
        with tempfile.TemporaryDirectory(prefix="manticore_client_") as directory:
            database = windows_client.normalize_database_path(str(Path(directory) / "current.db"))
            self.assertTrue(database.endswith("current.db"))
            with self.assertRaises(ValueError):
                windows_client.normalize_database_path(str(Path(directory) / "current.xlsx"))

    def test_version_comparison_key(self):
        self.assertGreater(windows_client.version_key("2.0.0"), windows_client.version_key("1.9.9"))
        self.assertGreater(windows_client.version_key("1.0.0"), windows_client.version_key("1.0.0-rc.1"))

    def test_desktop_api_never_installs_before_download(self):
        api = windows_client.DesktopApi('https://manticore.example.test')
        with mock.patch('desktop.windows_client.launch_installer_after_exit') as launch:
            result = api.install_approved_update()
            self.assertEqual(result['state'], 'disabled')
            launch.assert_not_called()

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.installed_scope_switch', return_value='/ALLUSERS')
    @mock.patch('desktop.windows_client.subprocess.Popen')
    def test_installer_launcher_waits_for_current_process(self, popen, _scope, _powershell):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(windows_client, 'application_data_directory', return_value=Path(directory)):
            windows_client.launch_installer_after_exit(Path(r'C:\Temp\Manticore-Setup-2.0.0.exe'))

        command_arguments = popen.call_args.args[0]
        encoded_command = command_arguments[-1]
        command = base64.b64decode(encoded_command).decode('utf-16le')
        self.assertIn('Wait-Process -Id', command)
        self.assertIn('Start-Process -FilePath', command)
        self.assertIn('Manticore-Setup-2.0.0.exe', command)
        self.assertIn("'/ALLUSERS'", command)
        self.assertIn("'/NORESTARTAPPLICATIONS'", command)
        self.assertIn('-PassThru -Wait', command)
        self.assertIn('--skip-update', command)
        self.assertIn('/DIR=', command)

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.installed_scope_switch', return_value='/CURRENTUSER')
    @mock.patch('desktop.windows_client.subprocess.Popen')
    def test_update_restart_resets_inherited_pyinstaller_environment(self, popen, _scope, _powershell):
        inherited = {'_PYI_PARENT_PROCESS_LEVEL': '1', '_PYI_APPLICATION_HOME_DIR': 'old-unpacked-app',
                     'PYINSTALLER_RESET_ENVIRONMENT': '0', 'PATH': 'system-path'}
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(windows_client, 'application_data_directory', return_value=Path(directory)), \
             mock.patch.dict(windows_client.os.environ, inherited, clear=True):
            windows_client.launch_installer_after_exit(Path(directory) / 'setup.exe')
            child_environment = popen.call_args.kwargs['env']
            self.assertEqual(child_environment['PYINSTALLER_RESET_ENVIRONMENT'], '1')
            self.assertEqual(child_environment['PATH'], 'system-path')
            self.assertEqual(dict(windows_client.os.environ), inherited)
            self.assertIsNot(child_environment, windows_client.os.environ)

    def test_desktop_window_uses_bundled_icon(self):
        webview = mock.MagicMock()
        window = webview.create_window.return_value
        with tempfile.TemporaryDirectory(prefix='manticore_window_') as directory:
            root = Path(directory)
            data_directory = root / 'data'
            bundle_directory = root / 'bundle'
            with (
                mock.patch.dict('sys.modules', {'webview': webview}),
                mock.patch('desktop.windows_client.application_data_directory', return_value=data_directory),
                mock.patch('desktop.windows_client.bundle_root', return_value=bundle_directory),
            ):
                windows_client.open_desktop_window('https://manticore.example.test')

        webview.settings.__setitem__.assert_has_calls([
            mock.call('ALLOW_DOWNLOADS', True),
            mock.call('OPEN_EXTERNAL_LINKS_IN_BROWSER', True),
        ])
        start_callback = webview.start.call_args.args[0]
        webview.start.assert_called_once_with(
            start_callback,
            private_mode=False,
            storage_path=str(data_directory / 'webview'),
            icon=str(bundle_directory / windows_client.WINDOW_ICON_PATH),
        )
        self.assertTrue(str(webview.create_window.call_args.args[1]).endswith('desktop\\ui\\startup.html'))
        with mock.patch('desktop.windows_client.check_server_connection', return_value=''):
            start_callback()
        window.load_url.assert_called_once_with('https://manticore.example.test')

    @mock.patch('desktop.windows_client.threading.Timer')
    @mock.patch('desktop.windows_client.save_config')
    def test_setup_api_validates_and_saves_remote_configuration(self, save_config, timer):
        api = windows_client.SetupApi({'local_secret_key': 'kept-secret'}, 'configuration')

        result = api.submit_configuration({'mode': 'remote', 'server_url': 'https://example.test/'})

        self.assertTrue(result['ok'])
        saved = save_config.call_args.args[0]
        self.assertEqual(saved['server_url'], 'https://example.test')
        self.assertEqual(saved['update_server_url'], 'https://example.test')
        self.assertEqual(saved['local_secret_key'], 'kept-secret')
        timer.assert_called_once_with(0.05, api._close_window)
        timer.return_value.start.assert_called_once_with()

    def test_setup_api_rejects_remote_http(self):
        api = windows_client.SetupApi({}, 'configuration')
        result = api.submit_configuration({'mode': 'remote', 'server_url': 'http://example.test'})
        self.assertFalse(result['ok'])
        self.assertIn('HTTPS', result['error'])

    @mock.patch('desktop.windows_client.threading.Timer')
    def test_setup_api_admin_password_round_trip(self, timer):
        with tempfile.TemporaryDirectory(prefix='manticore_password_') as directory:
            result_path = Path(directory) / 'password.secret'
            api = windows_client.SetupApi({}, 'admin-password', str(result_path))
            mismatch = api.submit_admin_password('password-one', 'password-two')
            accepted = api.submit_admin_password('password-one', 'password-one')
            self.assertFalse(mismatch['ok'])
            self.assertTrue(accepted['ok'])
            self.assertEqual(result_path.read_text(encoding='utf-8'), 'password-one')
            timer.assert_called_once_with(0.05, api._close_window)
            timer.return_value.start.assert_called_once_with()

    @mock.patch('desktop.windows_client.urllib.request.urlopen')
    def test_connection_check_returns_friendly_error(self, urlopen):
        urlopen.side_effect = windows_client.urllib.error.URLError('host unavailable')
        error = windows_client.check_server_connection('https://example.test', timeout=0.1)
        self.assertIn('Сервер не ответил', error)

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.win_verify_trust', return_value=windows_client.WINTRUST_UNTRUSTED_ROOT)
    @mock.patch('desktop.windows_client.subprocess.run')
    def test_authenticode_signer_certificate_is_pinned(self, run, verify_trust, _powershell):
        run.return_value = mock.Mock(returncode=0, stdout=('a' * 64) + '\n', stderr='')
        installer = Path(r'C:\Temp\Manticore-Setup-2.0.0.exe')

        windows_client.verify_authenticode_signature(installer, 'a' * 64)
        verify_trust.assert_called_once_with(installer)
        with self.assertRaises(ValueError):
            windows_client.verify_authenticode_signature(installer, 'b' * 64)

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.win_verify_trust', return_value=0x80096010)
    @mock.patch('desktop.windows_client.subprocess.run')
    def test_authenticode_rejects_a_bad_file_digest(self, run, _verify_trust, _powershell):
        run.return_value = mock.Mock(returncode=0, stdout=('a' * 64) + '\n', stderr='')

        with self.assertRaisesRegex(ValueError, '0x80096010'):
            windows_client.verify_authenticode_signature(
                Path(r'C:\Temp\Manticore-Setup-tampered.exe'),
                'a' * 64,
            )

    @mock.patch('desktop.windows_client.current_version', return_value='1.0.0')
    @mock.patch('desktop.windows_client.load_trust_policy', return_value={'signer_certificate_sha256': ''})
    @mock.patch('desktop.windows_client.desktop_releases.fetch_stable_release')
    def test_direct_latest_supports_version_jump_and_blocks_downgrade(self, fetch, _policy, _version):
        for version, expected in [('1.5.0', True), ('1.0.0', False), ('0.9.0', False)]:
            fetch.return_value = {'version': version}
            result = windows_client.fetch_update_manifest('https://ignored.example.test')
            self.assertEqual(bool(result), expected)
        self.assertEqual(fetch.call_args, mock.call())


if __name__ == "__main__":
    unittest.main()
