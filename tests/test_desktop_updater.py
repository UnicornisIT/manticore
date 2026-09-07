import hashlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import desktop_releases as releases
from desktop import windows_client as client
from desktop.release_tools import validate_tag


def release(version='1.5.0', **overrides):
    filename = f'Manticore-Setup-{version}.exe'
    return dict(id=1, tag_name=f'v{version}', draft=False, prerelease=False, immutable=False,
                assets=[dict(id=2, name=filename, size=4, digest='sha256:' + hashlib.sha256(b'test').hexdigest(),
                             browser_download_url=f'https://github.com/UnicornisIT/manticore/releases/download/v{version}/{filename}')], **overrides)


class StableReleaseTests(unittest.TestCase):
    def test_semver_precedence(self):
        versions = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta', '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0']
        self.assertEqual(sorted(reversed(versions), key=releases.version_key), versions)
        self.assertEqual(releases.version_key('1.0.0+build.2'), releases.version_key('1.0.0+build.10'))
        for bad in ['01.0.0', '1.0', '1.0.0-01', '1.0.0-rc..1']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                releases.normalize_version(bad)

    def test_stable_validator_ignores_immutable_requirement_but_rejects_alpha(self):
        valid = releases._validated_release_payload(release(), releases.DEFAULT_GITHUB_REPOSITORY, stable_client=True)
        self.assertEqual(valid['version'], '1.5.0')
        for version in ['1.5.0-alpha', '1.5.0-beta.1', '1.5.0-rc.1', '1.5.0-rebuild']:
            with self.subTest(version=version), self.assertRaises(ValueError):
                releases._validated_release_payload(release(version), releases.DEFAULT_GITHUB_REPOSITORY, stable_client=True)
        for field in ['draft', 'prerelease']:
            payload = release(); payload[field] = True
            with self.assertRaises(ValueError):
                releases._validated_release_payload(payload, releases.DEFAULT_GITHUB_REPOSITORY, stable_client=True)

    def test_missing_or_foreign_installer_is_error_not_no_update(self):
        for field, value in [('digest', None), ('size', 0), ('browser_download_url', 'https://evil.test/file.exe'), ('browser_download_url', 'https://github.com/UnicornisIT/manticore/releases/download/v1.0.0/wrong.exe')]:
            payload = release(); payload['assets'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                releases._validated_release_payload(payload, releases.DEFAULT_GITHUB_REPOSITORY, stable_client=True)

    def test_api_failures_and_malformed_metadata(self):
        for code in [403, 404, 429, 500]:
            with mock.patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('https://api.github.com', code, 'error', {}, None)), self.assertRaises(ValueError):
                releases.fetch_stable_release()
        with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(b'broken')), self.assertRaises(ValueError):
            releases.fetch_stable_release()

    def test_tag_must_match_stable_version(self):
        validate_tag('1.2.0', 'v1.2.0')
        validate_tag('0.0.2-alpha', 'v0.0.2-alpha', allow_prerelease=True)
        with self.assertRaises(ValueError):
            validate_tag('0.0.2-alpha', 'v0.0.3-alpha', allow_prerelease=True)
        for version, tag in [('1.2.0', 'v1.2.1'), ('1.2.0-alpha', 'v1.2.0-alpha'), ('1.2.0', 'v1.2.0-rebuild')]:
            with self.assertRaises(ValueError):
                validate_tag(version, tag)


class PreviewReleaseTests(unittest.TestCase):
    def fetch(self, payload):
        with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(payload).encode())):
            return releases.fetch_preview_release()

    def test_published_prerelease_and_unflagged_alpha_are_accepted(self):
        for flagged in [True, False]:
            payload = release('0.0.4-alpha'); payload['prerelease'] = flagged
            self.assertEqual(self.fetch([payload])['version'], '0.0.4-alpha')

    def test_selection_uses_semver_not_publication_order(self):
        draft = release('9.0.0'); draft['draft'] = True
        items = [release('1.0.0-beta.2'), draft, {'tag_name': 'nightly'},
                 release('1.0.0-beta.11'), release('1.0.0-alpha')]
        self.assertEqual(self.fetch(items)['version'], '1.0.0-beta.11')
        self.assertEqual(self.fetch(items + [release('1.0.0')])['version'], '1.0.0')

    def test_pagination(self):
        pages = [io.BytesIO(json.dumps([release('1.0.0-alpha')] * 100).encode()),
                 io.BytesIO(json.dumps([release('1.0.0-beta')]).encode())]
        with mock.patch('urllib.request.urlopen', side_effect=pages) as fetch:
            self.assertEqual(releases.fetch_preview_release()['version'], '1.0.0-beta')
            self.assertTrue(fetch.call_args.args[0].full_url.endswith('page=2'))

    def test_bad_latest_installer_is_error_without_fallback(self):
        for field, value in [('digest', None), ('size', 0),
                             ('browser_download_url', 'https://evil.test/file.exe'),
                             ('browser_download_url', 'https://github.com/UnicornisIT/manticore/releases/download/v1.0.0/wrong.exe')]:
            payload = release('1.0.0-beta'); payload['prerelease'] = True
            payload['assets'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.fetch([payload, release('1.0.0-alpha')])

    def test_empty_and_malformed_lists(self):
        for payload in [[], {}, [None], [{'draft': True, 'tag_name': 'v1.0.0'}]]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.fetch(payload)

    @mock.patch.object(client, 'current_version', return_value='0.0.3-alpha')
    @mock.patch.object(client, 'load_trust_policy', return_value={'signer_certificate_sha256': ''})
    @mock.patch.object(releases, 'fetch_stable_release')
    @mock.patch.object(releases, 'fetch_preview_release')
    def test_alpha_client_channel_and_no_downgrade(self, preview, stable, _policy, _version):
        self.assertEqual(client.update_channel(), 'preview')
        self.assertEqual(client.DesktopUpdater(mock.Mock()).status()['channel'], 'preview')
        for version, expected in [('0.0.4-alpha', True), ('0.0.3-beta', True),
                                  ('0.0.3', True), ('0.0.3-alpha', False), ('0.0.2-alpha', False)]:
            preview.return_value = {'version': version}
            self.assertEqual(bool(client.fetch_update_manifest()), expected)
        stable.assert_not_called()

    def test_channel_follows_installed_version(self):
        for version, expected in [('1.0.0-alpha', 'preview'), ('1.0.0-beta.1', 'preview'),
                                  ('1.0.0-rc.1', 'preview'), ('1.0.0', 'stable'), ('1.0.0+build.1', 'stable')]:
            with mock.patch.object(client, 'current_version', return_value=version):
                self.assertEqual(client.update_channel(), expected)


class UpdateChannelTests(unittest.TestCase):
    def test_channels_split_stable_and_preliminary_even_with_wrong_github_flag(self):
        flagged = release('0.0.6'); flagged['prerelease'] = True
        draft = release('9.0.0'); draft['draft'] = True
        items = [release('0.0.5-alpha'), release('0.0.4'), flagged, draft]
        with mock.patch.object(releases, '_fetch_release_json', return_value=items):
            self.assertEqual(releases.fetch_channel_release('stable')['version'], '0.0.4')
            self.assertEqual(releases.fetch_channel_release('preview')['version'], '0.0.6')

    def test_missing_stable_release_is_not_an_error(self):
        with mock.patch.object(releases, '_fetch_release_json', return_value=[release('0.0.5-alpha')]):
            self.assertEqual(releases.fetch_channel_release('stable'), {})
        with mock.patch.object(releases, '_fetch_release_json', return_value=[release('1.0.0')]):
            self.assertEqual(releases.fetch_channel_release('preview'), {})

    def test_explicit_channel_still_blocks_downgrade(self):
        with mock.patch.object(client, 'current_version', return_value='0.0.5-alpha'), \
             mock.patch.object(client, 'load_trust_policy', return_value={'signer_certificate_sha256': ''}), \
             mock.patch.object(releases, 'fetch_channel_release') as fetch:
            for version, expected in [('0.0.4', False), ('0.0.5-alpha', False), ('0.0.5', True)]:
                fetch.return_value = {'version': version}
                self.assertEqual(bool(client.fetch_update_manifest(channel='stable')), expected)
                fetch.assert_called_with('stable')

    def test_independent_status_and_channel_routing(self):
        api = client.DesktopApi('')
        stable, preview = (api._channel_updaters[name] for name in ('stable', 'preview'))
        stable._set(state='current')
        preview._set(state='available', version='0.0.6-alpha')
        states = api.get_update_channels()
        self.assertEqual(states['stable']['state'], 'current')
        self.assertEqual(states['preview']['version'], '0.0.6-alpha')
        states['preview']['version'] = 'tampered'
        self.assertEqual(preview.status()['version'], '0.0.6-alpha')
        with mock.patch.object(preview, 'download') as download, mock.patch.object(stable, 'download') as other:
            api.download_update('preview')
            download.assert_called_once()
            other.assert_not_called()
        for channel in ['nightly', '../stable', None, []]:
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                api.check_for_update(channel)

    def test_installing_one_channel_blocks_other_install_and_download(self):
        api = client.DesktopApi('')
        api._channel_updaters['stable']._set(state='installing')
        preview = api._channel_updaters['preview']
        with mock.patch.object(preview, 'install') as install, mock.patch.object(preview, 'download') as download:
            api.install_approved_update('preview')
            api.download_update('preview')
            install.assert_not_called()
            download.assert_not_called()


class DesktopUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = mock.patch.object(client, 'application_data_directory', return_value=self.root)
        self.data.start(); self.addCleanup(self.data.stop)
        self.packaged = mock.patch.object(client.sys, 'frozen', True, create=True)
        self.packaged.start(); self.addCleanup(self.packaged.stop)
        self.updater = client.DesktopUpdater(mock.Mock())
        self.manifest = dict(version='1.5.0', size=4, sha256=hashlib.sha256(b'test').hexdigest(),
                             download_url='https://github.com/example', signer_certificate_sha256='')

    def downloaded(self):
        path = self.root / 'installer.exe'; path.write_bytes(b'test')
        self.updater._manifest = self.manifest
        self.updater._installer = path
        self.updater._set(state='downloaded')
        return path

    def test_successful_download_and_progress(self):
        self.updater._manifest = self.manifest
        with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(b'test')):
            self.updater._download()
        state = self.updater.status()
        self.assertEqual(state['state'], 'downloaded')
        self.assertEqual(state['percent'], 100)
        self.assertEqual(state['downloaded'], 4)
        self.assertNotIn('download_url', state)
        self.assertNotIn('sha256', state)

    def test_corrupt_or_incomplete_download_removed(self):
        for body in [b'bad', b'evil', b'toolong']:
            with mock.patch('urllib.request.urlopen', return_value=io.BytesIO(body)), self.assertRaises(ValueError):
                client.download_installer(self.manifest)
            self.assertEqual(list((self.root / 'updates').glob('*.exe')), [])

    def test_not_enough_space(self):
        with mock.patch.object(client.shutil, 'disk_usage', return_value=mock.Mock(free=0)), self.assertRaises(OSError):
            client.download_installer(self.manifest)

    def test_parallel_check_is_coalesced(self):
        entered, resume = threading.Event(), threading.Event()
        def fetch():
            entered.set(); resume.wait(3); return self.manifest
        with mock.patch.object(client, 'fetch_update_manifest', side_effect=fetch) as check:
            self.updater.check(); self.assertTrue(entered.wait(2))
            self.assertEqual(self.updater.check()['state'], 'checking')
            check.assert_called_once()
            resume.set()
        # No download is scheduled by a check.
        self.assertFalse((self.root / 'updates').exists())

    def test_install_requires_download_and_native_confirmation(self):
        with mock.patch.object(client, 'launch_installer_after_exit') as launch:
            self.updater.install(); launch.assert_not_called()
            self.downloaded()
            with mock.patch.object(client, 'confirm_native_message', return_value=False):
                self.assertEqual(self.updater.install()['state'], 'downloaded')
            launch.assert_not_called()
            with mock.patch.object(client, 'confirm_native_message', return_value=True), mock.patch.object(client.threading, 'Timer') as timer:
                self.assertEqual(self.updater.install()['state'], 'installing')
                launch.assert_called_once()
                timer.return_value.start.assert_called_once()

    def test_tamper_after_download_cannot_install(self):
        self.downloaded().write_bytes(b'evil')
        with mock.patch.object(client, 'confirm_native_message', return_value=True), mock.patch.object(client, 'launch_installer_after_exit') as launch:
            self.assertEqual(self.updater.install()['state'], 'error')
            launch.assert_not_called()

    def test_signed_policy_still_requires_signature(self):
        path = self.downloaded(); self.manifest['signer_certificate_sha256'] = 'a' * 64
        with mock.patch.object(client, 'verify_authenticode_signature', side_effect=ValueError('bad signer')) as verify:
            with self.assertRaises(ValueError): self.updater._verify(path)
            verify.assert_called_once_with(path, 'a' * 64)

    def test_blocked_installer_does_not_close_client(self):
        self.downloaded()
        with mock.patch.object(client, 'confirm_native_message', return_value=True), mock.patch.object(client, 'launch_installer_after_exit', side_effect=OSError('blocked')), mock.patch.object(client.threading, 'Timer') as timer:
            self.assertEqual(self.updater.install()['state'], 'error')
            timer.assert_not_called()

    def test_network_error_is_recoverable(self):
        with mock.patch.object(client, 'fetch_update_manifest', side_effect=TimeoutError('timeout')):
            self.updater._check()
        self.assertEqual(self.updater.status()['state'], 'error')
        with mock.patch.object(client, 'fetch_update_manifest', return_value={}):
            self.updater._check()
        self.assertEqual(self.updater.status()['state'], 'current')

    def test_startup_check_is_delayed_and_does_not_download(self):
        webview = mock.MagicMock()
        with mock.patch.dict('sys.modules', {'webview': webview}), mock.patch.object(client, 'check_server_connection', return_value=''), mock.patch.object(client.threading, 'Timer') as timer, mock.patch.object(client, 'download_installer') as download:
            client.open_desktop_window('https://example.test')
            webview.start.call_args.args[0]()
            self.assertEqual(timer.call_args.args[0], 4)
            timer.return_value.start.assert_called_once()
            download.assert_not_called()

    def test_github_check_does_not_require_manticore_server(self):
        webview = mock.MagicMock()
        window = webview.create_window.return_value
        loaded = window.events.loaded
        window._resolve_url.side_effect = lambda path: 'http://127.0.0.1:7331/' + Path(path).name
        window.get_current_url.return_value = 'http://127.0.0.1:7331/connection_error.html'
        with mock.patch.dict('sys.modules', {'webview': webview}), mock.patch.object(client, 'check_server_connection', return_value='Server unavailable'), mock.patch.object(client.threading, 'Timer') as timer:
            client.open_desktop_window('https://offline.example.test')
            webview.start.call_args.args[0]()
            self.assertEqual(timer.call_args.args[0], 4)
            timer.return_value.start.assert_called_once()
            self.assertTrue(str(webview.create_window.return_value.load_url.call_args.args[0]).endswith('connection_error.html'))
            loaded.__iadd__.call_args.args[0]()
            window.evaluate_js.assert_called_once()
            window.evaluate_js.reset_mock()
            window.get_current_url.return_value = 'https://untrusted.example.test/'
            loaded.__iadd__.call_args.args[0]()
            window.evaluate_js.assert_not_called()

    def test_unsigned_policy_is_explicit_and_source_is_fixed(self):
        directory = self.root / 'desktop'; directory.mkdir()
        path = directory / 'trusted_update.json'
        payload = dict(github_repository='UnicornisIT/manticore', signer_certificate_sha256='')
        with mock.patch.object(client, 'bundle_root', return_value=self.root):
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError): client.load_trust_policy()
            payload['allow_unsigned_updates'] = True; path.write_text(json.dumps(payload))
            self.assertEqual(client.load_trust_policy()['signer_certificate_sha256'], '')
            payload['github_repository'] = 'other/repo'; path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError): client.load_trust_policy()


if __name__ == '__main__':
    unittest.main()
