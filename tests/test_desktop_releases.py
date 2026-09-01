import tempfile
import unittest
from pathlib import Path

import desktop_releases


class DesktopReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="manticore_desktop_release_")
        self.upload_folder = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def github_payload(self, *, immutable=True, digest=None):
        digest = digest if digest is not None else "sha256:" + "a" * 64
        return {
            "id": 123,
            "tag_name": "v2.3.4",
            "name": "v2.3.4",
            "body": "Исправления",
            "draft": False,
            "prerelease": False,
            "immutable": immutable,
            "html_url": "https://github.com/UnicornisIT/manticore/releases/tag/v2.3.4",
            "published_at": "2026-09-01T00:00:00Z",
            "assets": [{
                "id": 456,
                "name": "Manticore-Setup-2.3.4.exe",
                "size": 1024,
                "digest": digest,
                "browser_download_url": (
                    "https://github.com/UnicornisIT/manticore/releases/download/"
                    "v2.3.4/Manticore-Setup-2.3.4.exe"
                ),
            }],
        }

    def test_validates_and_approves_immutable_github_release(self):
        release = desktop_releases._validated_release_payload(
            self.github_payload(),
            "UnicornisIT/manticore",
        )
        approval = desktop_releases.approve_release(self.upload_folder, release, approved_by="admin")
        loaded = desktop_releases.load_approval(self.upload_folder)

        self.assertEqual(release["version"], "2.3.4")
        self.assertEqual(approval["sha256"], "a" * 64)
        self.assertEqual(loaded["asset_id"], 456)
        self.assertEqual(loaded["approved_by"], "admin")

    def test_rebuild_tag_uses_base_version_installer(self):
        payload = self.github_payload()
        payload["tag_name"] = "v2.3.4-rebuild.2"
        payload["name"] = "v2.3.4-rebuild.2"
        payload["html_url"] = "https://github.com/UnicornisIT/manticore/releases/tag/v2.3.4-rebuild.2"
        payload["assets"][0]["browser_download_url"] = (
            "https://github.com/UnicornisIT/manticore/releases/download/"
            "v2.3.4-rebuild.2/Manticore-Setup-2.3.4.exe"
        )

        release = desktop_releases._validated_release_payload(payload, "UnicornisIT/manticore")
        approval = desktop_releases.approve_release(self.upload_folder, release, approved_by="admin")
        loaded = desktop_releases.load_approval(self.upload_folder)

        self.assertEqual(release["version"], "2.3.4")
        self.assertTrue(release["is_rebuild"])
        self.assertTrue(approval["is_rebuild"])
        self.assertTrue(loaded["is_rebuild"])
        self.assertEqual(loaded["asset_name"], "Manticore-Setup-2.3.4.exe")

        payload["assets"][0]["name"] = "Manticore-Setup-2.3.4-rebuild.exe"
        with self.assertRaises(desktop_releases.DesktopReleaseError):
            desktop_releases._validated_release_payload(payload, "UnicornisIT/manticore")

    def test_rejects_mutable_release_and_missing_digest(self):
        with self.assertRaises(desktop_releases.DesktopReleaseError):
            desktop_releases._validated_release_payload(
                self.github_payload(immutable=False),
                "UnicornisIT/manticore",
            )
        with self.assertRaises(desktop_releases.DesktopReleaseError):
            desktop_releases._validated_release_payload(
                self.github_payload(digest=""),
                "UnicornisIT/manticore",
            )

    def test_rejects_asset_from_another_repository(self):
        payload = self.github_payload()
        payload["assets"][0]["browser_download_url"] = (
            "https://github.com/attacker/project/releases/download/v2.3.4/Manticore-Setup-2.3.4.exe"
        )
        with self.assertRaises(desktop_releases.DesktopReleaseError):
            desktop_releases._validated_release_payload(payload, "UnicornisIT/manticore")

    def test_version_comparison(self):
        self.assertTrue(desktop_releases.is_newer("1.2.0", "1.1.9"))
        self.assertFalse(desktop_releases.is_newer("1.2.0", "1.2.0"))
        self.assertTrue(desktop_releases.is_newer("1.2.0", "1.2.0-rc.1"))


if __name__ == "__main__":
    unittest.main()
