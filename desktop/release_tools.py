"""Validate VERSION/tags and generate release assets. Run from the repository root."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_releases import normalize_version, DEFAULT_GITHUB_REPOSITORY


def validate_tag(version, tag, *, allow_prerelease=False):
    if allow_prerelease and tag == f"v{version}" and normalize_version(version) == version and '+' not in version:
        return
    if not re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", tag) or tag != f"v{version}":
        raise ValueError(f"Stable tag {tag!r} must exactly match VERSION ({version}).")


def version_resource(version):
    from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
    parts = tuple(map(int, re.split(r"[-+]", version)[0].split("."))) + (0,)
    resource = VSVersionInfo(ffi=FixedFileInfo(filevers=parts, prodvers=parts, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=[
        StringFileInfo([StringTable('040904B0', [StringStruct('FileVersion', version), StringStruct('ProductVersion', version), StringStruct('ProductName', 'Manticore'), StringStruct('OriginalFilename', 'Manticore.exe')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
    (ROOT / 'build').mkdir(exist_ok=True)
    (ROOT / 'build' / 'windows-version.txt').write_text(str(resource), encoding='utf-8')


def metadata(version):
    directory = ROOT / 'dist' / 'installer'
    installer = directory / f'Manticore-Setup-{version}.exe'
    with installer.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    payload = {'schema_version': 1, 'repository': DEFAULT_GITHUB_REPOSITORY, 'version': version,
               'tag': f'v{version}', 'asset': installer.name, 'size': installer.stat().st_size, 'sha256': digest}
    (directory / 'desktop-update.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    (directory / 'SHA256SUMS.txt').write_text(f'{digest}  {installer.name}\n', encoding='ascii')
    return payload


def verify_release_signatures(version):
    """Apply the same pinned-certificate trust policy as installed clients."""
    from desktop.windows_client import verify_authenticode_signature

    policy = json.loads((ROOT / 'build' / 'trusted_update.json').read_text(encoding='utf-8-sig'))
    fingerprint = str(policy.get('signer_certificate_sha256') or '').lower()
    if policy.get('github_repository') != DEFAULT_GITHUB_REPOSITORY or not re.fullmatch(r'[0-9a-f]{64}', fingerprint) or fingerprint == '0' * 64:
        raise ValueError('Signed release verification requires a pinned publisher certificate.')
    for binary in (ROOT / 'dist' / 'Manticore.exe', ROOT / 'dist' / 'installer' / f'Manticore-Setup-{version}.exe'):
        if not binary.is_file():
            raise FileNotFoundError(binary)
        verify_authenticode_signature(binary, fingerprint)
        print(f'Publisher pin and WinVerifyTrust: PASS ({binary.name})')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-tag')
    parser.add_argument('--allow-prerelease', action='store_true')
    parser.add_argument('--version-resource', action='store_true')
    parser.add_argument('--metadata', action='store_true')
    parser.add_argument('--verify-signatures', action='store_true')
    args = parser.parse_args()
    raw_version = (ROOT / 'VERSION').read_text(encoding='utf-8-sig').strip()
    version = normalize_version(raw_version)
    if raw_version != version:
        raise ValueError('VERSION must contain a plain SemVer without the v tag prefix.')
    if args.check_tag:
        validate_tag(version, args.check_tag, allow_prerelease=args.allow_prerelease)
    if args.version_resource:
        version_resource(version)
    if args.metadata:
        print(json.dumps(metadata(version)))
    if args.verify_signatures:
        verify_release_signatures(version)
