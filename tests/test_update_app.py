import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import update_app


class UpdateAppTests(unittest.TestCase):
    def test_update_stops_when_git_metadata_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(update_app.UpdateError) as raised:
                update_app.update_git_repository(Path(tmp_dir))

        message = str(raised.exception)
        self.assertIn("Program files were not updated", message)
        self.assertIn("not a Git repository", message)

    def test_version_comparison_accepts_release_tag_prefix(self):
        self.assertTrue(update_app.is_release_newer('v1.2.0', '1.1.9'))
        self.assertFalse(update_app.is_release_newer('v1.1.1', '1.1.1'))
        self.assertFalse(update_app.is_release_newer('v1.1.0', '1.1.1'))

    def test_github_repository_is_derived_from_https_and_ssh_remotes(self):
        self.assertEqual(
            update_app.github_repository_from_remote('https://github.com/UnicornisIT/manticore.git'),
            'UnicornisIT/manticore',
        )
        self.assertEqual(
            update_app.github_repository_from_remote('git@github.com:UnicornisIT/manticore.git'),
            'UnicornisIT/manticore',
        )

    def test_latest_release_response_is_validated(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    'tag_name': 'v1.2.0',
                    'name': 'manticore 1.2.0',
                    'html_url': 'https://github.com/UnicornisIT/manticore/releases/tag/v1.2.0',
                    'published_at': '2026-08-30T10:00:00Z',
                }).encode('utf-8')

        with mock.patch.object(update_app.urllib.request, 'urlopen', return_value=FakeResponse()):
            release = update_app.fetch_latest_release(
                Path('.'),
                api_url='https://api.github.com/repos/UnicornisIT/manticore/releases/latest',
            )

        self.assertEqual(release['tag_name'], 'v1.2.0')

    def test_update_status_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            status_path = Path(tmp_dir) / 'status.json'
            update_app.write_update_status(status_path, 'queued', 'Queued', current_version='1.1.1')
            status = update_app.read_update_status(status_path)

        self.assertEqual(status['state'], 'queued')
        self.assertEqual(status['current_version'], '1.1.1')
        self.assertIn('updated_at', status)

    @unittest.skipUnless(shutil.which('git'), 'Git is required for the updater integration test')
    def test_repository_fast_forwards_to_release_tag(self):
        def git(cwd, *args):
            return subprocess.run(
                ['git', *args],
                cwd=str(cwd),
                check=True,
                text=True,
                capture_output=True,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / 'source'
            deployed = root / 'deployed'
            source.mkdir()
            git(source, 'init')
            git(source, 'config', 'user.email', 'tests@example.test')
            git(source, 'config', 'user.name', 'Updater Tests')
            (source / 'VERSION').write_text('1.1.1\n', encoding='utf-8')
            git(source, 'add', 'VERSION')
            git(source, 'commit', '-m', 'Initial release')
            git(root, 'clone', str(source), str(deployed))

            (source / 'VERSION').write_text('1.2.0\n', encoding='utf-8')
            git(source, 'add', 'VERSION')
            git(source, 'commit', '-m', 'Next release')
            git(source, 'tag', 'v1.2.0')

            changed = update_app.update_git_repository(deployed, 'v1.2.0')

            self.assertTrue(changed)
            self.assertEqual((deployed / 'VERSION').read_text(encoding='utf-8').strip(), '1.2.0')


if __name__ == "__main__":
    unittest.main()
