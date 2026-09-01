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

    @mock.patch('desktop.windows_client.threading.Timer')
    @mock.patch('desktop.windows_client.offer_and_install_update', return_value=True)
    def test_desktop_api_starts_approved_update_and_closes_windows(self, install_update, timer):
        api = windows_client.DesktopApi('https://manticore.example.test')

        result = api.install_approved_update()

        self.assertTrue(result['started'])
        install_update.assert_called_once_with(
            'https://manticore.example.test',
            ask_confirmation=False,
            show_check_errors=True,
            allow_same_version_rebuild=True,
        )
        timer.return_value.start.assert_called_once_with()

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.installed_scope_switch', return_value='/ALLUSERS')
    @mock.patch('desktop.windows_client.subprocess.Popen')
    def test_installer_launcher_waits_for_current_process(self, popen, _scope, _powershell):
        windows_client.launch_installer_after_exit(Path(r'C:\Temp\Manticore-Setup-2.0.0.exe'))

        command_arguments = popen.call_args.args[0]
        encoded_command = command_arguments[-1]
        command = base64.b64decode(encoded_command).decode('utf-16le')
        self.assertIn('Wait-Process -Id', command)
        self.assertIn('Start-Process -FilePath', command)
        self.assertIn('Manticore-Setup-2.0.0.exe', command)
        self.assertIn("'/ALLUSERS'", command)
        self.assertIn("'/RESTARTAPPLICATIONS'", command)

    def test_desktop_window_uses_bundled_icon(self):
        webview = mock.Mock()
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

        webview.start.assert_called_once_with(
            private_mode=False,
            storage_path=str(data_directory / 'webview'),
            icon=str(bundle_directory / windows_client.WINDOW_ICON_PATH),
        )

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

    @mock.patch('desktop.windows_client.load_trust_policy')
    @mock.patch('desktop.windows_client.desktop_releases.fetch_release_by_tag')
    @mock.patch('desktop.windows_client.urllib.request.urlopen')
    def test_update_must_match_server_approval_and_github_release(self, urlopen, fetch_release, trust_policy):
        trust_policy.return_value = {
            'github_repository': 'UnicornisIT/manticore',
            'signer_certificate_sha256': 'b' * 64,
        }
        approval = {
            'repository': 'UnicornisIT/manticore',
            'release_id': 100,
            'tag_name': 'v9.8.7',
            'version': '9.8.7',
            'asset_id': 200,
            'asset_name': 'Manticore-Setup-9.8.7.exe',
            'sha256': 'a' * 64,
            'size': 1234,
        }
        server_response = mock.MagicMock()
        server_response.__enter__.return_value.read.return_value = json.dumps({
            'repository': 'UnicornisIT/manticore',
            'approved': True,
            'approval': approval,
        }).encode('utf-8')
        urlopen.return_value = server_response
        fetch_release.return_value = {
            **approval,
            'notes': 'Исправления',
            'download_url': 'https://github.com/UnicornisIT/manticore/releases/download/v9.8.7/Manticore-Setup-9.8.7.exe',
        }

        manifest = windows_client.fetch_update_manifest('https://manticore.example.test')

        self.assertEqual(manifest['version'], '9.8.7')
        self.assertEqual(manifest['sha256'], 'a' * 64)
        self.assertEqual(manifest['signer_certificate_sha256'], 'b' * 64)

        fetch_release.return_value['sha256'] = 'c' * 64
        with self.assertRaises(ValueError):
            windows_client.fetch_update_manifest('https://manticore.example.test')

    @mock.patch('desktop.windows_client.current_version', return_value='9.8.7')
    @mock.patch('desktop.windows_client.load_trust_policy')
    @mock.patch('desktop.windows_client.desktop_releases.fetch_release_by_tag')
    @mock.patch('desktop.windows_client.urllib.request.urlopen')
    def test_same_version_rebuild_is_available_only_for_manual_install(
        self,
        urlopen,
        fetch_release,
        trust_policy,
        _current_version,
    ):
        trust_policy.return_value = {
            'github_repository': 'UnicornisIT/manticore',
            'signer_certificate_sha256': 'b' * 64,
        }
        approval = {
            'repository': 'UnicornisIT/manticore',
            'release_id': 101,
            'tag_name': 'v9.8.7-rebuild',
            'version': '9.8.7',
            'asset_id': 201,
            'asset_name': 'Manticore-Setup-9.8.7.exe',
            'sha256': 'a' * 64,
            'size': 1234,
        }
        server_response = mock.MagicMock()
        server_response.__enter__.return_value.read.return_value = json.dumps({
            'repository': 'UnicornisIT/manticore',
            'approved': True,
            'approval': approval,
        }).encode('utf-8')
        urlopen.return_value = server_response
        fetch_release.return_value = {
            **approval,
            'is_rebuild': True,
            'notes': 'Исправленная сборка',
            'download_url': (
                'https://github.com/UnicornisIT/manticore/releases/download/'
                'v9.8.7-rebuild/Manticore-Setup-9.8.7.exe'
            ),
        }

        self.assertEqual(windows_client.fetch_update_manifest('https://manticore.example.test'), {})
        manifest = windows_client.fetch_update_manifest(
            'https://manticore.example.test',
            allow_same_version_rebuild=True,
        )

        self.assertEqual(manifest['version'], '9.8.7')
        self.assertEqual(manifest['tag_name'], 'v9.8.7-rebuild')
        self.assertTrue(manifest['is_rebuild'])


if __name__ == "__main__":
    unittest.main()
