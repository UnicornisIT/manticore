import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from desktop import release_tools


class ReleaseSignatureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'build').mkdir()
        (self.root / 'dist' / 'installer').mkdir(parents=True)
        self.exe = self.root / 'dist' / 'Manticore.exe'
        self.installer = self.root / 'dist' / 'installer' / 'Manticore-Setup-0.0.2-alpha.exe'
        self.exe.write_bytes(b'test-exe')
        self.installer.write_bytes(b'test-installer')
        self.policy = self.root / 'build' / 'trusted_update.json'
        self.policy.write_text(json.dumps({'github_repository': 'UnicornisIT/manticore', 'signer_certificate_sha256': 'a' * 64}))
        patch = mock.patch.object(release_tools, 'ROOT', self.root)
        patch.start()
        self.addCleanup(patch.stop)

    @mock.patch('desktop.windows_client.verify_authenticode_signature')
    def test_verifies_both_artifacts_with_client_policy(self, verify):
        release_tools.verify_release_signatures('0.0.2-alpha')
        self.assertEqual(verify.call_args_list, [mock.call(self.exe, 'a' * 64), mock.call(self.installer, 'a' * 64)])

    @mock.patch('desktop.windows_client.verify_authenticode_signature', side_effect=ValueError('Invalid signature'))
    def test_signature_failure_stops_release(self, verify):
        with self.assertRaisesRegex(ValueError, 'Invalid signature'):
            release_tools.verify_release_signatures('0.0.2-alpha')
        verify.assert_called_once()

    @mock.patch('desktop.windows_client.verify_authenticode_signature')
    def test_missing_pin_cannot_bypass_signature_verification(self, verify):
        self.policy.write_text(json.dumps({'github_repository': 'UnicornisIT/manticore', 'allow_unsigned_updates': True}))
        with self.assertRaisesRegex(ValueError, 'pinned publisher'):
            release_tools.verify_release_signatures('0.0.2-alpha')
        verify.assert_not_called()

    @mock.patch('desktop.windows_client.verify_authenticode_signature')
    def test_missing_installer_stops_release(self, verify):
        self.installer.unlink()
        with self.assertRaises(FileNotFoundError):
            release_tools.verify_release_signatures('0.0.2-alpha')


if __name__ == '__main__':
    unittest.main()
