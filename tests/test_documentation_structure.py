import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationStructureTests(unittest.TestCase):
    def test_readme_is_short_and_links_to_split_guides(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(readme.splitlines()), 180)
        self.assertIn("Статус: alpha", readme)

        for relative_path in (
            "docs/installation.md",
            "docs/file-formats.md",
            "docs/roles.md",
            "docs/deployment.md",
            "docs/security.md",
            "docs/desktop-updates.md",
            "docs/updates.md",
            "docs/project-structure.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
        ):
            self.assertIn(relative_path, readme)
            self.assertTrue((ROOT / relative_path).is_file(), relative_path)

    def test_project_structure_lists_current_entrypoints(self):
        structure = (ROOT / "docs" / "project-structure.md").read_text(encoding="utf-8")
        for entry in (
            "data/email_providers.json",
            "contingent.py",
            "contingent_web.py",
            "db_safety.py",
            "email_validation.py",
            "desktop_releases.py",
            "app_metadata.py",
            "sync_app_metadata.py",
            "VERSION",
            "RELEASE_CHECKLIST.md",
            "routes/",
            "services/",
            "db/",
        ):
            self.assertIn(entry, structure)

    def test_security_and_contribution_policies_are_actionable(self):
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

        self.assertIn("dev-eva@outlook.com", security)
        self.assertIn("Не создавайте публичный issue", security)
        for command in (
            "python.exe -m unittest discover -s tests -v",
            "ruff.exe check .",
            "mypy.exe",
            "sync_app_metadata.py --check",
            "pre-commit.exe run --all-files",
        ):
            self.assertIn(command, contributing)


if __name__ == "__main__":
    unittest.main()
