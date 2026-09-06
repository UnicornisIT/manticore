"""Browser request/confirmation protocol regressions using the shipped script."""
from pathlib import Path
import shutil
import subprocess
import unittest


class StudentTransferFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required')
    def test_transfer_confirmation_protocol(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which('node'), '--test', str(root / 'tests/frontend/student_transfer.test.cjs')],
            capture_output=True, text=True, encoding='utf-8', timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
