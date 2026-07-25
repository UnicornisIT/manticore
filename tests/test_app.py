import os
import io
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from urllib.parse import unquote

import pandas as pd


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TEST_UPLOAD_DIR = tempfile.mkdtemp(prefix='manticore_tests_')

os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['ADMIN_DEFAULT_PASSWORD'] = 'test-admin-password'
os.environ['UPLOAD_FOLDER'] = TEST_UPLOAD_DIR
os.environ['DB_FILENAME'] = 'test.db'
os.environ['DEFAULT_CAMPAIGN_YEAR'] = '2026'
os.environ['LEGACY_CAMPAIGN_YEAR'] = '2025'
os.environ['APP_DEBUG'] = 'false'

sys.path.insert(0, PROJECT_ROOT)
import app as manticore


manticore.app.config['TESTING'] = True


def reset_database():
    with sqlite3.connect(manticore.DB_PATH) as conn:
        for table in (
            'abiturients',
            'pending_duplicates',
            'login_conflicts',
            'enrollment_candidates',
            'enrollment_orders',
            'enrollment_order_upload_rows',
            'enrollment_order_uploads',
            'students',
            'students_duplicates',
            'audit_logs',
            'login_attempts',
            'campaign_settings',
            'login_generation_settings',
        ):
            conn.execute(f'DELETE FROM {table}')


class ManticoreAppTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

    def setUp(self):
        reset_database()
        manticore.save_login_generation_settings(
            manticore.get_default_login_generation_rules(),
            setup_completed=True,
            updated_by='test'
        )

    def make_abiturients_file(self, rows, filename='abiturients.xlsx'):
        file_path = os.path.join(TEST_UPLOAD_DIR, filename)
        pd.DataFrame(rows).to_excel(file_path, index=False)
        return file_path

    def login_session(self, client, username='admin', role='admin'):
        with client.session_transaction() as session:
            session['user'] = username
            session['role'] = role

    def csrf_from_response(self, response):
        token_match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(token_match)
        return token_match.group(1)

    def test_custom_login_generation_rules_are_used_for_import(self):
        rules = manticore.get_default_login_generation_rules()
        rules.update({
            'mode': 'custom',
            'template': 'u{yy}{spec}{base}{seq}',
            'number_width': 4,
            'error_prefix': 'bad',
            'duplicate_prefix': 'copy',
            'spec_codes': {'ИТ': 'it'},
            'base_codes': {'очно': 'o'},
            'base_match_mode': 'last_part',
        })
        manticore.save_login_generation_settings(rules, setup_completed=True, updated_by='test')

        file_path = self.make_abiturients_file([
            {'ФИО': 'Тестов Тест Тестович', 'Договор': '2026-ИТ-0001-очно'},
            {'ФИО': 'Ошибкин Олег Олегович', 'Договор': 'без номера'},
        ], filename='custom_rules.xlsx')

        plan_df, summary = manticore.build_abiturients_import_plan(file_path, '2026')

        self.assertEqual(summary['ready_count'], 1)
        self.assertEqual(summary['conflict_count'], 1)
        self.assertEqual(plan_df['login'].tolist(), ['u26ito0001', 'bad0001'])

    def test_lowercase_i_base_aliases_are_hidden_but_still_accepted(self):
        rules = manticore.get_default_login_generation_rules()
        rules['base_codes'] = {
            '11и': '11i',
            '11И': '11i',
            '9и': '9i',
            '9И': '9i',
            '11': '11',
            '9': '9',
        }
        manticore.save_login_generation_settings(rules, setup_completed=True, updated_by='test')

        normalized_rules = manticore.get_login_generation_rules()
        self.assertNotIn('11и', normalized_rules['base_codes'])
        self.assertNotIn('9и', normalized_rules['base_codes'])
        self.assertIn('11И', normalized_rules['base_codes'])
        self.assertIn('9И', normalized_rules['base_codes'])
        self.assertEqual(
            manticore.parse_dogovor('2026-СД-0001-11и', normalized_rules),
            manticore.parse_dogovor('2026-СД-0001-11И', normalized_rules),
        )
        self.assertEqual(
            manticore.parse_dogovor('2026-ЛД-0002-9и', normalized_rules),
            manticore.parse_dogovor('2026-ЛД-0002-9И', normalized_rules),
        )

        client = manticore.app.test_client()
        self.login_session(client)
        for path in ('/abiturients', '/manual_create'):
            response = client.get(path)
            body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('value="11и"', body)
            self.assertNotIn('value="9и"', body)
            self.assertIn('value="11И"', body)
            self.assertIn('value="9И"', body)

        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('Старый Тест Запись', '2026-СД-0003-11и', 'old001', '2026', 'Старый', 'Тест Запись')
            )
        filtered_rows = manticore.get_all_abiturients(base='11И', campaign_year='2026')
        self.assertTrue(any(row['login'] == 'old001' for row in filtered_rows))

        file_path = self.make_abiturients_file([
            {'ФИО': 'Новый Один Тестович', 'Договор': '2026-СД-0001-11и'},
            {'ФИО': 'Новый Два Тестович', 'Договор': '2026-ЛД-0002-9и'},
        ], filename='lowercase_i.xlsx')

        plan_df, summary = manticore.build_abiturients_import_plan(file_path, '2026')

        self.assertEqual(summary['ready_count'], 2)
        self.assertEqual(plan_df['Договор'].tolist(), ['2026-СД-0001-11И', '2026-ЛД-0002-9И'])

        result_path, summary = manticore.apply_abiturients_import(file_path, '2026')
        self.assertEqual(summary['ready_count'], 2)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            stored_dogovors = {
                row[0]
                for row in conn.execute("SELECT dogovor FROM abiturients WHERE login IN ('26311i001', '2619i001')")
            }
        self.assertEqual(stored_dogovors, {'2026-СД-0001-11И', '2026-ЛД-0002-9И'})
        os.remove(result_path)

    def test_admin_is_redirected_to_setup_until_login_rules_are_saved(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute('DELETE FROM login_generation_settings')

        client = manticore.app.test_client()
        self.login_session(client)

        response = client.get('/file_work')
        self.assertEqual(response.status_code, 303)
        self.assertIn('/setup', response.headers['Location'])

        setup_response = client.get('/setup')
        csrf_token = self.csrf_from_response(setup_response)
        save_response = client.post('/setup', data={'csrf_token': csrf_token, 'mode': 'standard'})

        self.assertEqual(save_response.status_code, 302)
        self.assertTrue(manticore.is_login_generation_setup_completed())

    def test_abiturients_import_plan_uses_dogovor_and_warns_about_namesakes(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('Иванов Иван Иванович', '2026-ФМ-0001-11', '26611001', '2026', 'Иванов', 'Иван Иванович')
            )
            conn.execute(
                '''
                INSERT INTO students
                    (username, password, email, firstname, lastname, cohort1, source_campaign_year, source_dogovor, source_fio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'student001', 'cron', 'student@example.test', 'Семен', 'Сидоров', '26ФМ-11-1',
                    '2026', '2026-ФМ-0002-11', 'Сидоров Семен Семенович'
                )
            )
            conn.execute(
                '''
                INSERT INTO pending_duplicates (fio, dogovor, login, campaign_year, fam, imotch)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('Дубров Павел Павлович', '2026-ФМ-0003-11', 'dubl001', '2026', 'Дубров', 'Павел Павлович')
            )

        file_path = self.make_abiturients_file([
            {'ФИО': 'Петров Петр Петрович', 'Договор': '2026-ФМ-0004-11'},
            {'ФИО': 'Иванов Иван Иванович', 'Договор': '2026-ФМ-0005-11'},
            {'ФИО': 'Андреев Андрей Андреевич', 'Договор': '2026-ФМ-0001-11'},
            {'ФИО': 'Семенов Семен Семенович', 'Договор': '2026-ФМ-0002-11'},
            {'ФИО': 'Дубров Павел Павлович', 'Договор': '2026-ФМ-0003-11'},
            {'ФИО': 'Повторов Петр Петрович', 'Договор': '2026-ФМ-0004-11'},
        ])

        plan_df, summary = manticore.build_abiturients_import_plan(file_path, '2026')

        self.assertEqual(summary['total'], 6)
        self.assertEqual(summary['ready_count'], 2)
        self.assertEqual(summary['duplicate_count'], 1)
        self.assertEqual(summary['conflict_count'], 3)
        self.assertEqual(summary['warning_count'], 1)
        self.assertEqual(
            plan_df['import_action'].tolist(),
            ['create', 'create', 'conflict', 'conflict', 'duplicate', 'conflict']
        )
        self.assertIn('Возможный тёзка', plan_df.iloc[1]['import_status'])
        self.assertIn('Договор уже есть у абитуриента', plan_df.iloc[2]['import_status'])
        self.assertIn('Договор уже есть у студента', plan_df.iloc[3]['import_status'])
        self.assertIn('Договор уже ожидает проверки', plan_df.iloc[4]['import_status'])
        self.assertIn('Договор повторяется', plan_df.iloc[5]['import_status'])

    def test_apply_abiturients_import_creates_backup_and_audit_log(self):
        file_path = self.make_abiturients_file([
            {'ФИО': 'Петров Петр Петрович', 'Договор': '2026 ФМ 11'},
            {'ФИО': 'Сидоров Семен Семенович', 'Договор': 'ошибка'},
        ])

        result_path, summary = manticore.apply_abiturients_import(file_path, '2026')

        self.assertTrue(os.path.exists(result_path))
        self.assertEqual(summary['ready_count'], 1)
        self.assertEqual(summary['conflict_count'], 1)
        backups = manticore.list_database_backups()
        self.assertTrue(any('before_abiturients_import' in backup['name'] for backup in backups))

        with sqlite3.connect(manticore.DB_PATH) as conn:
            abiturients_count = conn.execute('SELECT COUNT(*) FROM abiturients').fetchone()[0]
            conflicts_count = conn.execute('SELECT COUNT(*) FROM login_conflicts').fetchone()[0]
            audit_count = conn.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='abiturients_import'"
            ).fetchone()[0]

        self.assertEqual(abiturients_count, 1)
        self.assertEqual(conflicts_count, 1)
        self.assertEqual(audit_count, 1)
        os.remove(result_path)

    def test_students_list_hides_password_for_non_admin(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO students (username, password, email, firstname, lastname, cohort1)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('student001', 'visible-secret', 'student@example.test', 'Петр', 'Петров', '26ФМ-11-1')
            )

        client = manticore.app.test_client()
        with client.session_transaction() as session:
            session['user'] = 'assistant'
            session['role'] = 'assistant'

        response = client.get('/students_list')
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('visible-secret', body)
        self.assertIn('••••••', body)

    def test_abiturients_sort_links_keep_current_filters(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Петров Петр Петрович', '2026-ФМ-0201-11', '26611201', '2026', 'Петров', 'Петр Петрович', 'petrov@example.test', 1)
            )
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Иванов Иван Иванович', '2026-ФМ-0202-11', '26611202', '2026', 'Иванов', 'Иван Иванович', '', 1)
            )

        client = manticore.app.test_client()
        self.login_session(client)
        response = client.get('/abiturients?has_email=1&has_paid=1&order_by=fam&order_dir=asc')
        body = unquote(response.get_data(as_text=True).replace('&amp;', '&'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('Петров Петр Петрович', body)
        self.assertNotIn('Иванов Иван Иванович', body)
        self.assertRegex(body, r'href="/abiturients\?[^"]*has_email=1[^"]*has_paid=1[^"]*order_by=fio')

    def test_students_sort_links_keep_current_filters(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO students (username, password, email, firstname, lastname, cohort1)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('student_p', 'secret', 'p@example.test', 'Петр', 'Петров', '26ФМ-11-1')
            )
            conn.execute(
                '''
                INSERT INTO students (username, password, email, firstname, lastname, cohort1)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('student_i', 'secret', 'i@example.test', 'Иван', 'Иванов', '26СД-9-1')
            )

        client = manticore.app.test_client()
        self.login_session(client)
        response = client.get('/students_list?cohort=26ФМ-11-1&lastname=П&order_by=username&order_dir=asc')
        body = unquote(response.get_data(as_text=True).replace('&amp;', '&'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('Петров', body)
        self.assertNotIn('Иванов', body)
        self.assertRegex(body, r'href="/students_list\?[^"]*cohort=26ФМ-11-1[^"]*lastname=П[^"]*order_by=lastname')

    def test_admin_backup_and_audit_pages_render(self):
        backup_path = manticore.create_database_backup('page_render_test')
        self.assertTrue(os.path.exists(backup_path))

        client = manticore.app.test_client()
        with client.session_transaction() as session:
            session['user'] = 'admin'
            session['role'] = 'admin'

        backups_response = client.get('/backups')
        audit_response = client.get('/audit_logs')

        self.assertEqual(backups_response.status_code, 200)
        self.assertEqual(audit_response.status_code, 200)
        self.assertIn('page_render_test', backups_response.get_data(as_text=True))

    def test_file_work_sections_render_individual_pages(self):
        client = manticore.app.test_client()
        self.login_session(client)

        common_response = client.get('/file_work')
        common_body = common_response.get_data(as_text=True)
        orders_response = client.get('/file_work/orders')
        orders_body = orders_response.get_data(as_text=True)
        invalid_response = client.get('/file_work/unknown')

        self.assertEqual(common_response.status_code, 200)
        self.assertIn('id="file-section-abiturients"', common_body)
        self.assertIn('id="file-section-updates"', common_body)
        self.assertIn('id="file-section-orders"', common_body)
        self.assertIn('id="file-section-students"', common_body)
        self.assertIn('/file_work/orders', common_body)

        self.assertEqual(orders_response.status_code, 200)
        self.assertIn('id="file-section-orders"', orders_body)
        self.assertNotIn('id="file-section-abiturients"', orders_body)
        self.assertNotIn('id="file-section-updates"', orders_body)
        self.assertNotIn('id="file-section-students"', orders_body)

        self.assertEqual(invalid_response.status_code, 303)
        self.assertIn('/file_work', invalid_response.headers['Location'])

    def test_file_work_preview_renders_without_writing_to_database(self):
        client = manticore.app.test_client()
        self.login_session(client)

        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)

        csv_bytes = 'ФИО,Договор\nПетров Петр Петрович,2026 ФМ 11\n'.encode('utf-8-sig')
        response = client.post(
            '/file_work',
            data={
                'csrf_token': csrf_token,
                'import_action': 'preview',
                'file': (io.BytesIO(csv_bytes), 'abiturients.csv'),
            },
            content_type='multipart/form-data'
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Предпросмотр импорта абитуриентов', body)
        self.assertIn('Подтвердить импорт', body)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            abiturients_count = conn.execute('SELECT COUNT(*) FROM abiturients').fetchone()[0]
        self.assertEqual(abiturients_count, 0)

    def test_file_work_preview_shows_friendly_row_report(self):
        client = manticore.app.test_client()
        self.login_session(client)

        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)

        csv_bytes = (
            'ФИО,Договор\n'
            ',2026 ФМ 11\n'
            'Иванов Иван Иванович,не договор\n'
        ).encode('utf-8-sig')
        response = client.post(
            '/file_work',
            data={
                'csrf_token': csrf_token,
                'import_action': 'preview',
                'file': (io.BytesIO(csv_bytes), 'abiturients.csv'),
            },
            content_type='multipart/form-data'
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Отчет по проверке абитуриентов', body)
        self.assertIn('Строка 2', body)
        self.assertIn('Не заполнено ФИО', body)
        self.assertIn('Строка 3', body)
        self.assertIn('Не удалось разобрать номер договора', body)

    def test_file_work_preview_shows_all_rows(self):
        client = manticore.app.test_client()
        self.login_session(client)

        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)

        rows = ['ФИО,Договор']
        for index in range(1, 26):
            rows.append(f'Фамилия{index} Имя{index} Отчество{index},2026-ФМ-{index:04d}-11')
        csv_bytes = ('\n'.join(rows) + '\n').encode('utf-8-sig')
        response = client.post(
            '/file_work',
            data={
                'csrf_token': csrf_token,
                'import_action': 'preview',
                'file': (io.BytesIO(csv_bytes), 'abiturients.csv'),
            },
            content_type='multipart/form-data'
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('В таблице показаны все строки файла', body)
        self.assertNotIn('Показаны первые 20 строк', body)
        self.assertIn('Фамилия25 Имя25 Отчество25', body)

    def test_dashboard_search_and_person_card_render(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Петров Петр Петрович', '2026-ФМ-0100-11', '26611010', '2026', 'Петров', 'Петр Петрович', 'p@example.test', 1)
            )
            conn.execute(
                'INSERT OR IGNORE INTO groups (name, group_year) VALUES (?, ?)',
                ('99ZZZ-DASHBOARD-END-1', '2026')
            )
            abiturient_id = conn.execute('SELECT id FROM abiturients WHERE login=?', ('26611010',)).fetchone()[0]

        client = manticore.app.test_client()
        self.login_session(client)

        dashboard_response = client.get('/')
        search_response = client.get('/search?q=0100')
        overlay_response = client.get('/search_overlay?q=0100')
        card_response = client.get(f'/person/abiturient/{abiturient_id}')
        wizard_response = client.get('/migration_wizard')

        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_body = dashboard_response.get_data(as_text=True)
        self.assertIn('Панель состояния', dashboard_body)
        self.assertNotIn('dashboard-search', dashboard_body)
        self.assertNotIn('quick-actions', dashboard_body)
        self.assertIn('aria-label="Поиск"', dashboard_body)
        self.assertNotIn('>Поиск</a>', dashboard_body)
        self.assertIn('nav-search', dashboard_body)
        self.assertIn('global-search-modal', dashboard_body)
        self.assertIn('data-search-overlay-url="/search_overlay"', dashboard_body)
        self.assertIn('fetch(searchUrl.toString()', dashboard_body)
        self.assertIn('Мастер миграции', dashboard_body)
        self.assertIn('Контингент', dashboard_body)
        self.assertIn('Операции', dashboard_body)
        self.assertIn('Работа с файлами', dashboard_body)
        self.assertIn('Проверка данных', dashboard_body)
        self.assertIn('Ручное создание логина', dashboard_body)
        self.assertIn('nav-dropdown', dashboard_body)
        self.assertIn("otherDropdown.open = false", dashboard_body)
        self.assertNotIn('nav-menu', dashboard_body)
        self.assertNotIn('Что требует внимания', dashboard_body)
        self.assertIn('Центр задач', dashboard_body)
        self.assertIn('Готовы к кандидатам', dashboard_body)
        self.assertIn('Кандидатов к зачислению', dashboard_body)
        self.assertIn('Сверены приказом', dashboard_body)
        self.assertIn('Полная проверка данных', dashboard_body)
        self.assertNotIn('<span>Без почты</span>', dashboard_body)
        self.assertNotIn('<span>Не оплачены</span>', dashboard_body)
        self.assertNotIn('<span>Конфликтов</span>', dashboard_body)
        self.assertNotIn('<span>В дублях</span>', dashboard_body)
        self.assertIn('dashboard-groups-scroll', dashboard_body)
        self.assertIn('99ZZZ-DASHBOARD-END-1', dashboard_body)
        self.assertEqual(search_response.status_code, 200)
        self.assertIn('Петров Петр Петрович', search_response.get_data(as_text=True))
        self.assertEqual(overlay_response.status_code, 200)
        overlay_data = overlay_response.get_json()
        self.assertEqual(overlay_data['query'], '0100')
        self.assertTrue(any(item['title'] == 'Петров Петр Петрович' for item in overlay_data['results']))
        self.assertEqual(card_response.status_code, 200)
        card_body = card_response.get_data(as_text=True)
        self.assertIn('Карточка абитуриента', card_body)
        self.assertIn('Номер договора', card_body)
        self.assertIn('Логин Moodle', card_body)
        self.assertIn('Договор оплачен', card_body)
        self.assertIn('2026-ФМ-0100-11', card_body)
        self.assertNotIn('>fio<', card_body)
        self.assertNotIn('>dogovor<', card_body)
        self.assertNotIn('>paid<', card_body)
        self.assertEqual(wizard_response.status_code, 200)
        wizard_body = wizard_response.get_data(as_text=True)
        self.assertIn('Мастер миграции', wizard_body)
        self.assertIn('1. Подготовить кандидатов', wizard_body)
        self.assertIn('2. Загрузить приказ', wizard_body)
        self.assertIn('3. Перенести в студенты', wizard_body)
        self.assertIn('Академические группы', wizard_body)
        self.assertIn('Дублирующие записи студентов', wizard_body)
        self.assertIn('Дублирующие записи абитуриентов', wizard_body)
        self.assertNotIn('Что проверить перед миграцией', wizard_body)

    def test_data_checks_page_groups_actionable_issues(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Иванов Иван Иванович', '2026-ФМ-0200-11', '26611020', '2026', 'Иванов', 'Иван Иванович', '', 0)
            )
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Петров Петр Петрович', '', '26611021', '2026', 'Петров', 'Петр Петрович', 'bad-email', 1)
            )
            conn.execute(
                '''
                INSERT INTO students (username, password, email, firstname, lastname, cohort1, source_campaign_year, source_dogovor)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                ('student020', 'secret', 'wrong-mail', 'Анна', 'Кривец', '', '2026', '')
            )
            conn.execute(
                '''
                INSERT INTO pending_duplicates (fio, dogovor, login, campaign_year, fam, imotch)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('Дубль Дмитрий', '2026-СД-0201-11', '26611022', '2026', 'Дубль', 'Дмитрий')
            )
            conn.execute(
                '''
                INSERT INTO login_conflicts (fio, dogovor, login, campaign_year, fam, imotch)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('Конфликт Константин', '2026-СД-0202-11', 'error000', '2026', 'Конфликт', 'Константин')
            )

        client = manticore.app.test_client()
        self.login_session(client)

        dashboard_response = client.get('/')
        checks_response = client.get('/data_checks')

        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_body = dashboard_response.get_data(as_text=True)
        self.assertIn('Центр задач', dashboard_body)
        self.assertIn('Абитуриенты без почты', dashboard_body)
        self.assertIn('Не оплачены', dashboard_body)
        self.assertIn('Проверка данных', dashboard_body)
        self.assertEqual(checks_response.status_code, 200)
        checks_body = checks_response.get_data(as_text=True)
        self.assertIn('Проверка данных', checks_body)
        self.assertIn('Некорректная почта', checks_body)
        self.assertIn('Без договора', checks_body)
        self.assertIn('Без академической группы', checks_body)
        self.assertIn('Без договора при поступлении', checks_body)
        self.assertIn('Дублирующие записи абитуриентов', checks_body)
        self.assertIn('Конфликты логинов', checks_body)
        self.assertIn('Иванов Иван Иванович', checks_body)
        self.assertIn('Кривец Анна', checks_body)

    def test_abiturients_updates_import_updates_email_and_paid(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Петров Петр Петрович', '2026-ФМ-0101-11', '26611011', '2026', 'Петров', 'Петр Петрович', 0)
            )

        updates_path = os.path.join(TEST_UPLOAD_DIR, 'updates.xlsx')
        pd.DataFrame([
            {'Договор': '2026-ФМ-0101-11', 'Email': 'new@example.test', 'Оплата': 'да'}
        ]).to_excel(updates_path, index=False)

        summary = manticore.process_abiturients_updates(updates_path, '2026')

        self.assertEqual(summary['updated_email'], 1)
        self.assertEqual(summary['updated_paid'], 1)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            row = conn.execute('SELECT email, paid FROM abiturients WHERE login=?', ('26611011',)).fetchone()
        self.assertEqual(row, ('new@example.test', 1))

    def test_abiturients_updates_template_download(self):
        client = manticore.app.test_client()
        self.login_session(client)

        page_response = client.get('/file_work')
        template_response = client.get('/abiturients_updates_template/download')

        self.assertEqual(page_response.status_code, 200)
        self.assertIn('Скачать шаблон обновлений', page_response.get_data(as_text=True))
        self.assertEqual(template_response.status_code, 200)
        self.assertEqual(
            template_response.mimetype,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        workbook = pd.ExcelFile(io.BytesIO(template_response.data))
        self.assertEqual(workbook.sheet_names, ['Шаблон', 'Подсказка', 'Пример'])
        template_df = pd.read_excel(workbook, sheet_name='Шаблон')
        help_df = pd.read_excel(workbook, sheet_name='Подсказка')
        self.assertEqual(list(template_df.columns), ['Договор', 'Email', 'Оплата'])
        self.assertIn('Договор', help_df['Поле'].tolist())

    def test_abiturients_updates_upload_reports_row_errors(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Смирнова Светлана Сергеевна', '2026-ФМ-0300-11', '26611030', '2026', 'Смирнова', 'Светлана Сергеевна', 0)
            )

        client = manticore.app.test_client()
        self.login_session(client)
        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)
        upload = io.BytesIO()
        pd.DataFrame([
            {'Договор': '2026-ФМ-0300-11', 'Email': 'wrong-mail', 'Оплата': 'оплаченно'},
            {'Договор': '2026-ФМ-9999-11', 'Email': 'ok@example.test', 'Оплата': 'да'},
            {'Договор': '', 'Email': 'empty@example.test', 'Оплата': 'да'},
        ]).to_excel(upload, index=False)
        upload.seek(0)

        response = client.post(
            '/abiturients_updates_upload',
            data={
                'csrf_token': csrf_token,
                'updates_file': (upload, 'updates.xlsx'),
            },
            content_type='multipart/form-data',
            follow_redirects=True
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Отчет по файлу обновлений', body)
        self.assertIn('Строка 2', body)
        self.assertIn('Почта выглядит некорректно', body)
        self.assertIn('Не удалось распознать значение оплаты', body)
        self.assertIn('Строка 3', body)
        self.assertIn('Договор не найден', body)
        self.assertIn('Строка 4', body)
        self.assertIn('Не указан номер договора', body)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            row = conn.execute('SELECT email, paid FROM abiturients WHERE login=?', ('26611030',)).fetchone()
        self.assertEqual(row, (None, 0))

    def test_two_stage_enrollment_requires_order_before_student_migration(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'Иванов Иван Иванович', '2026-ФМ-0500-11', '26611050',
                    '2026', 'Иванов', 'Иван Иванович', 'ivanov@example.test', 1
                )
            )
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'Петров Петр Петрович', '2026-ФМ-0501-11', '26611051',
                    '2026', 'Петров', 'Петр Петрович', '', 1
                )
            )
            conn.execute(
                'INSERT OR IGNORE INTO groups (name, group_year) VALUES (?, ?)',
                ('26ФМ-11-1', '2026')
            )

        sync_summary = manticore.sync_enrollment_candidates_from_ready_abiturients('2026')
        self.assertEqual(sync_summary['created'], 1)
        self.assertEqual(len(sync_summary['skipped_without_email']), 1)

        with sqlite3.connect(manticore.DB_PATH) as conn:
            candidate = conn.execute(
                '''
                SELECT id, specialty, verification_status
                FROM enrollment_candidates
                WHERE login=?
                ''',
                ('26611050',)
            ).fetchone()
        self.assertIsNotNone(candidate)
        candidate_id, specialty, verification_status = candidate
        self.assertEqual(specialty, '33.02.01 «Фармация»')
        self.assertEqual(verification_status, 'waiting_order')

        client = manticore.app.test_client()
        self.login_session(client)
        get_response = client.get('/abiturients_to_students')
        csrf_token = self.csrf_from_response(get_response)
        blocked_response = client.post(
            '/abiturients_to_students',
            data={
                'csrf_token': csrf_token,
                'group_year': '2026',
                'cohort1': '26ФМ-11-1',
                'candidate_ids': [str(candidate_id)],
            },
            follow_redirects=True
        )
        self.assertEqual(blocked_response.status_code, 200)
        self.assertIn('Не перенесены без совпадения с приказом', blocked_response.get_data(as_text=True))
        with sqlite3.connect(manticore.DB_PATH) as conn:
            student_count = conn.execute('SELECT COUNT(*) FROM students WHERE username=?', ('26611050',)).fetchone()[0]
        self.assertEqual(student_count, 0)

        order_path = os.path.join(TEST_UPLOAD_DIR, 'enrollment_order.xlsx')
        pd.DataFrame([
            {
                'ФИО': 'Иванов Иван Иванович',
                'Специальность': '33.02.01 «Фармация»',
                'Группа': '26ФМ-11-1',
                'Номер приказа': '123-у',
                'Дата приказа': '2026-08-15',
            }
        ]).to_excel(order_path, index=False)
        order_summary = manticore.apply_enrollment_order_import(order_path, '2026')
        self.assertEqual(order_summary['matched_count'], 1)

        get_response = client.get('/abiturients_to_students?specialty=фм')
        body = get_response.get_data(as_text=True)
        self.assertIn('Сверен с приказом', body)
        self.assertIn('26ФМ-11-1', body)
        csrf_token = self.csrf_from_response(get_response)
        migrated_response = client.post(
            '/abiturients_to_students',
            data={
                'csrf_token': csrf_token,
                'group_year': '2026',
                'specialty': 'фм',
                'cohort1': '26ФМ-11-1',
                'candidate_ids': [str(candidate_id)],
            },
            follow_redirects=True
        )
        self.assertEqual(migrated_response.status_code, 200)
        self.assertIn('Иванов', migrated_response.get_data(as_text=True))
        with sqlite3.connect(manticore.DB_PATH) as conn:
            student = conn.execute(
                '''
                SELECT username, email, firstname, lastname, cohort1, source_dogovor, source_fio
                FROM students
                WHERE username=?
                ''',
                ('26611050',)
            ).fetchone()
            abiturient_count = conn.execute('SELECT COUNT(*) FROM abiturients WHERE login=?', ('26611050',)).fetchone()[0]
            candidate_count = conn.execute('SELECT COUNT(*) FROM enrollment_candidates WHERE login=?', ('26611050',)).fetchone()[0]

        self.assertEqual(
            student,
            (
                '26611050', 'ivanov@example.test', 'Иван Иванович', 'Иванов',
                '26ФМ-11-1', '2026-ФМ-0500-11', 'Иванов Иван Иванович'
            )
        )
        self.assertEqual(abiturient_count, 0)
        self.assertEqual(candidate_count, 0)

    def test_docx_enrollment_order_paragraph_blocks_use_official_specialty_names(self):
        from docx import Document

        order_path = os.path.join(TEST_UPLOAD_DIR, 'paragraph_order.docx')
        document = Document()
        table = document.add_table(rows=2, cols=3)
        table.cell(0, 0).text = '19 сентября 2025 г.'
        table.cell(0, 2).text = '№ 1909-У'
        table.cell(1, 1).text = 'Ставрополь'
        document.add_paragraph('Приложение №1')
        document.add_paragraph('к приказу от 19.09.2025 № 1909-У')
        document.add_paragraph('СПО 33.02.01 «Фармация» очная форма обучения (Приложение № 4).')
        document.add_paragraph('Иванов Иван Иванович')
        document.add_paragraph('СПО 31.02.07 «Стоматологическое дело» очная форма')
        document.add_paragraph('Петров Петр Петрович')
        document.save(order_path)

        df = manticore.read_docx_enrollment_order_dataframe(order_path)
        rows = df.to_dict(orient='records')

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['ФИО'], 'Иванов Иван Иванович')
        self.assertEqual(rows[0]['Специальность'], '33.02.01 «Фармация»')
        self.assertEqual(rows[0]['Номер приказа'], '1909-У')
        self.assertEqual(rows[0]['Дата приказа'], '19.09.2025')
        self.assertEqual(rows[1]['ФИО'], 'Петров Петр Петрович')
        self.assertEqual(rows[1]['Специальность'], '31.02.07 «Стоматологическое дело»')
        self.assertEqual(
            manticore.normalize_specialty_key('31.02.07 «Стоматологическое дело»'),
            manticore.normalize_specialty_key('СтД')
        )

    def test_enrollment_order_preview_can_fix_candidate_fio_from_order(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'Иванов Иван Иванвич', '2026-ФМ-0550-11', '26611055',
                    '2026', 'Иванов', 'Иван Иванвич', 'ivanov@example.test', 1
                )
            )

        manticore.sync_enrollment_candidates_from_ready_abiturients('2026')
        pending_order_path = manticore.make_temp_upload_path('xlsx', prefix=manticore.PENDING_ENROLLMENT_ORDER_IMPORT_PREFIX)
        pd.DataFrame([
            {
                'ФИО': 'Иванов Иван Иванович',
                'Специальность': '33.02.01 «Фармация»',
                'Номер приказа': '055-У',
                'Дата приказа': '2026-08-25',
            }
        ]).to_excel(pending_order_path, index=False)

        plan_df, summary = manticore.build_enrollment_order_import_plan(pending_order_path, '2026')
        row = plan_df.iloc[0]
        self.assertEqual(summary['matched_count'], 0)
        self.assertEqual(summary['fio_suggestion_count'], 1)
        self.assertEqual(row['suggested_candidate_fio'], 'Иванов Иван Иванвич')

        with sqlite3.connect(manticore.DB_PATH) as conn:
            candidate_id = conn.execute(
                'SELECT id FROM enrollment_candidates WHERE login=?',
                ('26611055',)
            ).fetchone()[0]

        client = manticore.app.test_client()
        self.login_session(client)
        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)
        fix_response = client.post(
            '/enrollment_order_preview/fix_candidate_fio',
            data={
                'csrf_token': csrf_token,
                'pending_enrollment_order_import': os.path.basename(pending_order_path),
                'row_number': str(row['_row_number']),
                'candidate_id': str(candidate_id),
            }
        )
        fix_body = fix_response.get_data(as_text=True)

        self.assertEqual(fix_response.status_code, 200)
        self.assertIn('Иванов Иван Иванович', fix_body)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            candidate = conn.execute(
                'SELECT fio, fam, imotch FROM enrollment_candidates WHERE id=?',
                (candidate_id,)
            ).fetchone()
            abiturient = conn.execute(
                'SELECT fio, fam, imotch FROM abiturients WHERE login=?',
                ('26611055',)
            ).fetchone()
        self.assertEqual(candidate, ('Иванов Иван Иванович', 'Иванов', 'Иван Иванович'))
        self.assertEqual(abiturient, ('Иванов Иван Иванович', 'Иванов', 'Иван Иванович'))

        refreshed_df, refreshed_summary = manticore.build_enrollment_order_import_plan(pending_order_path, '2026')
        self.assertEqual(refreshed_summary['matched_count'], 1)
        self.assertTrue(bool(refreshed_df.iloc[0]['has_candidate']))

    def test_enrollment_order_preview_flags_candidate_when_source_abiturient_fio_differs(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'Братыпкина Алина Алексеевна', '2026-СтД-0046-11И', '26811003',
                    '2026', 'Братыпкина', 'Алина Алексеевна', 'bratypkina@example.test', 1
                )
            )

        manticore.sync_enrollment_candidates_from_ready_abiturients('2026')
        with sqlite3.connect(manticore.DB_PATH) as conn:
            candidate_id = conn.execute(
                'SELECT id FROM enrollment_candidates WHERE login=?',
                ('26811003',)
            ).fetchone()[0]
            conn.execute(
                '''
                UPDATE enrollment_candidates
                SET fio=?, fam=?, imotch=?
                WHERE id=?
                ''',
                ('Братыкина Алина Алексеевна', 'Братыкина', 'Алина Алексеевна', candidate_id)
            )

        order_path = os.path.join(TEST_UPLOAD_DIR, 'source_mismatch_order.xlsx')
        pd.DataFrame([
            {
                'ФИО': 'Братыкина Алина Алексеевна',
                'Специальность': '31.02.07 «Стоматологическое дело»',
                'Номер приказа': '3009-У',
                'Дата приказа': '30.09.2025',
            }
        ]).to_excel(order_path, index=False)

        plan_df, summary = manticore.build_enrollment_order_import_plan(order_path, '2026')
        row = plan_df.iloc[0]

        self.assertEqual(summary['matched_count'], 0)
        self.assertEqual(summary['import_count'], 0)
        self.assertEqual(summary['fio_suggestion_count'], 1)
        self.assertEqual(row['import_action'], 'fio_review')
        self.assertFalse(bool(row['has_candidate']))
        self.assertEqual(row['suggested_candidate_fio'], 'Братыпкина Алина Алексеевна')
        self.assertEqual(row['suggested_candidate_actual_fio'], 'Братыкина Алина Алексеевна')

        preview_row = manticore.enrollment_order_preview_rows(plan_df)[0]
        self.assertEqual(preview_row['action_label'], 'Проверить ФИО')
        self.assertEqual(preview_row['badge_class'], 'status-warning')

    def test_multiple_enrollment_orders_accumulate_without_clearing_previous_uploads(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'Иванов Иван Иванович', '2026-ФМ-0600-11', '26611060',
                    '2026', 'Иванов', 'Иван Иванович', 'ivanov@example.test', 1
                )
            )
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'Петров Петр Петрович', '2026-СД-0601-9', '26390601',
                    '2026', 'Петров', 'Петр Петрович', 'petrov@example.test', 1
                )
            )

        manticore.sync_enrollment_candidates_from_ready_abiturients('2026')
        first_order_path = os.path.join(TEST_UPLOAD_DIR, 'first_enrollment_order.xlsx')
        second_order_path = os.path.join(TEST_UPLOAD_DIR, 'second_enrollment_order.xlsx')
        pd.DataFrame([
            {
                'ФИО': 'Иванов Иван Иванович',
                'Специальность': '33.02.01 «Фармация»',
                'Номер приказа': '001-У',
                'Дата приказа': '2026-08-20',
            }
        ]).to_excel(first_order_path, index=False)
        pd.DataFrame([
            {
                'ФИО': 'Петров Петр Петрович',
                'Специальность': '34.02.01 «Сестринское дело»',
                'Номер приказа': '002-У',
                'Дата приказа': '2026-08-30',
            }
        ]).to_excel(second_order_path, index=False)

        first_summary = manticore.apply_enrollment_order_import(first_order_path, '2026')
        second_summary = manticore.apply_enrollment_order_import(second_order_path, '2026')

        self.assertEqual(first_summary['matched_count'], 1)
        self.assertEqual(second_summary['matched_count'], 1)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            order_count = conn.execute('SELECT COUNT(*) FROM enrollment_orders WHERE campaign_year=?', ('2026',)).fetchone()[0]
            statuses = conn.execute(
                '''
                SELECT fio, verification_status
                FROM enrollment_candidates
                WHERE campaign_year=?
                ORDER BY fio
                ''',
                ('2026',)
            ).fetchall()

        self.assertEqual(order_count, 2)
        self.assertEqual(statuses, [
            ('Иванов Иван Иванович', 'verified'),
            ('Петров Петр Петрович', 'verified'),
        ])
        uploads = manticore.get_enrollment_order_uploads('2026')
        self.assertEqual(len(uploads), 2)
        self.assertEqual(uploads[0]['original_filename'], 'second_enrollment_order.xlsx')
        self.assertEqual(uploads[1]['original_filename'], 'first_enrollment_order.xlsx')

        first_upload = manticore.get_enrollment_order_upload(first_summary['upload_id'])
        first_upload_path = manticore.get_stored_enrollment_order_path(first_upload['stored_filename'])
        self.assertTrue(os.path.exists(first_upload_path))

        client = manticore.app.test_client()
        self.login_session(client)
        detail_response = client.get(f"/enrollment_order_uploads/{first_summary['upload_id']}")
        detail_body = detail_response.get_data(as_text=True)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn('first_enrollment_order.xlsx', detail_body)
        self.assertIn('Иванов Иван Иванович', detail_body)

        download_response = client.get(f"/enrollment_order_uploads/{first_summary['upload_id']}/download")
        self.assertEqual(download_response.status_code, 200)
        self.assertIn('attachment', download_response.headers.get('Content-Disposition', ''))
        download_response.close()

        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)
        delete_response = client.post(
            f"/enrollment_order_uploads/{first_summary['upload_id']}/delete",
            data={'csrf_token': csrf_token},
            follow_redirects=True
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(os.path.exists(first_upload_path))
        with sqlite3.connect(manticore.DB_PATH) as conn:
            order_count_after_delete = conn.execute(
                'SELECT COUNT(*) FROM enrollment_orders WHERE campaign_year=?',
                ('2026',)
            ).fetchone()[0]
            statuses_after_delete = conn.execute(
                '''
                SELECT fio, verification_status
                FROM enrollment_candidates
                WHERE campaign_year=?
                ORDER BY fio
                ''',
                ('2026',)
            ).fetchall()
        self.assertEqual(order_count_after_delete, 1)
        self.assertEqual(statuses_after_delete, [
            ('Иванов Иван Иванович', 'missing_in_order'),
            ('Петров Петр Петрович', 'verified'),
        ])

    def test_students_upload_reports_row_errors(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO students (username, password, email, firstname, lastname, cohort1)
                VALUES (?, ?, ?, ?, ?, ?)
                ''',
                ('student_existing', 'secret', 'old@example.test', 'Старый', 'Студент', '26ФМ-11-1')
            )

        client = manticore.app.test_client()
        self.login_session(client)
        get_response = client.get('/file_work')
        csrf_token = self.csrf_from_response(get_response)
        upload = io.BytesIO()
        pd.DataFrame([
            {
                'username': '',
                'password': 'pass',
                'email': 'missing-login@example.test',
                'firstname': 'Иван',
                'lastname': 'Иванов',
                'cohort1': '26ФМ-11-1',
            },
            {
                'username': 'student_bad_mail',
                'password': 'pass',
                'email': 'bad-mail',
                'firstname': 'Петр',
                'lastname': 'Петров',
                'cohort1': '26ФМ-11-1',
            },
            {
                'username': 'student_existing',
                'password': 'pass',
                'email': 'duplicate@example.test',
                'firstname': 'Дубль',
                'lastname': 'Студент',
                'cohort1': '26ФМ-11-1',
            },
            {
                'username': 'student_new',
                'password': 'pass',
                'email': 'new@example.test',
                'firstname': 'Новый',
                'lastname': 'Студент',
                'cohort1': '26ФМ-11-1',
            },
        ]).to_excel(upload, index=False)
        upload.seek(0)

        response = client.post(
            '/students_upload',
            data={
                'csrf_token': csrf_token,
                'file': (upload, 'students.xlsx'),
            },
            content_type='multipart/form-data'
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Предпросмотр загрузки студентов', body)
        self.assertIn('Отчет по проверке студентов', body)
        self.assertIn('Строка 2', body)
        self.assertIn('Не заполнено: Логин', body)
        self.assertIn('Строка 3', body)
        self.assertIn('Почта выглядит некорректно', body)
        self.assertIn('Строка 4', body)
        self.assertIn('будет перенесена в дубли студентов', body)
        self.assertIn('К добавлению: 1', body)
        self.assertIn('Подтвердить загрузку студентов', body)
        self.assertIn('status-badge status-success', body)
        self.assertIn('status-badge status-warning', body)

        with sqlite3.connect(manticore.DB_PATH) as conn:
            new_count = conn.execute('SELECT COUNT(*) FROM students WHERE username=?', ('student_new',)).fetchone()[0]
            duplicate_count = conn.execute(
                'SELECT COUNT(*) FROM students_duplicates WHERE username=?',
                ('student_existing',)
            ).fetchone()[0]
        self.assertEqual(new_count, 0)
        self.assertEqual(duplicate_count, 0)

        pending_match = re.search(r'name="pending_students_import" value="([^"]+)"', body)
        self.assertIsNotNone(pending_match)
        confirm_csrf = self.csrf_from_response(response)
        confirm_response = client.post(
            '/students_upload',
            data={
                'csrf_token': confirm_csrf,
                'students_import_action': 'confirm',
                'pending_students_import': pending_match.group(1),
            },
            follow_redirects=True
        )
        confirm_body = confirm_response.get_data(as_text=True)

        self.assertEqual(confirm_response.status_code, 200)
        self.assertIn('Загрузка студентов завершена', confirm_body)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            new_count = conn.execute('SELECT COUNT(*) FROM students WHERE username=?', ('student_new',)).fetchone()[0]
            bad_count = conn.execute('SELECT COUNT(*) FROM students WHERE username=?', ('student_bad_mail',)).fetchone()[0]
            duplicate_count = conn.execute(
                'SELECT COUNT(*) FROM students_duplicates WHERE username=?',
                ('student_existing',)
            ).fetchone()[0]
        self.assertEqual(new_count, 1)
        self.assertEqual(bad_count, 0)
        self.assertEqual(duplicate_count, 1)

        students_response = client.get('/students_list')
        students_body = students_response.get_data(as_text=True)
        self.assertIn('status-badge status-success', students_body)
        self.assertIn('Есть почта', students_body)

    def test_campaign_archive_page_toggles_status(self):
        client = manticore.app.test_client()
        self.login_session(client)

        get_response = client.get('/campaigns')
        csrf_token = self.csrf_from_response(get_response)
        post_response = client.post(
            '/campaigns',
            data={'csrf_token': csrf_token, 'campaign_year': '2026', 'is_archived': '1'},
            follow_redirects=True
        )

        self.assertEqual(post_response.status_code, 200)
        self.assertTrue(manticore.is_campaign_archived('2026'))

    def test_campaign_page_creates_new_campaign_for_switchers(self):
        client = manticore.app.test_client()
        self.login_session(client)

        get_response = client.get('/campaigns')
        csrf_token = self.csrf_from_response(get_response)
        post_response = client.post(
            '/campaigns',
            data={
                'csrf_token': csrf_token,
                'campaign_action': 'create',
                'new_campaign_year': '2031',
            },
            follow_redirects=True
        )
        page_body = post_response.get_data(as_text=True)

        self.assertEqual(post_response.status_code, 200)
        self.assertIn('Кампания 2031 создана', page_body)
        self.assertIn('<td>2031</td>', page_body)
        self.assertIn('2031', manticore.get_campaign_years())
        self.assertIn('2031', manticore.get_group_years(include_base=True))
        with client.session_transaction() as session:
            self.assertEqual(session.get('campaign_year'), '2031')

        abiturients_response = client.get('/abiturients')
        abiturients_body = abiturients_response.get_data(as_text=True)
        self.assertIn('<option value="2031" selected>2031</option>', abiturients_body)

    def test_bulk_abiturients_marks_selected_as_paid(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, paid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                ('Петров Петр Петрович', '2026-ФМ-0102-11', '26611012', '2026', 'Петров', 'Петр Петрович', 0)
            )
            abiturient_id = conn.execute('SELECT id FROM abiturients WHERE login=?', ('26611012',)).fetchone()[0]

        client = manticore.app.test_client()
        self.login_session(client)
        get_response = client.get('/abiturients')
        csrf_token = self.csrf_from_response(get_response)
        response = client.post(
            '/abiturients/bulk',
            data={
                'csrf_token': csrf_token,
                'bulk_action': 'mark_paid',
                'abiturient_ids': [str(abiturient_id)],
            },
            follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            paid = conn.execute('SELECT paid FROM abiturients WHERE id=?', (abiturient_id,)).fetchone()[0]
        self.assertEqual(paid, 1)

    def test_student_card_and_export_hide_password_for_non_admin(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO students (username, password, email, firstname, lastname, cohort1, source_campaign_year)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                ('student010', 'secret-pass', 'student010@example.test', 'Петр', 'Петров', '26ФМ-11-1', '2026')
            )

        client = manticore.app.test_client()
        self.login_session(client, username='viewer', role='viewer')
        card_response = client.get('/person/student/student010')
        export_response = client.get('/students/download')

        self.assertEqual(card_response.status_code, 200)
        self.assertNotIn('secret-pass', card_response.get_data(as_text=True))
        self.assertIn('Скрыт для безопасности', card_response.get_data(as_text=True))
        self.assertEqual(export_response.status_code, 200)
        exported = pd.read_excel(io.BytesIO(export_response.data))
        self.assertEqual(exported.loc[0, 'password'], '******')

    def test_delete_student_does_not_return_to_archived_campaign(self):
        with sqlite3.connect(manticore.DB_PATH) as conn:
            conn.execute(
                '''
                INSERT INTO students
                    (username, password, email, firstname, lastname, cohort1, source_campaign_year, source_dogovor, source_fio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'student011', 'cron', 'student011@example.test', 'Семен', 'Сидоров', '26ФМ-11-1',
                    '2026', '2026-ФМ-0111-11', 'Сидоров Семен Семенович'
                )
            )
            conn.execute(
                '''
                INSERT INTO campaign_settings (campaign_year, is_archived, archived_at, archived_by)
                VALUES (?, ?, datetime('now', 'localtime'), ?)
                ''',
                ('2026', 1, 'admin')
            )

        client = manticore.app.test_client()
        self.login_session(client)
        get_response = client.get('/students_list')
        csrf_token = self.csrf_from_response(get_response)
        response = client.post(
            '/delete_student',
            data={'csrf_token': csrf_token, 'username': 'student011'},
            follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        with sqlite3.connect(manticore.DB_PATH) as conn:
            student_count = conn.execute('SELECT COUNT(*) FROM students WHERE username=?', ('student011',)).fetchone()[0]
            abiturient_count = conn.execute('SELECT COUNT(*) FROM abiturients WHERE login=?', ('student011',)).fetchone()[0]
        self.assertEqual(student_count, 1)
        self.assertEqual(abiturient_count, 0)


if __name__ == '__main__':
    unittest.main()
