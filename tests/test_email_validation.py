import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import email_validation


class NoAnswer(Exception):
    pass


class NXDOMAIN(Exception):
    pass


class Timeout(Exception):
    pass


class FakeResolver:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def resolve(self, domain, record_type):
        self.calls.append((domain, record_type))
        answer = self.answers.get(record_type, NoAnswer())
        if isinstance(answer, Exception):
            raise answer
        return answer


class EmailValidationTests(unittest.TestCase):
    def test_confirmed_provider_and_legacy_domains(self):
        expected = {
            'gmail.com', 'googlemail.com', 'icloud.com', 'me.com', 'mac.com',
            'mail.ru', 'inbox.ru', 'list.ru', 'bk.ru', 'internet.ru', 'xmail.ru',
            'yandex.ru', 'ya.ru', 'yandex.com', 'yandex.by', 'yandex.kz', 'yandex.com.tr',
            'outlook.com', 'hotmail.com', 'live.com', 'msn.com',
            'proton.me', 'protonmail.com', 'protonmail.ch', 'pm.me',
            'tuta.com', 'tutanota.com', 'tutanota.de', 'tutamail.com', 'tuta.io', 'keemail.me',
            'yahoo.com', 'myyahoo.com', 'yahoo.co.uk', 'yahoo.fr',
            'rambler.ru', 'lenta.ru', 'autorambler.ru', 'myrambler.ru', 'ro.ru',
        }
        for domain in expected:
            with self.subTest(domain=domain):
                result = email_validation.validate_email(f'user@{domain}')
                self.assertTrue(result.valid)
                self.assertIn(result.code, {'known_provider_valid', 'known_provider_legacy_valid'})

    def test_known_mismatch_and_typos_have_safe_suggestions(self):
        for domain, suggestion in {
            'gmail.ru': 'gmail.com', 'gmal.com': 'gmail.com', 'gmial.com': 'gmail.com',
            'iclod.com': 'icloud.com', 'outlok.com': 'outlook.com', 'protonn.me': 'proton.me',
        }.items():
            with self.subTest(domain=domain):
                result = email_validation.validate_email(f'user@{domain}')
                self.assertEqual(result.severity, 'warning')
                self.assertEqual(result.suggested_domain, suggestion)
                self.assertEqual(result.confidence, 'high')

    def test_unrelated_corporate_domain_is_not_provider_typo(self):
        resolver = FakeResolver({'MX': ['mx.medical-company.ru.']})
        result = email_validation.validate_email('user@medical-company.ru', resolver=resolver)
        self.assertEqual(result.code, 'domain_valid_mx')
        self.assertFalse(result.suggested_domain)

        unknown_resolver = FakeResolver({'MX': ['mx.unknown-domain.example.']})
        unknown = email_validation.validate_email('user@unknown-domain.example', resolver=unknown_resolver)
        self.assertTrue(unknown.valid)
        self.assertEqual(unknown.code, 'domain_valid_mx')

    def test_dns_outcomes(self):
        scenarios = [
            ({'MX': ['mx.example.test.']}, 'domain_valid_mx', True),
            ({'MX': NoAnswer(), 'A': ['192.0.2.1']}, 'domain_valid_implicit_mx', True),
            ({'MX': NXDOMAIN()}, 'domain_not_found', True),
            ({'MX': NoAnswer(), 'A': NoAnswer(), 'AAAA': NoAnswer()}, 'domain_no_mail', True),
            ({'MX': Timeout()}, 'verification_unavailable', True),
        ]
        for answers, code, valid in scenarios:
            with self.subTest(code=code):
                result = email_validation.validate_email('user@corporate-example.test', resolver=FakeResolver(answers))
                self.assertEqual(result.code, code)
                self.assertEqual(result.valid, valid)
                if code in {'domain_not_found', 'domain_no_mail'}:
                    self.assertEqual(result.severity, 'warning')

    def test_dns_cache_avoids_second_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / 'cache.db')
            resolver = FakeResolver({'MX': ['mx.example.test.']})
            first = email_validation.validate_email('one@cache-example.test', database_path=database, resolver=resolver, now=1000)
            second = email_validation.validate_email('two@cache-example.test', database_path=database, resolver=resolver, now=1001)
            self.assertEqual(first.code, 'domain_valid_mx')
            self.assertEqual(second.code, 'domain_valid_mx')
            self.assertEqual(len(resolver.calls), 1)
            connection = sqlite3.connect(database)
            try:
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM email_domain_cache').fetchone()[0], 1)
            finally:
                connection.close()

    def test_registry_schema_domains_sources_and_typo_targets(self):
        registry = email_validation.load_provider_registry()
        email_validation.validate_provider_registry(registry)
        broken = {
            **registry,
            'providers': [dict(item) for item in registry['providers']],
        }
        broken['providers'][1]['canonical_domains'] = ['gmail.com']
        with self.assertRaises(ValueError):
            email_validation.validate_provider_registry(broken)

    def test_ten_thousand_repeated_domains_use_one_dns_lookup(self):
        resolver = FakeResolver({'MX': ['mx.bulk-example.test.']})
        started = time.monotonic()
        for index in range(10000):
            result = email_validation.validate_email(
                f'user{index}@bulk-example.test', resolver=resolver, now=2000
            )
            self.assertTrue(result.valid)
        self.assertEqual(len(resolver.calls), 1)
        self.assertLess(time.monotonic() - started, 5.0)


if __name__ == '__main__':
    unittest.main()
