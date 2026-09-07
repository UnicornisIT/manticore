"""Execute the actual workflow PowerShell against an isolated fake GitHub CLI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh')
WORKFLOW = ROOT / '.github/workflows/windows-release.yml'

# This fake replaces only the network boundary; PowerShell, hashing, parsing,
# downloads and failure propagation execute exactly as in the workflow.
FAKE_GH = r'''
import hashlib, json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
root = Path.cwd()
state_path = root / 'state.json'
state = json.loads(state_path.read_text())
mode = state['mode']
state['calls'].append(args)
def save():
    state_path.write_text(json.dumps(state))
def fail(message):
    save()
    print(message, file=sys.stderr)
    sys.exit(1)
cmd = args[1] if args[0] == 'release' else 'api'
if cmd != 'api':
    assert args[2] == state['tag']
    assert args[args.index('--repo') + 1] == 'UnicornisIT/manticore'
if mode == cmd + '_error':
    fail('simulated ' + cmd + ' error')
source = root / 'dist/installer'
remote = root / 'remote'
installer = state['installer']
def assets():
    result = []
    for i, p in enumerate(sorted(remote.iterdir())):
        result.append({'name': p.name, 'size': p.stat().st_size, 'id': i + 1,
                       'downloadCount': int(state.get('downloaded', False)),
                       'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest(),
                       'browser_download_url': 'https://github.com/UnicornisIT/manticore/releases/download/' + state['tag'] + '/' + p.name})
    return result
if cmd == 'view':
    state['views'] += 1
    if mode == 'lookup_error':
        fail('HTTP 403 forbidden')
    if mode in ('new', 'create_error') and state['views'] == 1:
        fail('release not found')
    result = {'databaseId': 42, 'tagName': state['tag'], 'isDraft': mode != 'published', 'assets': assets()}
    if mode == 'wrong_tag': result['tagName'] = 'v9.9.9'
    if mode == 'changed_id' and state['views'] > 1: result['databaseId'] = 43
    if mode == 'changed_assets' and state.get('downloaded'): result['assets'][0]['id'] = 999
    if mode == 'cli_without_digest':
        for asset in result['assets']: asset.pop('digest')
    print(json.dumps(result))
elif cmd == 'create':
    assert '--draft' in args and '--verify-tag' in args
elif cmd == 'upload':
    assert '--clobber' in args
    for name in (installer, 'desktop-update.json', 'SHA256SUMS.txt'):
        assert 'dist/installer/' + name in args
        shutil.copyfile(source / name, remote / name)
    if mode == 'missing_asset': (remote / 'desktop-update.json').unlink()
    if mode == 'extra_exe': (remote / 'other.exe').write_bytes(b'other')
    if mode == 'remote_size': (remote / installer).write_bytes(b'short')
elif cmd == 'download':
    dest = Path(args[args.index('--dir') + 1])
    assert dest.is_dir() and not (dest / installer).exists()
    for name in (installer, 'desktop-update.json', 'SHA256SUMS.txt'):
        shutil.copyfile(remote / name, dest / name)
    targets = {'corrupt_installer': installer, 'corrupt_metadata': 'desktop-update.json', 'corrupt_sums': 'SHA256SUMS.txt'}
    if mode in targets:
        p = dest / targets[mode]
        content = p.read_bytes()
        p.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    if mode == 'download_missing': (dest / installer).unlink()
    state['downloaded'] = True
elif cmd == 'api':
    assert args == ['api', 'repos/UnicornisIT/manticore/releases/42']
    result = {'id': 42, 'tag_name': state['tag'], 'draft': mode != 'published_during_download', 'assets': assets()}
    item = next(a for a in result['assets'] if a['name'] == installer)
    if mode == 'api_no_digest': item.pop('digest')
    if mode == 'api_wrong_digest': item['digest'] = 'sha256:' + '0' * 64
    if mode == 'api_wrong_url': item['browser_download_url'] = 'https://example.com/setup.exe'
    print(json.dumps(result))
elif cmd == 'edit':
    assert state.get('downloaded') and '--draft=false' in args
else:
    fail('Unexpected command')
save()
'''


@unittest.skipUnless(PWSH, 'PowerShell 7 is required (available on the Windows release runner)')
class ReleasePublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = WORKFLOW.read_text(encoding='utf-8')
        stage = workflow.split('      - name: Stage and verify release, then publish to its channel\n', 1)[1]
        cls.script = textwrap.dedent(stage.split('        run: |\n', 1)[1].split('      - name:', 1)[0])

    def run_release(self, mode='reuse', version='0.0.3-alpha'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / 'dist/installer'
            assets.mkdir(parents=True)
            (root / 'remote').mkdir()
            (root / 'remote/notes.txt').write_text('preserve unrelated assets')
            installer = f'Manticore-Setup-{version}.exe'
            content = b'signed installer fixture'
            digest = hashlib.sha256(content).hexdigest()
            (assets / installer).write_bytes(content)
            metadata = dict(schema_version=1, repository='UnicornisIT/manticore', version=version,
                            tag=f'v{version}', asset=installer, size=len(content), sha256=digest)
            if mode == 'local_metadata': metadata['tag'] = 'v9.9.9'
            if mode == 'local_hash': metadata['sha256'] = '0' * 64
            (assets / 'desktop-update.json').write_text(json.dumps(metadata))
            (assets / 'SHA256SUMS.txt').write_text(f'{digest}  {installer}\n' if mode != 'local_sums' else 'wrong')
            (root / 'VERSION').write_text(version)
            (root / 'state.json').write_text(json.dumps(dict(mode=mode, tag=f'v{version}', installer=installer, calls=[], views=0)))
            (root / 'fake_gh.py').write_text(FAKE_GH)
            # A native subprocess produces real stdout/stderr and LASTEXITCODE.
            wrapper = "function gh { & $env:TEST_PYTHON (Join-Path $pwd 'fake_gh.py') @args }\n"
            (root / 'publish.ps1').write_text(wrapper + self.script)
            env = dict(os.environ, GH_REPO='UnicornisIT/manticore', RELEASE_TAG=f'v{version}',
                       RUNNER_TEMP=directory, TEST_PYTHON=sys.executable)
            result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', str(root / 'publish.ps1')],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=60)
            state = json.loads((root / 'state.json').read_text())
            self.assertEqual((root / 'remote/notes.txt').read_text(), 'preserve unrelated assets')
            return result, state['calls']

    def test_create_and_reuse_drafts_in_all_channels(self):
        for mode, version in [('new', '0.0.3-alpha'), ('reuse', '0.0.3-beta'),
                              ('reuse', '0.0.3-rc.1'), ('reuse', '0.0.3'),
                              ('cli_without_digest', '0.0.3-alpha')]:
            with self.subTest(mode=mode, version=version):
                result, calls = self.run_release(mode, version)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Draft verification: PASS', result.stdout)
                self.assertIn('Release publish: PASS', result.stdout)
                self.assertEqual(sum(c[:2] == ['release', 'create'] for c in calls), int(mode == 'new'))
                self.assertEqual(calls[-1][:2], ['release', 'edit'])
                self.assertIn('--latest=false' if '-' in version else '--latest', calls[-1])
                self.assertIn('--prerelease' if '-' in version else '--prerelease=false', calls[-1])

    def test_failures_never_publish(self):
        for mode in ['published', 'wrong_tag', 'changed_id', 'lookup_error', 'create_error',
                     'upload_error', 'view_error', 'download_error', 'api_error', 'missing_asset',
                     'extra_exe', 'remote_size', 'corrupt_installer', 'corrupt_metadata', 'corrupt_sums',
                     'download_missing', 'api_no_digest', 'api_wrong_digest', 'api_wrong_url',
                     'published_during_download', 'changed_assets', 'local_metadata', 'local_hash', 'local_sums']:
            with self.subTest(mode=mode):
                result, calls = self.run_release(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(any(c[:2] == ['release', 'edit'] for c in calls), mode)
                if mode in ('published', 'wrong_tag', 'changed_id', 'lookup_error', 'view_error',
                            'local_metadata', 'local_hash', 'local_sums'):
                    self.assertFalse(any(c[0] == 'release' and c[1] in ('upload', 'create') for c in calls), mode)

    def test_publish_error_is_not_reported_as_success(self):
        result, calls = self.run_release('edit_error')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][:2], ['release', 'edit'])
        self.assertNotIn('Release publish: PASS', result.stdout)
