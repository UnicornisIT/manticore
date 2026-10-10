import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScriptEntrypointTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_root_entrypoints_are_thin_compatibility_wrappers(self):
        expected_targets = {
            "start_one_click.bat": r"scripts\start_one_click.bat",
            "install_and_run_windows.ps1": r"scripts\install_and_run_windows.ps1",
            "update.sh": "scripts/update.sh",
            "update_windows.bat": r"scripts\update_windows.bat",
            "update_and_run_windows.bat": r"scripts\update_and_run_windows.bat",
            "update_windows.ps1": r"scripts\update_windows.ps1",
        }
        for wrapper, target in expected_targets.items():
            with self.subTest(wrapper=wrapper):
                body = self.read(wrapper)
                self.assertIn(target, body)
                self.assertLessEqual(len([line for line in body.splitlines() if line.strip()]), 8)

    def test_update_wrappers_delegate_to_update_app(self):
        shell_wrapper = self.read("scripts/update.sh")
        powershell_wrapper = self.read("scripts/update_windows.ps1")

        self.assertIn('"$APP_DIR/update_app.py"', shell_wrapper)
        self.assertIn('Join-Path $AppDir "update_app.py"', powershell_wrapper)
        self.assertNotIn("git pull", shell_wrapper)
        self.assertNotIn("git pull", powershell_wrapper)

    def test_deploy_uses_canonical_update_wrapper(self):
        self.assertIn("%APP_DIR%/scripts/update.sh", self.read("deploy.sh"))
        self.assertIn(
            "/srv/manticore/scripts/update.sh",
            self.read("deploy/manticore-update.service"),
        )


if __name__ == "__main__":
    unittest.main()
