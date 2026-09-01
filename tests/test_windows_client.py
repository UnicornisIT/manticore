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
        )
        timer.return_value.start.assert_called_once_with()

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.subprocess.Popen')
    def test_installer_launcher_waits_for_current_process(self, popen, _powershell):
        windows_client.launch_installer_after_exit(Path(r'C:\Temp\Manticore-Setup-2.0.0.exe'))

        command_arguments = popen.call_args.args[0]
        encoded_command = command_arguments[-1]
        command = base64.b64decode(encoded_command).decode('utf-16le')
        self.assertIn('Wait-Process -Id', command)
        self.assertIn('Start-Process -FilePath', command)
        self.assertIn('Manticore-Setup-2.0.0.exe', command)

    @mock.patch('desktop.windows_client.powershell_executable', return_value=r'C:\Windows\powershell.exe')
    @mock.patch('desktop.windows_client.subprocess.run')
    def test_authenticode_signer_certificate_is_pinned(self, run, _powershell):
        run.return_value = mock.Mock(returncode=0, stdout=('a' * 64) + '\n', stderr='')
        installer = Path(r'C:\Temp\Manticore-Setup-2.0.0.exe')

        windows_client.verify_authenticode_signature(installer, 'a' * 64)
        with self.assertRaises(ValueError):
            windows_client.verify_authenticode_signature(installer, 'b' * 64)

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


if __name__ == "__main__":
    unittest.main()
