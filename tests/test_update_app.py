import tempfile
import unittest
from pathlib import Path

import update_app


class UpdateAppTests(unittest.TestCase):
    def test_update_stops_when_git_metadata_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaises(update_app.UpdateError) as raised:
                update_app.update_git_repository(Path(tmp_dir))

        message = str(raised.exception)
        self.assertIn("Program files were not updated", message)
        self.assertIn("not a Git repository", message)


if __name__ == "__main__":
    unittest.main()
