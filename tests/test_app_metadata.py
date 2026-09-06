"""Regression coverage for the shared, user-visible application information."""

import gc
import importlib.util
import os
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LinkParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs))


class AppMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # app.py initializes its database at import time. A separate module and
        # database keep these tests independent of test_app.py's global fixture.
        directory = tempfile.TemporaryDirectory(prefix='manticore_metadata_')
        cls.addClassCleanup(directory.cleanup)
        module_name = '_manticore_metadata_test_app'
        spec = importlib.util.spec_from_file_location(module_name, PROJECT_ROOT / 'app.py')
        cls.manticore = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = cls.manticore
        cls.addClassCleanup(sys.modules.pop, module_name, None)
        cls.addClassCleanup(cls.close_database_connections)
        with mock.patch.dict(os.environ, {
            'SECRET_KEY': 'metadata-test-secret',
            'ADMIN_DEFAULT_PASSWORD': 'metadata-test-admin-password',
            'UPLOAD_FOLDER': directory.name,
            'DB_FILENAME': 'metadata.db',
            'DEFAULT_CAMPAIGN_YEAR': '2026',
            'LEGACY_CAMPAIGN_YEAR': '2025',
            'APP_DEBUG': 'false',
        }):
            spec.loader.exec_module(cls.manticore)
        cls.manticore.app.config['TESTING'] = True
        cls.manticore.save_login_generation_settings(
            cls.manticore.get_default_login_generation_rules(),
            setup_completed=True,
            updated_by='metadata-test',
        )

    @classmethod
    def close_database_connections(cls):
        # sqlite's connection context manager commits without closing. The
        # module-level admin initialization retains its connection on Windows.
        connection = getattr(cls.manticore, 'conn', None)
        if connection is not None:
            connection.close()
        gc.collect()

    def get_page(self, path, role=None):
        client = self.manticore.app.test_client()
        if role:
            with client.session_transaction() as session:
                session['user'] = 'admin'
                session['role'] = role
        response = client.get(path, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def section(self, body, tag, element_id):
        match = re.search(
            rf'<{tag}\b[^>]*\bid="{re.escape(element_id)}"[^>]*>.*?</{tag}>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(match, f'Missing {tag}#{element_id}')
        return match.group(0)

    def assert_contact_links(self, body, metadata):
        links = LinkParser(body).links
        contact_urls = {
            'github_url': metadata['github_url'],
            'telegram_url': metadata['telegram_url'],
            'developer_email': 'mailto:' + metadata['developer_email'],
        }
        for key, url in contact_urls.items():
            with self.subTest(contact=key):
                matches = [link for link in links if link.get('href') == url]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].get('target'), '_blank')
                self.assertTrue({'noopener', 'noreferrer'} <= set(matches[0].get('rel', '').split()))

    def test_about_is_available_to_every_authenticated_role(self):
        for role in self.manticore.ROLE_LABELS:
            with self.subTest(role=role):
                body = self.get_page('/desktop_settings', role=role)
                about = self.section(body, 'section', 'about')
                self.assertIn('О программе', about)
                self.assertIn(self.manticore.APP_METADATA['name'], about)
                self.assertIn(self.manticore.APP_METADATA['developer_name'], about)
                self.assertIn(self.manticore.APP_METADATA['developer_email'], about)
                self.assertIn(self.manticore.APP_METADATA['github_url'].removeprefix('https://'), about)
                self.assertIn('@' + self.manticore.APP_METADATA['telegram_url'].rsplit('/', 1)[-1], about)
                self.assert_contact_links(about, self.manticore.APP_METADATA)
                self.assertNotRegex(about.split('>', 1)[0], r'\b(?:hidden|data-desktop-only)\b')
                header = re.search(r'<header class="page-header">(.*?)</header>', body, re.S).group(1)
                self.assertNotIn('href="#about"', header)
                self.assertNotIn('О программе', header)

    def test_about_remains_available_before_initial_setup_is_completed(self):
        with mock.patch.object(self.manticore, 'is_login_generation_setup_completed', return_value=False):
            for role in self.manticore.ROLE_LABELS:
                with self.subTest(role=role):
                    about = self.section(self.get_page('/desktop_settings', role=role), 'section', 'about')
                    self.assert_contact_links(about, self.manticore.APP_METADATA)

    def test_settings_still_requires_authentication(self):
        response = self.manticore.app.test_client().get('/desktop_settings')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/login')

    def test_login_renders_one_compact_footer_with_shared_contact_links(self):
        body = self.get_page('/login')
        self.assertEqual(body.count('id="main-footer"'), 1)
        footer = self.section(body, 'footer', 'main-footer')
        self.assertIn(self.manticore.APP_METADATA['name'], footer)
        self.assertIn(self.manticore.APP_METADATA['developer_name'], footer)
        self.assert_contact_links(footer, self.manticore.APP_METADATA)
        self.assertNotRegex(footer.split('>', 1)[0], r'\bhidden\b')

    def test_running_app_version_matches_release_source(self):
        expected = (PROJECT_ROOT / 'VERSION').read_text(encoding='utf-8-sig').strip()
        self.assertEqual(self.manticore.APP_VERSION, expected)

    def test_about_sidebar_and_login_footer_render_runtime_version(self):
        version = '99.8.7-metadata-regression'
        with mock.patch.object(self.manticore, 'APP_VERSION', version):
            settings = self.get_page('/desktop_settings', role='viewer')
            about = self.section(settings, 'section', 'about')
            footer = self.section(self.get_page('/login'), 'footer', 'main-footer')
        self.assertIn(version, about)
        self.assertIn(version, footer)
        self.assertRegex(settings, rf'class="sidebar-meta"[^>]*>.*?{re.escape(version)}')

    def test_all_contact_surfaces_follow_the_same_metadata(self):
        metadata = dict(self.manticore.APP_METADATA, **{
            'developer_name': 'Metadata regression developer',
            'developer_email': 'metadata-regression@example.test',
            'github_url': 'https://github.com/metadata-regression',
            'telegram_url': 'https://t.me/metadata_regression',
        })
        with mock.patch.object(self.manticore, 'APP_METADATA', metadata):
            about = self.section(self.get_page('/desktop_settings', role='admin'), 'section', 'about')
            footer = self.section(self.get_page('/login'), 'footer', 'main-footer')
            rules = self.get_page('/setup', role='admin')
        for content in (about, footer):
            self.assertIn(metadata['developer_name'], content)
            self.assert_contact_links(content, metadata)
        self.assertTrue(
            any(link.get('href') == 'mailto:' + metadata['developer_email'] for link in LinkParser(rules).links),
            'Login rules must use the shared developer email',
        )
        self.assertIn('>' + metadata['developer_email'] + '</a>', rules)
        self.assertNotIn(self.manticore.APP_METADATA['developer_email'], rules)

    def test_templates_do_not_duplicate_central_contact_values(self):
        contact_keys = ('developer_name', 'developer_email', 'github_url', 'telegram_url')
        templates = [
            *PROJECT_ROOT.joinpath('templates').rglob('*.html'),
            *PROJECT_ROOT.joinpath('desktop', 'ui').rglob('*.html'),
        ]
        for path in templates:
            source = path.read_text(encoding='utf-8-sig')
            for key in contact_keys:
                with self.subTest(template=str(path.relative_to(PROJECT_ROOT)), metadata=key):
                    self.assertFalse(
                        self.manticore.APP_METADATA[key] in source,
                        f'{path.relative_to(PROJECT_ROOT)} duplicates {key}',
                    )

    def test_readme_contacts_are_generated_from_runtime_metadata(self):
        import sync_app_metadata

        source = (PROJECT_ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertEqual(source.count(sync_app_metadata.START), 1)
        self.assertEqual(source.count(sync_app_metadata.END), 1)
        self.assertIn(sync_app_metadata.render_contacts(), source)
        self.assertTrue(sync_app_metadata.sync_readme(check=True))


if __name__ == '__main__':
    unittest.main()
