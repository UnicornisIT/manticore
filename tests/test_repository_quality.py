import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryQualityTests(unittest.TestCase):
    def test_pyproject_configures_ruff_and_mypy_for_python_311(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["tool"]["ruff"]["target-version"], "py311")
        self.assertEqual(metadata["tool"]["mypy"]["python_version"], "3.11")

    def test_ci_runs_tests_lint_types_and_secret_scan(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("run: mypy", workflow)
        self.assertIn("gitleaks/gitleaks-action", workflow)

    def test_changelog_codeowners_and_repository_metadata_exist(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

        self.assertNotIn("## Что добавили и улучшили в последних версиях", readme)
        self.assertIn("Предпросмотр импорта абитуриентов", changelog)
        self.assertIn("@UnicornisIT", (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8"))
        self.assertIn(
            "manticore-admissions", (ROOT / "docs" / "REPO_METADATA.md").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
