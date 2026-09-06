import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop import windows_client


class WindowsClientTests(unittest.TestCase):
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
