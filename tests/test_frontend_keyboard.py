"""Run the shipped JavaScript keyboard regressions using Node's built-in test runner."""

from pathlib import Path
import shutil
import subprocess
import unittest


NODE = shutil.which('node')
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FrontendKeyboardTests(unittest.TestCase):
    @unittest.skipUnless(NODE, 'Node.js is required for updater UI regressions')
    def test_desktop_updater(self):
        result = subprocess.run(
            [NODE, '--test', str(PROJECT_ROOT / 'tests/frontend/desktop_updater.test.cjs')],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, 'Node.js is required for management regressions')
    def test_management_preferences(self):
        result = subprocess.run(
            [NODE, '--test', str(PROJECT_ROOT / 'tests/frontend/management.test.cjs')],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding='utf-8', timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(NODE, 'Node.js is required for JavaScript keyboard regressions')
    def test_search_shortcuts(self):
        result = subprocess.run(
            [NODE, '--test', str(PROJECT_ROOT / 'tests/frontend/search_shortcut.test.cjs')],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
