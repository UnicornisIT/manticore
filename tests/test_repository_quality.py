import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryQualityTests(unittest.TestCase):
    def test_pyproject_configures_ruff_and_mypy_for_python_311(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(metadata["tool"]["ruff"]["target-version"], "py311")
        self.assertEqual(metadata["tool"]["mypy"]["python_version"], "3.11")

    def test_ci_runs_tests_lint_and_types(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("python -m unittest discover -s tests", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("run: mypy", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("windows-latest", workflow)

    def test_security_workflow_scans_full_history_for_secrets(self):
        workflow = (ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")

        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("sha256sum --check --strict", workflow)
        self.assertIn('LOG_OPTS="--all"', workflow)
        self.assertIn("gitleaks git . ", workflow)
        self.assertIn("--exit-code 1", workflow)
        self.assertIn('cron: "17 6 * * 1"', workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertNotIn("gitleaks", (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))

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
