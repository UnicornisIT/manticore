import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DependencyMetadataTests(unittest.TestCase):
    def test_pyproject_defines_supported_python_and_required_extras(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = metadata["project"]

        self.assertEqual(project["requires-python"], ">=3.11,<3.12")
        self.assertEqual(set(project["optional-dependencies"]), {"prod", "desktop", "dev"})
        self.assertIn("gunicorn>=23.0,<24.0", project["optional-dependencies"]["prod"])
        self.assertIn("pywebview>=5.0,<7.0", project["optional-dependencies"]["desktop"])
        self.assertTrue(any(item.startswith("ruff") for item in project["optional-dependencies"]["dev"]))

    def test_production_lock_is_fully_pinned_and_used_by_linux_scripts(self):
        lock_lines = [
            line.strip()
            for line in (ROOT / "requirements-prod.lock").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertIn("gunicorn==23.0.0", lock_lines)
        self.assertTrue(all("==" in line for line in lock_lines))
        self.assertIn("requirements-prod.lock", (ROOT / "deploy.sh").read_text(encoding="utf-8"))
        self.assertIn(
            "requirements-prod.lock",
            (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
