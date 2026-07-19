import os
import io
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest

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
            'students',
            'students_duplicates',
            'audit_logs',
            'login_attempts',
            'campaign_settings',
        ):
            conn.execute(f'DELETE FROM {table}')


class ManticoreAppTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_UPLOAD_DIR, ignore_errors=True)

    def setUp(self):
        reset_database()

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
        self.assertIn('Критичных задач нет', dashboard_body)
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
        self.assertIn('Начать миграцию абитуриентов', wizard_body)
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
            content_type='multipart/form-data',
            follow_redirects=True
        )
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('Отчет по загрузке студентов', body)
        self.assertIn('Строка 2', body)
        self.assertIn('Не заполнено: Логин', body)
        self.assertIn('Строка 3', body)
        self.assertIn('Почта выглядит некорректно', body)
        self.assertIn('Строка 4', body)
        self.assertIn('перенесена в дубли студентов', body)
        self.assertIn('Добавлено студентов: 1', body)

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
