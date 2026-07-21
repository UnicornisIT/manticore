import os
import re
import csv
import copy
import json
import secrets
import tempfile
import shutil
import pandas as pd
from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash, has_request_context, jsonify
from urllib.parse import urlparse
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3
import io
import hmac
from string import Formatter
from functools import wraps
from datetime import date, datetime
from time import time
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

# Load environment variables from .env file
load_dotenv()

TRUTHY_ENV_VALUES = {'1', 'true', 'yes', 'on'}

def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in TRUTHY_ENV_VALUES

def env_int(name, default, minimum=None):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32).hex()
MAX_UPLOAD_SIZE_MB = env_int('MAX_UPLOAD_SIZE_MB', 16, minimum=1)
MAX_UPLOAD_BYTES = env_int('MAX_CONTENT_LENGTH', MAX_UPLOAD_SIZE_MB * 1024 * 1024, minimum=1024)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax'),
    SESSION_COOKIE_SECURE=env_bool('SESSION_COOKIE_SECURE', False),
)
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_FILENAME = os.environ.get('DB_FILENAME', 'baze.db')
DB_PATH = os.path.join(app.config['UPLOAD_FOLDER'], DB_FILENAME)
APP_VERSION = os.environ.get('APP_VERSION', '1.0.8')
ENABLE_HSTS = env_bool('ENABLE_HSTS', False)
HSTS_MAX_AGE = env_int('HSTS_MAX_AGE', 31536000, minimum=0)
HSTS_INCLUDE_SUBDOMAINS = env_bool('HSTS_INCLUDE_SUBDOMAINS', False)
HSTS_PRELOAD = env_bool('HSTS_PRELOAD', False)
ABITURIENT_UPLOAD_EXTENSIONS = {'xlsx', 'xls', 'csv'}
STUDENTS_UPLOAD_EXTENSIONS = {'xlsx', 'xls', 'csv'}
GROUPS_UPLOAD_EXTENSIONS = {'csv'}
PENDING_ABITURIENTS_IMPORT_PREFIX = 'pending_abiturients_'
PENDING_STUDENTS_IMPORT_PREFIX = 'pending_students_'
DB_BACKUP_PREFIX = 'baze_backup_'
ABITURIENT_REQUIRED_COLUMNS = {'ФИО', 'Договор'}
ABITURIENT_RESULT_COLUMNS = [
    'campaign_year', 'ФИО', 'Договор', 'login', 'Фамилия',
    'Имя_Отчество', 'import_action', 'import_status'
]
STUDENT_PREVIEW_COLUMNS = [
    '_row_number', 'username', 'email', 'firstname', 'lastname', 'cohort1',
    'source_dogovor', 'source_campaign_year', 'import_action', 'import_status'
]
UPLOAD_REPORT_LIMIT = 40
STUDENT_UPLOAD_REQUIRED_COLUMNS = ['username', 'password', 'email', 'firstname', 'lastname', 'cohort1']
STUDENT_UPLOAD_FIELD_LABELS = {
    'username': 'Логин',
    'password': 'Пароль',
    'email': 'Email',
    'firstname': 'Имя',
    'lastname': 'Фамилия',
    'cohort1': 'Академическая группа',
}
ROLE_LABELS = {
    'admin': 'Администратор',
    'manager': 'Куратор',
    'operator': 'Оператор',
    'assistant': 'Ассистент',
    'viewer': 'Только просмотр',
}
ARCHIVED_CAMPAIGN_MESSAGE = 'Кампания архивирована. Изменения в ней недоступны.'

class UploadValidationError(ValueError):
    pass

def format_upload_size(size_bytes):
    size_mb = size_bytes / (1024 * 1024)
    if size_mb >= 1:
        return f'{size_mb:.0f} МБ'
    return f'{max(1, size_bytes // 1024)} КБ'

def allowed_extensions_text(allowed_extensions):
    return ', '.join(f'.{extension}' for extension in sorted(allowed_extensions))

def get_upload_extension(file_storage):
    filename = file_storage.filename if file_storage else ''
    return os.path.splitext(filename or '')[1].lstrip('.').lower()

def validate_uploaded_file(file_storage, allowed_extensions):
    if not file_storage or not file_storage.filename:
        raise UploadValidationError('Выберите файл для загрузки.')

    extension = get_upload_extension(file_storage)
    if extension not in allowed_extensions:
        raise UploadValidationError(
            f'Неверный тип файла. Разрешены: {allowed_extensions_text(allowed_extensions)}.'
        )
    return extension

def make_temp_upload_path(extension, prefix='upload_'):
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    fd, path = tempfile.mkstemp(
        prefix=prefix,
        suffix=f'.{extension}',
        dir=app.config['UPLOAD_FOLDER']
    )
    os.close(fd)
    return path

def save_upload_to_temp(file_storage, allowed_extensions, prefix='upload_'):
    extension = validate_uploaded_file(file_storage, allowed_extensions)
    temp_path = make_temp_upload_path(extension, prefix=prefix)
    file_storage.save(temp_path)
    return temp_path

def cleanup_temp_files(*paths):
    for path in paths:
        if not path:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError:
            app.logger.warning('Could not remove temporary file: %s', path)

def send_temp_download(file_path, download_name, mimetype):
    with open(file_path, 'rb') as file_obj:
        output = io.BytesIO(file_obj.read())
    output.seek(0)
    cleanup_temp_files(file_path)
    return send_file(output, as_attachment=True, download_name=download_name, mimetype=mimetype)

def read_csv_dataframe(file_path):
    last_error = None
    for encoding in ('utf-8-sig', 'cp1251', 'utf-8'):
        try:
            return pd.read_csv(file_path, sep=None, engine='python', encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise ValueError('Не удалось прочитать CSV в кодировке UTF-8 или Windows-1251')
    raise ValueError('Не удалось прочитать CSV-файл')

def read_tabular_upload(file_path):
    extension = os.path.splitext(file_path)[1].lower()
    if extension == '.csv':
        return read_csv_dataframe(file_path)
    if extension == '.xls':
        return pd.read_excel(file_path)
    return pd.read_excel(file_path, engine="openpyxl")

_campaign_year_re = re.compile(r'^20\d{2}$')
_dogovor_year_re = re.compile(r'20\d{2}')
_email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

def is_valid_email(value):
    value = str(value or '').strip()
    return not value or bool(_email_re.fullmatch(value))

def clean_campaign_year(value, fallback):
    value = str(value or '').strip()
    if _campaign_year_re.fullmatch(value):
        return value
    return fallback

DEFAULT_CAMPAIGN_YEAR = clean_campaign_year(os.environ.get('DEFAULT_CAMPAIGN_YEAR'), str(date.today().year))
LEGACY_CAMPAIGN_YEAR = clean_campaign_year(os.environ.get('LEGACY_CAMPAIGN_YEAR'), '2025')
BASE_CAMPAIGN_YEARS = [str(y) for y in range(2020, 2031)]
MAX_GROUP_STUDENTS = 25
GROUPS_TEMPLATE_EXAMPLES = (
    ('ФМ', '11'),
    ('СД', '9'),
    ('ЛД', '11'),
)

_group_name_re = re.compile(r'^\d{2}[A-Za-zА-Яа-яЁё]+-(?:\d{1,2}(?:[A-Za-zА-Яа-яЁё])?|[A-Za-zА-Яа-яЁё]+)-\d+$')
_group_head_re = re.compile(r'^(\d{2})([A-Za-zА-Яа-яЁё]+)$')
_group_year_code_re = re.compile(r'^\s*(\d{2})')
_specialty_aliases = {
    'ФМ': 'ФМ',
    'СД': 'СД',
    'СТО': 'СтО',
    'СТП': 'СтП',
    'СТД': 'СтД',
    'СТПР': 'СтПр',
    'АД': 'АД',
    'ЛД': 'ЛД',
}

def normalize_campaign_year(value, fallback=None):
    return clean_campaign_year(value, fallback or DEFAULT_CAMPAIGN_YEAR)

def normalize_group_year(value, fallback=None):
    fallback = fallback or DEFAULT_CAMPAIGN_YEAR
    value = str(value or '').strip()
    if re.fullmatch(r'\d{2}', value):
        value = f'20{value}'
    return normalize_campaign_year(value, fallback)

def infer_campaign_year(dogovor, fallback=LEGACY_CAMPAIGN_YEAR):
    match = _dogovor_year_re.search(str(dogovor or ''))
    if match:
        return normalize_campaign_year(match.group(0), fallback)
    return fallback

def infer_group_year(group_name, fallback=None):
    fallback = normalize_group_year(fallback, DEFAULT_CAMPAIGN_YEAR)
    match = _group_year_code_re.match(str(group_name or ''))
    if match:
        return normalize_group_year(match.group(1), fallback)
    return fallback

spec_codes = {
    "ЛД": "1", "АД": "2", "СД": "3", "СтО": "4",
    "СтПр": "5", "СтП": "5", "ФМ": "6", "ЛабД": "7", "СтД": "8"
}

base_codes = {
    "2НМ": "inm", "2М": "im",
    "НМ": "nm", "М": "im",
    "11И": "11i", "9И": "9i",
    "11": "11", "9": "9", 
}

LOGIN_RULES_SETTINGS_ID = 1
LOGIN_TEMPLATE_FIELDS = {'yyyy', 'yy', 'spec', 'base', 'seq', 'num'}
LOGIN_MATCH_MODES = {'last_part', 'anywhere'}

DEFAULT_LOGIN_GENERATION_RULES = {
    'mode': 'standard',
    'template': '{yy}{spec}{base}{seq}',
    'number_width': 3,
    'error_prefix': 'error',
    'duplicate_prefix': 'dubl',
    'unique_scope': 'campaign',
    'year_regex': r'20\d{2}',
    'base_match_mode': 'last_part',
    'spec_codes': spec_codes,
    'base_codes': base_codes,
}

def get_default_login_generation_rules():
    return copy.deepcopy(DEFAULT_LOGIN_GENERATION_RULES)

def canonicalize_base_label(label):
    label = str(label or '').strip()
    folded = label.casefold()
    if folded == '11и':
        return '11И'
    if folded == '9и':
        return '9И'
    return label

def normalize_base_codes_mapping(mapping):
    normalized = {}
    for label, code in (mapping or {}).items():
        label = canonicalize_base_label(label)
        if label:
            normalized[label] = str(code).strip()
    return normalized

def merge_login_generation_rules(raw_rules=None):
    rules = get_default_login_generation_rules()
    if isinstance(raw_rules, dict):
        for key, value in raw_rules.items():
            if key == 'base_codes' and isinstance(value, dict):
                rules[key] = normalize_base_codes_mapping(value)
            elif key == 'spec_codes' and isinstance(value, dict):
                rules[key] = {str(k).strip(): str(v).strip() for k, v in value.items() if str(k).strip()}
            elif key in rules:
                rules[key] = value
    rules['base_codes'] = normalize_base_codes_mapping(rules.get('base_codes'))
    rules['mode'] = 'custom' if rules.get('mode') == 'custom' else 'standard'
    rules['template'] = str(rules.get('template') or DEFAULT_LOGIN_GENERATION_RULES['template']).strip()
    try:
        rules['number_width'] = max(1, min(8, int(rules.get('number_width', 3))))
    except (TypeError, ValueError):
        rules['number_width'] = 3
    rules['error_prefix'] = str(rules.get('error_prefix') or 'error').strip() or 'error'
    rules['duplicate_prefix'] = str(rules.get('duplicate_prefix') or 'dubl').strip() or 'dubl'
    rules['unique_scope'] = 'global' if rules.get('unique_scope') == 'global' else 'campaign'
    rules['year_regex'] = str(rules.get('year_regex') or r'20\d{2}').strip() or r'20\d{2}'
    rules['base_match_mode'] = rules.get('base_match_mode') if rules.get('base_match_mode') in LOGIN_MATCH_MODES else 'last_part'
    if not rules.get('spec_codes'):
        rules['spec_codes'] = copy.deepcopy(spec_codes)
    if not rules.get('base_codes'):
        rules['base_codes'] = copy.deepcopy(base_codes)
    return rules

def create_login_generation_settings_table(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS login_generation_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            settings_json TEXT NOT NULL,
            setup_completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_by TEXT
        )
    ''')

def get_login_generation_settings_row():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            create_login_generation_settings_table(conn)
            return conn.execute(
                '''
                SELECT settings_json, setup_completed, updated_at, updated_by
                FROM login_generation_settings
                WHERE id=?
                ''',
                (LOGIN_RULES_SETTINGS_ID,)
            ).fetchone()
    except sqlite3.Error:
        return None

def get_login_generation_rules():
    row = get_login_generation_settings_row()
    if not row:
        return get_default_login_generation_rules()
    try:
        stored_rules = json.loads(row[0] or '{}')
    except (TypeError, ValueError):
        stored_rules = {}
    return merge_login_generation_rules(stored_rules)

def is_login_generation_setup_completed():
    row = get_login_generation_settings_row()
    return bool(row and row[1])

def save_login_generation_settings(rules, setup_completed=True, updated_by=''):
    normalized_rules = merge_login_generation_rules(rules)
    validate_login_generation_rules(normalized_rules)
    with sqlite3.connect(DB_PATH) as conn:
        create_login_generation_settings_table(conn)
        conn.execute(
            '''
            INSERT INTO login_generation_settings (id, settings_json, setup_completed, updated_at, updated_by)
            VALUES (?, ?, ?, datetime('now', 'localtime'), ?)
            ON CONFLICT(id) DO UPDATE SET
                settings_json=excluded.settings_json,
                setup_completed=excluded.setup_completed,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
            ''',
            (
                LOGIN_RULES_SETTINGS_ID,
                json.dumps(normalized_rules, ensure_ascii=False, sort_keys=True),
                1 if setup_completed else 0,
                updated_by,
            )
        )
        conn.commit()
    return normalized_rules

def mapping_to_text(mapping):
    return '\n'.join(f'{key} = {value}' for key, value in (mapping or {}).items())

def parse_mapping_text(text, field_label):
    mapping = {}
    for line_number, raw_line in enumerate(str(text or '').splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        separator = '=' if '=' in line else ':' if ':' in line else ''
        if not separator:
            raise ValueError(f'{field_label}: строка {line_number} должна быть в формате "значение = код".')
        key, value = [part.strip() for part in line.split(separator, 1)]
        if not key or not value:
            raise ValueError(f'{field_label}: строка {line_number} содержит пустое значение или код.')
        if re.search(r'\s', value):
            raise ValueError(f'{field_label}: код в строке {line_number} не должен содержать пробелы.')
        mapping[key] = value
    if not mapping:
        raise ValueError(f'{field_label}: добавьте хотя бы одну строку.')
    return mapping

def validate_login_template(template):
    template = str(template or '').strip()
    if not template:
        raise ValueError('Шаблон логина не может быть пустым.')
    formatter = Formatter()
    try:
        parsed_parts = list(formatter.parse(template))
    except ValueError as exc:
        raise ValueError(f'Шаблон логина заполнен некорректно: {exc}') from exc
    used_fields = set()
    for _literal, field_name, format_spec, conversion in parsed_parts:
        if field_name is None:
            continue
        if conversion:
            raise ValueError('Шаблон логина не должен использовать преобразования вида !r или !s.')
        if field_name not in LOGIN_TEMPLATE_FIELDS:
            allowed = ', '.join(sorted(f'{{{field}}}' for field in LOGIN_TEMPLATE_FIELDS))
            raise ValueError(f'Неизвестное поле {{{field_name}}}. Доступны: {allowed}.')
        used_fields.add(field_name)
        if format_spec:
            for _spec_literal, nested_field, _nested_spec, _nested_conversion in formatter.parse(format_spec):
                if nested_field:
                    raise ValueError('Шаблон логина не должен содержать вложенные поля форматирования.')
    if 'seq' not in used_fields and 'num' not in used_fields:
        raise ValueError('Шаблон логина должен содержать {seq} или {num}.')
    return template

def validate_login_generation_rules(rules):
    rules = merge_login_generation_rules(rules)
    validate_login_template(rules['template'])
    re.compile(rules['year_regex'])
    if not rules['spec_codes']:
        raise ValueError('Добавьте хотя бы один код специальности.')
    if not rules['base_codes']:
        raise ValueError('Добавьте хотя бы один код базы.')
    for prefix_label, prefix in (('Префикс ошибок', rules['error_prefix']), ('Префикс дублей', rules['duplicate_prefix'])):
        if re.search(r'\s', prefix):
            raise ValueError(f'{prefix_label} не должен содержать пробелы.')
    render_login_template(
        rules,
        {'year': '2026', 'year_code': '26', 'spec_code': '1', 'base_code': '11'},
        1
    )
    return True

def build_login_rules_from_form(form, mode='custom'):
    if mode == 'standard':
        return get_default_login_generation_rules()
    return merge_login_generation_rules({
        'mode': 'custom',
        'template': form.get('template'),
        'number_width': form.get('number_width'),
        'error_prefix': form.get('error_prefix'),
        'duplicate_prefix': form.get('duplicate_prefix'),
        'unique_scope': form.get('unique_scope'),
        'year_regex': form.get('year_regex'),
        'base_match_mode': form.get('base_match_mode'),
        'spec_codes': parse_mapping_text(form.get('spec_codes_text'), 'Коды специальностей'),
        'base_codes': parse_mapping_text(form.get('base_codes_text'), 'Коды базы'),
    })

def render_login_template(rules, parts, number):
    rules = merge_login_generation_rules(rules)
    width = rules['number_width']
    number = int(number)
    context = {
        'yyyy': parts['year'],
        'yy': parts['year_code'],
        'spec': parts['spec_code'],
        'base': parts['base_code'],
        'seq': f'{number:0{width}d}',
        'num': number,
    }
    return rules['template'].format(**context)

def get_login_rules_form_context(rules=None):
    rules = merge_login_generation_rules(rules or get_login_generation_rules())
    sample_spec = next(iter(rules.get('spec_codes') or {'ФМ': '6'}))
    sample_base = next(iter(rules.get('base_codes') or {'11': '11'}))
    sample_dogovor = f'2026-{sample_spec}-0001-{sample_base}'
    sample_parts = parse_dogovor_parts(sample_dogovor, rules)
    sample_login = render_login_template(rules, sample_parts, 1) if sample_parts else ''
    return {
        'rules': rules,
        'setup_completed': is_login_generation_setup_completed(),
        'spec_codes_text': mapping_to_text(rules.get('spec_codes')),
        'base_codes_text': mapping_to_text(rules.get('base_codes')),
        'sample_dogovor': sample_dogovor,
        'sample_login': sample_login,
    }

_dogovor_latin_lookalikes = str.maketrans({
    'A': 'А',
    'B': 'В',
    'C': 'С',
    'E': 'Е',
    'H': 'Н',
    'I': 'И',
    'K': 'К',
    'M': 'М',
    'O': 'О',
    'P': 'Р',
    'T': 'Т',
    'X': 'Х',
})
_dogovor_dash_re = re.compile(r'[\u2010-\u2015\u2212]')
_dogovor_individual_base_re = re.compile(r'\b(9|11)[иi]\b', re.IGNORECASE)

def normalize_dogovor_text(dogovor):
    normalized = str(dogovor or '').strip()
    normalized = _dogovor_dash_re.sub('-', normalized).replace(' ', '-')
    return normalized.upper().translate(_dogovor_latin_lookalikes)

def normalize_dogovor_storage_text(dogovor):
    value = str(dogovor or '').strip()
    return _dogovor_individual_base_re.sub(lambda match: f'{match.group(1)}И', value)

def find_mapped_code(normalized_text, mapping):
    for label, code in sorted((mapping or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if str(label).upper() in normalized_text:
            return str(label), str(code)
    return None, None

def find_base_code(normalized_text, mapping, match_mode='last_part'):
    if match_mode == 'anywhere':
        return find_mapped_code(normalized_text, mapping)

    parts = normalized_text.split('-')
    if len(parts) < 2:
        return None, None
    last_part = parts[-1].strip()
    for label, code in sorted((mapping or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if last_part == str(label).upper():
            return str(label), str(code)
    return None, None

def parse_dogovor_parts(dogovor, rules=None):
    rules = merge_login_generation_rules(rules or get_login_generation_rules())
    normalized = normalize_dogovor_text(dogovor)
    year_match = re.search(rules['year_regex'], normalized)
    spec_label, spec_code = find_mapped_code(normalized, rules['spec_codes'])
    base_label, base_code = find_base_code(normalized, rules['base_codes'], rules['base_match_mode'])

    if not (year_match and spec_code and base_code):
        return None

    year = year_match.group()
    return {
        'year': year,
        'year_code': year[-2:],
        'spec_label': spec_label,
        'spec_code': spec_code,
        'base_label': base_label,
        'base_code': base_code,
    }

def build_login_from_parts(parts, number, rules=None):
    return render_login_template(rules or get_login_generation_rules(), parts, number)

def next_login_from_parts(parts, existing_logins, rules=None):
    rules = merge_login_generation_rules(rules or get_login_generation_rules())
    number = 1
    while True:
        login = build_login_from_parts(parts, number, rules)
        if login not in existing_logins:
            return login
        number += 1

def parse_dogovor(dogovor, rules=None):
    rules = merge_login_generation_rules(rules or get_login_generation_rules())
    parts = parse_dogovor_parts(dogovor, rules)
    if not parts:
        return rules['error_prefix']
    return f"{parts['year_code']}{parts['spec_code']}{parts['base_code']}"

def split_fio(fio):
    fio = ' '.join(str(fio or '').split())
    if not fio:
        return '', '', ''
    fam, imotch = fio.split(' ', 1) if ' ' in fio else (fio, '')
    return fio, fam, imotch

def get_table_columns(conn, table):
    cur = conn.execute(f'PRAGMA table_info({table})')
    return [row[1] for row in cur.fetchall()]

def table_exists(conn, table):
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    )
    return cur.fetchone() is not None

def get_unique_table_name(conn, base_name):
    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if base_name not in existing_tables:
        return base_name

    suffix = 2
    while f'{base_name}_{suffix}' in existing_tables:
        suffix += 1
    return f'{base_name}_{suffix}'

def create_abiturients_table(conn):
    conn.execute(f'''
        CREATE TABLE IF NOT EXISTS abiturients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT,
            dogovor TEXT,
            login TEXT,
            campaign_year TEXT NOT NULL DEFAULT '{LEGACY_CAMPAIGN_YEAR}',
            fam TEXT,
            imotch TEXT,
            email TEXT,
            paid INTEGER DEFAULT 0,
            comment TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')

def migrate_abiturients_table(conn):
    columns = get_table_columns(conn, 'abiturients')
    if not columns:
        create_abiturients_table(conn)
        return

    table_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='abiturients'"
    ).fetchone()
    table_sql = re.sub(r'\s+', ' ', (table_sql_row[0] if table_sql_row else '').lower())
    has_global_login_unique = 'login text unique' in table_sql

    if 'campaign_year' in columns and not has_global_login_unique:
        if 'paid' not in columns:
            conn.execute('ALTER TABLE abiturients ADD COLUMN paid INTEGER DEFAULT 0')
        conn.execute(
            "UPDATE abiturients SET campaign_year=? WHERE campaign_year IS NULL OR campaign_year=''",
            (LEGACY_CAMPAIGN_YEAR,)
        )
        conn.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_abiturients_campaign_login
            ON abiturients (campaign_year, login)
        ''')
        return

    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    backup_table = 'abiturients_legacy_campaign_migration'
    suffix = 1
    while backup_table in existing_tables:
        suffix += 1
        backup_table = f'abiturients_legacy_campaign_migration_{suffix}'

    conn.execute(f'ALTER TABLE abiturients RENAME TO {backup_table}')
    create_abiturients_table(conn)

    old_columns = get_table_columns(conn, backup_table)
    copy_columns = [
        'id', 'fio', 'dogovor', 'login', 'campaign_year',
        'fam', 'imotch', 'email', 'paid', 'comment', 'created_at'
    ]
    selectable_columns = [column for column in copy_columns if column in old_columns]
    if selectable_columns:
        cur = conn.execute(f'SELECT {", ".join(selectable_columns)} FROM {backup_table}')
        for values in cur.fetchall():
            row = dict(zip(selectable_columns, values))
            campaign_year = normalize_campaign_year(
                row.get('campaign_year'),
                infer_campaign_year(row.get('dogovor'))
            )
            conn.execute(
                '''
                INSERT OR IGNORE INTO abiturients
                    (id, fio, dogovor, login, campaign_year, fam, imotch, email, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    row.get('id'),
                    row.get('fio'),
                    row.get('dogovor'),
                    row.get('login'),
                    campaign_year,
                    row.get('fam'),
                    row.get('imotch'),
                    row.get('email'),
                    row.get('comment'),
                    row.get('created_at'),
                )
            )
    conn.execute(f'DROP TABLE {backup_table}')
    conn.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_abiturients_campaign_login
        ON abiturients (campaign_year, login)
    ''')

def migrate_legacy_students_abiturients_table(conn):
    if not table_exists(conn, 'students'):
        return

    columns = get_table_columns(conn, 'students')
    legacy_columns = {'fio', 'dogovor', 'login', 'fam', 'imotch'}
    moodle_columns = {'username', 'password', 'firstname', 'lastname', 'cohort1'}
    if not legacy_columns.issubset(columns) or moodle_columns.issubset(columns):
        return

    backup_table = get_unique_table_name(conn, 'students_legacy_abiturients_backup')
    conn.execute(f'ALTER TABLE students RENAME TO {backup_table}')

    old_columns = get_table_columns(conn, backup_table)
    copy_columns = [
        'id', 'fio', 'dogovor', 'login', 'fam',
        'imotch', 'email', 'paid', 'comment', 'created_at'
    ]
    selectable_columns = [column for column in copy_columns if column in old_columns]
    if not selectable_columns:
        return

    preserve_ids = conn.execute('SELECT COUNT(*) FROM abiturients').fetchone()[0] == 0
    cur = conn.execute(f'SELECT {", ".join(selectable_columns)} FROM {backup_table}')
    for values in cur.fetchall():
        row = dict(zip(selectable_columns, values))
        campaign_year = infer_campaign_year(row.get('dogovor'))
        if preserve_ids and 'id' in selectable_columns:
            conn.execute(
                '''
                INSERT OR IGNORE INTO abiturients
                    (id, fio, dogovor, login, campaign_year, fam, imotch, email, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    row.get('id'),
                    row.get('fio'),
                    row.get('dogovor'),
                    row.get('login'),
                    campaign_year,
                    row.get('fam'),
                    row.get('imotch'),
                    row.get('email'),
                    row.get('comment'),
                    row.get('created_at'),
                )
            )
        else:
            conn.execute(
                '''
                INSERT OR IGNORE INTO abiturients
                    (fio, dogovor, login, campaign_year, fam, imotch, email, comment, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    row.get('fio'),
                    row.get('dogovor'),
                    row.get('login'),
                    campaign_year,
                    row.get('fam'),
                    row.get('imotch'),
                    row.get('email'),
                    row.get('comment'),
                    row.get('created_at'),
                )
            )

def ensure_campaign_column(conn, table):
    columns = get_table_columns(conn, table)
    if not columns:
        return

    added_column = False
    if 'campaign_year' not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN campaign_year TEXT DEFAULT '{LEGACY_CAMPAIGN_YEAR}'"
        )
        added_column = True

    cur = conn.execute(f'SELECT id, dogovor, campaign_year FROM {table}')
    for row_id, dogovor, campaign_year in cur.fetchall():
        if added_column:
            fixed_year = infer_campaign_year(dogovor)
        else:
            fixed_year = normalize_campaign_year(campaign_year, infer_campaign_year(dogovor))
        if fixed_year != campaign_year:
            conn.execute(f'UPDATE {table} SET campaign_year=? WHERE id=?', (fixed_year, row_id))

def ensure_students_origin_columns(conn):
    columns = get_table_columns(conn, 'students')
    if not columns:
        return

    origin_columns = {
        'source_campaign_year': 'TEXT',
        'source_dogovor': 'TEXT',
        'source_fio': 'TEXT',
    }
    for column, column_type in origin_columns.items():
        if column not in columns:
            conn.execute(f'ALTER TABLE students ADD COLUMN {column} {column_type}')

def ensure_group_year_column(conn):
    if not table_exists(conn, 'groups'):
        return

    columns = get_table_columns(conn, 'groups')
    if not columns:
        return

    if 'group_year' not in columns:
        conn.execute('ALTER TABLE groups ADD COLUMN group_year TEXT')
    if 'is_hidden' not in columns:
        conn.execute('ALTER TABLE groups ADD COLUMN is_hidden INTEGER DEFAULT 0')

    cur = conn.execute('SELECT id, name, group_year, is_hidden FROM groups')
    for row_id, name, group_year, is_hidden in cur.fetchall():
        fixed_year = normalize_group_year(group_year, infer_group_year(name, DEFAULT_CAMPAIGN_YEAR))
        if fixed_year != group_year:
            conn.execute('UPDATE groups SET group_year=? WHERE id=?', (fixed_year, row_id))
        if is_hidden not in (0, 1):
            conn.execute('UPDATE groups SET is_hidden=0 WHERE id=?', (row_id,))

    conn.execute('CREATE INDEX IF NOT EXISTS idx_groups_group_year_name ON groups (group_year, name)')

PASSWORD_HASH_PREFIXES = ('scrypt:', 'pbkdf2:', 'argon2:')
MIN_PASSWORD_LENGTH = 8
LOGIN_MAX_ATTEMPTS = int(os.environ.get('LOGIN_MAX_ATTEMPTS', '5'))
LOGIN_WINDOW_SECONDS = int(os.environ.get('LOGIN_WINDOW_SECONDS', '600'))
CSRF_SESSION_KEY = 'csrf_token'
CSRF_FORM_FIELD = 'csrf_token'

@app.context_processor
def inject_template_globals():
    return {
        'app_version': APP_VERSION,
        'csrf_token': get_csrf_token,
        'role_labels': ROLE_LABELS,
        'is_campaign_archived': is_campaign_archived,
    }

def request_uses_https():
    forwarded_proto = request.headers.get('X-Forwarded-Proto', '')
    forwarded_proto = forwarded_proto.split(',')[0].strip().lower()
    return request.is_secure or forwarded_proto == 'https'

def build_hsts_header():
    parts = [f'max-age={HSTS_MAX_AGE}']
    if HSTS_INCLUDE_SUBDOMAINS:
        parts.append('includeSubDomains')
    if HSTS_PRELOAD:
        parts.append('preload')
    return '; '.join(parts)

@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if ENABLE_HSTS and request_uses_https():
        response.headers.setdefault('Strict-Transport-Security', build_hsts_header())
    if request.endpoint != 'static' and response.mimetype == 'text/html':
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

@app.errorhandler(RequestEntityTooLarge)
def handle_upload_too_large(error):
    flash(f'Файл слишком большой. Максимальный размер: {format_upload_size(MAX_UPLOAD_BYTES)}.', 'error')
    return redirect(get_safe_referrer(default_endpoint='file_work'), code=303)

def get_safe_referrer(default_endpoint='index'):
    referrer = request.referrer
    if referrer:
        parsed = urlparse(referrer)
        if not parsed.netloc or parsed.netloc == request.host:
            return referrer
    return url_for(default_endpoint if 'user' in session else 'login')

def get_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token

def refresh_csrf_token():
    session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_SESSION_KEY]

def validate_csrf_token():
    expected = session.get(CSRF_SESSION_KEY)
    actual = request.form.get(CSRF_FORM_FIELD) or request.headers.get('X-CSRF-Token') or ''
    return bool(expected) and hmac.compare_digest(expected, actual)

def get_login_csrf_token():
    return get_csrf_token()

def refresh_login_csrf_token():
    return refresh_csrf_token()

def validate_login_csrf_token():
    return validate_csrf_token()

@app.before_request
def protect_post_requests_with_csrf():
    if request.method != 'POST':
        return None
    if validate_csrf_token():
        return None
    refresh_csrf_token()
    flash('Сессия формы устарела. Попробуйте ещё раз.', 'error')
    return redirect(get_safe_referrer(default_endpoint='index'), code=303)

SETUP_ALLOWED_ENDPOINTS = {'static', 'login', 'logout', 'register', 'login_generation_setup'}

@app.before_request
def require_login_generation_setup():
    if request.endpoint in SETUP_ALLOWED_ENDPOINTS or not session.get('user'):
        return None
    if is_login_generation_setup_completed():
        return None
    if session.get('role') == 'admin':
        return redirect(url_for('login_generation_setup'), code=303)
    session.clear()
    flash('Администратор должен завершить первичную настройку правил логинов.', 'error')
    return redirect(url_for('login'), code=303)

def get_client_ip():
    forwarded_for = request.headers.get('X-Forwarded-For', '')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or 'unknown'

def sanitize_backup_reason(reason):
    safe_reason = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(reason or 'manual')).strip('_')
    return safe_reason[:60] or 'manual'

def create_database_backup(reason='manual'):
    if not os.path.exists(DB_PATH):
        return None
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    safe_reason = sanitize_backup_reason(reason)
    backup_name = f'{DB_BACKUP_PREFIX}{safe_reason}_{timestamp}.db'
    backup_path = os.path.join(app.config['UPLOAD_FOLDER'], backup_name)
    shutil.copy2(DB_PATH, backup_path)
    return backup_path

def list_database_backups():
    upload_folder = app.config['UPLOAD_FOLDER']
    if not os.path.isdir(upload_folder):
        return []
    backups = []
    for name in os.listdir(upload_folder):
        if not name.startswith(DB_BACKUP_PREFIX) or not name.endswith('.db'):
            continue
        path = os.path.join(upload_folder, name)
        if not os.path.isfile(path):
            continue
        stat = os.stat(path)
        backups.append({
            'name': name,
            'size': stat.st_size,
            'size_text': format_upload_size(stat.st_size),
            'created_at': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        })
    return sorted(backups, key=lambda item: item['created_at'], reverse=True)

def get_backup_path(backup_name):
    backup_name = os.path.basename(str(backup_name or ''))
    if not backup_name.startswith(DB_BACKUP_PREFIX) or not backup_name.endswith('.db'):
        raise ValueError('Некорректное имя резервной копии')
    upload_root = os.path.abspath(app.config['UPLOAD_FOLDER'])
    backup_path = os.path.abspath(os.path.join(upload_root, backup_name))
    if os.path.commonpath([upload_root, backup_path]) != upload_root:
        raise ValueError('Некорректный путь резервной копии')
    if not os.path.exists(backup_path):
        raise FileNotFoundError('Резервная копия не найдена')
    return backup_path

def create_audit_log_table(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    ''')

def create_campaign_settings_table(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS campaign_settings (
            campaign_year TEXT PRIMARY KEY,
            is_archived INTEGER DEFAULT 0,
            archived_at TEXT,
            archived_by TEXT
        )
    ''')

def log_action(action, entity_type='', entity_id='', details='', conn=None):
    username = session.get('user', '') if has_request_context() else ''
    ip_address = get_client_ip() if has_request_context() else ''
    should_close = conn is None
    if should_close:
        conn = sqlite3.connect(DB_PATH)
    try:
        create_audit_log_table(conn)
        conn.execute(
            '''
            INSERT INTO audit_logs (username, action, entity_type, entity_id, details, ip_address)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (username, action, entity_type, str(entity_id or ''), str(details or ''), ip_address)
        )
        if should_close:
            conn.commit()
    finally:
        if should_close:
            conn.close()

def get_audit_logs(limit=200):
    limit = max(1, min(int(limit or 200), 1000))
    with sqlite3.connect(DB_PATH) as conn:
        create_audit_log_table(conn)
        cur = conn.execute(
            '''
            SELECT username, action, entity_type, entity_id, details, ip_address, created_at
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?
            ''',
            (limit,)
        )
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

def is_campaign_archived(campaign_year):
    campaign_year = normalize_campaign_year(campaign_year, DEFAULT_CAMPAIGN_YEAR)
    with sqlite3.connect(DB_PATH) as conn:
        create_campaign_settings_table(conn)
        row = conn.execute(
            'SELECT is_archived FROM campaign_settings WHERE campaign_year=?',
            (campaign_year,)
        ).fetchone()
    return bool(row and row[0])

def ensure_campaign_open(campaign_year):
    if is_campaign_archived(campaign_year):
        flash(ARCHIVED_CAMPAIGN_MESSAGE, 'error')
        return False
    return True

def get_campaign_settings():
    years = get_campaign_years()
    with sqlite3.connect(DB_PATH) as conn:
        create_campaign_settings_table(conn)
        rows = {
            row[0]: {
                'is_archived': bool(row[1]),
                'archived_at': row[2],
                'archived_by': row[3],
            }
            for row in conn.execute(
                'SELECT campaign_year, is_archived, archived_at, archived_by FROM campaign_settings'
            )
        }
    settings = []
    for year in sorted(years, reverse=True):
        values = rows.get(year, {})
        settings.append({
            'campaign_year': year,
            'is_archived': bool(values.get('is_archived')),
            'archived_at': values.get('archived_at') or '',
            'archived_by': values.get('archived_by') or '',
        })
    return settings

def get_dashboard_data(campaign_year):
    campaign_year = normalize_campaign_year(campaign_year, DEFAULT_CAMPAIGN_YEAR)
    with sqlite3.connect(DB_PATH) as conn:
        create_campaign_settings_table(conn)
        ab_total = conn.execute('SELECT COUNT(*) FROM abiturients WHERE campaign_year=?', (campaign_year,)).fetchone()[0]
        no_email = conn.execute(
            "SELECT COUNT(*) FROM abiturients WHERE campaign_year=? AND (email IS NULL OR email='')",
            (campaign_year,)
        ).fetchone()[0]
        unpaid = conn.execute(
            'SELECT COUNT(*) FROM abiturients WHERE campaign_year=? AND COALESCE(paid, 0)=0',
            (campaign_year,)
        ).fetchone()[0]
        ready = conn.execute(
            "SELECT COUNT(*) FROM abiturients WHERE campaign_year=? AND email IS NOT NULL AND email<>'' AND COALESCE(paid, 0)=1",
            (campaign_year,)
        ).fetchone()[0]
        duplicates = conn.execute('SELECT COUNT(*) FROM pending_duplicates WHERE campaign_year=?', (campaign_year,)).fetchone()[0]
        conflicts = conn.execute('SELECT COUNT(*) FROM login_conflicts WHERE campaign_year=?', (campaign_year,)).fetchone()[0]
        students_total = conn.execute(
            'SELECT COUNT(*) FROM students WHERE source_campaign_year=?',
            (campaign_year,)
        ).fetchone()[0]
        students_without_campaign = conn.execute(
            "SELECT COUNT(*) FROM students WHERE source_campaign_year IS NULL OR source_campaign_year=''"
        ).fetchone()[0]
        students_without_dogovor = conn.execute(
            "SELECT COUNT(*) FROM students WHERE source_campaign_year=? AND (source_dogovor IS NULL OR source_dogovor='')",
            (campaign_year,)
        ).fetchone()[0]
        groups = get_groups_with_counts(conn, campaign_year)

    full_groups = [group for group in groups if group['is_full']]
    almost_full_groups = [
        group for group in groups
        if not group['is_full'] and group['capacity'] - group['count'] <= 3
    ]
    alerts = []
    if is_campaign_archived(campaign_year):
        alerts.append(('Архив', ARCHIVED_CAMPAIGN_MESSAGE))
    if conflicts:
        alerts.append(('Конфликты', f'Конфликтов логинов: {conflicts}'))
    if duplicates:
        alerts.append(('Дубли', f'Записей в дублях: {duplicates}'))
    if no_email:
        alerts.append(('Почта', f'Без почты: {no_email}'))
    if unpaid:
        alerts.append(('Оплата', f'Не оплачены: {unpaid}'))
    if full_groups:
        alerts.append(('Группы', f'Заполненных групп: {len(full_groups)}'))
    if students_without_dogovor:
        alerts.append(('Договоры', f'Студентов без договора: {students_without_dogovor}'))
    if students_without_campaign:
        alerts.append(('Студенты', f'Студентов без привязки к кампании: {students_without_campaign}'))

    data_quality = get_data_quality_report(campaign_year)
    task_counts = {
        'no_email': no_email,
        'unpaid': unpaid,
        'duplicates': duplicates,
        'conflicts': conflicts,
        'students_without_dogovor': students_without_dogovor,
        'other_data_quality': max(
            0,
            data_quality['total_issues'] - no_email - unpaid - duplicates - conflicts - students_without_dogovor
        ),
    }

    return {
        'campaign_year': campaign_year,
        'is_archived': is_campaign_archived(campaign_year),
        'abiturients_total': ab_total,
        'no_email': no_email,
        'unpaid': unpaid,
        'ready': ready,
        'duplicates': duplicates,
        'conflicts': conflicts,
        'students_total': students_total,
        'students_without_dogovor': students_without_dogovor,
        'students_without_campaign': students_without_campaign,
        'groups': groups,
        'full_groups': full_groups,
        'almost_full_groups': almost_full_groups,
        'alerts': alerts,
        'tasks': build_dashboard_tasks(task_counts),
    }

def make_data_check(check_id, title, count, description, action_url='', action_label='Открыть', samples=None, tone='warning'):
    return {
        'id': check_id,
        'title': title,
        'count': int(count or 0),
        'description': description,
        'action_url': action_url,
        'action_label': action_label,
        'samples': samples or [],
        'tone': tone if count else 'success',
    }

def make_sample(title, detail='', url=''):
    return {
        'title': title or 'Без названия',
        'detail': detail or '',
        'url': url,
    }

def abiturient_sample(row):
    abiturient_id, fio, dogovor, login, email = row[:5]
    detail_parts = [part for part in (dogovor, login, email) if part]
    return make_sample(fio or login or f'Запись {abiturient_id}', ' · '.join(detail_parts), url_for('person_card', kind='abiturient', record_id=abiturient_id))

def student_sample(row):
    username, email, firstname, lastname, cohort1, source_dogovor = row[:6]
    fio = ' '.join(part for part in (lastname, firstname) if part).strip() or username
    detail_parts = [part for part in (username, cohort1, source_dogovor, email) if part]
    return make_sample(fio, ' · '.join(detail_parts), url_for('person_card', kind='student', record_id=username))

def duplicate_group_samples(groups, title_index=1, detail_index=2, limit=5):
    samples = []
    for grouped_rows in groups[:limit]:
        first = grouped_rows[0]
        title = first[detail_index] or first[title_index] or 'Повтор'
        names = ', '.join((row[title_index] or row[3] or 'без имени') for row in grouped_rows[:3])
        if len(grouped_rows) > 3:
            names += '...'
        samples.append(make_sample(title, f'{len(grouped_rows)} записи: {names}'))
    return samples

def collect_duplicate_groups(rows, key_index):
    grouped = {}
    for row in rows:
        key = normalize_dogovor_key(row[key_index]) if key_index in {2, 5} else normalize_fio_key(row[key_index])
        if key:
            grouped.setdefault(key, []).append(row)
    return [items for items in grouped.values() if len(items) > 1]

def get_invalid_abiturient_email_rows(rows):
    return [row for row in rows if row[4] and not is_valid_email(row[4])]

def get_invalid_student_email_rows(rows):
    return [row for row in rows if row[1] and not is_valid_email(row[1])]

def get_data_quality_report(campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    sample_limit = 5
    with sqlite3.connect(DB_PATH) as conn:
        ab_rows = conn.execute(
            '''
            SELECT id, fio, dogovor, login, email, paid
            FROM abiturients
            WHERE campaign_year=?
            ORDER BY fio
            ''',
            (campaign_year,)
        ).fetchall()
        pending_duplicates = conn.execute(
            'SELECT id, fio, dogovor, login, fam, imotch, campaign_year FROM pending_duplicates WHERE campaign_year=? ORDER BY fio',
            (campaign_year,)
        ).fetchall()
        login_conflict_rows = conn.execute(
            'SELECT id, fio, dogovor, login, fam, imotch, campaign_year, conflict_time FROM login_conflicts WHERE campaign_year=? ORDER BY conflict_time DESC',
            (campaign_year,)
        ).fetchall()
        student_rows = conn.execute(
            '''
            SELECT username, email, firstname, lastname, cohort1, source_dogovor, source_campaign_year
            FROM students
            WHERE source_campaign_year=? OR source_campaign_year IS NULL OR source_campaign_year=''
            ORDER BY lastname, firstname, username
            ''',
            (campaign_year,)
        ).fetchall()

    ab_without_email = [row for row in ab_rows if not str(row[4] or '').strip()]
    ab_unpaid = [row for row in ab_rows if not is_paid_person_value(row[5])]
    ab_without_dogovor = [row for row in ab_rows if not normalize_dogovor_key(row[2])]
    ab_invalid_email = get_invalid_abiturient_email_rows(ab_rows)
    ab_duplicate_dogovors = collect_duplicate_groups(ab_rows, 2)
    ab_same_fio = collect_duplicate_groups(ab_rows, 1)

    current_students = [row for row in student_rows if row[6] == campaign_year]
    students_without_group = [row for row in current_students if not str(row[4] or '').strip()]
    students_without_dogovor = [row for row in current_students if not normalize_dogovor_key(row[5])]
    students_without_campaign = [row for row in student_rows if not str(row[6] or '').strip()]
    students_invalid_email = get_invalid_student_email_rows(current_students)
    student_duplicate_dogovors = collect_duplicate_groups(current_students, 5)

    sections = [
        {
            'title': 'Абитуриенты',
            'checks': [
                make_data_check('abiturients-without-email', 'Без почты', len(ab_without_email), 'Не получится восстановить доступ и выполнить миграцию без почты.', url_for('abiturients', has_email='0'), 'Открыть список', [abiturient_sample(row) for row in ab_without_email[:sample_limit]]),
                make_data_check('abiturients-unpaid', 'Не оплачены', len(ab_unpaid), 'Эти записи не готовы к миграции, пока оплата не отмечена.', url_for('abiturients', has_paid='0'), 'Открыть список', [abiturient_sample(row) for row in ab_unpaid[:sample_limit]]),
                make_data_check('abiturients-invalid-email', 'Некорректная почта', len(ab_invalid_email), 'Почта заполнена, но похожа на ошибочную.', url_for('abiturients'), 'Открыть абитуриентов', [abiturient_sample(row) for row in ab_invalid_email[:sample_limit]]),
                make_data_check('abiturients-without-dogovor', 'Без договора', len(ab_without_dogovor), 'Без договора сложно отличать тёзок и проверять повторы.', url_for('abiturients'), 'Открыть абитуриентов', [abiturient_sample(row) for row in ab_without_dogovor[:sample_limit]]),
                make_data_check('abiturients-duplicate-dogovor', 'Повторяющиеся договоры', sum(len(group) for group in ab_duplicate_dogovors), 'Один договор найден в нескольких записях абитуриентов.', url_for('abiturients'), 'Открыть абитуриентов', duplicate_group_samples(ab_duplicate_dogovors)),
                make_data_check('abiturients-same-fio', 'Одинаковое ФИО', sum(len(group) for group in ab_same_fio), 'Это могут быть дубли или тёзки. Лучше сверить договоры.', url_for('abiturients'), 'Открыть абитуриентов', duplicate_group_samples(ab_same_fio, title_index=1, detail_index=1)),
            ],
        },
        {
            'title': 'Студенты',
            'checks': [
                make_data_check('students-without-group', 'Без академической группы', len(students_without_group), 'Студент есть в базе, но не привязан к группе.', url_for('students_list'), 'Открыть студентов', [student_sample(row) for row in students_without_group[:sample_limit]]),
                make_data_check('students-without-dogovor', 'Без договора при поступлении', len(students_without_dogovor), 'Без договора сложнее проверить, от какого абитуриента появился студент.', url_for('students_list'), 'Открыть студентов', [student_sample(row) for row in students_without_dogovor[:sample_limit]]),
                make_data_check('students-without-campaign', 'Без кампании поступления', len(students_without_campaign), 'У студента не указан год кампании, поэтому он выпадает из отчетов по кампании.', url_for('students_list'), 'Открыть студентов', [student_sample(row) for row in students_without_campaign[:sample_limit]]),
                make_data_check('students-invalid-email', 'Некорректная почта', len(students_invalid_email), 'Почта студента заполнена, но похожа на ошибочную.', url_for('students_list'), 'Открыть студентов', [student_sample(row) for row in students_invalid_email[:sample_limit]]),
                make_data_check('students-duplicate-dogovor', 'Повторяющиеся договоры', sum(len(group) for group in student_duplicate_dogovors), 'Один договор при поступлении найден у нескольких студентов.', url_for('students_list'), 'Открыть студентов', duplicate_group_samples(student_duplicate_dogovors, title_index=3, detail_index=5)),
            ],
        },
        {
            'title': 'Миграция и конфликты',
            'checks': [
                make_data_check('pending-duplicates', 'Дублирующие записи абитуриентов', len(pending_duplicates), 'Эти записи ждут решения: подтвердить или отклонить.', url_for('duplicates_abiturients'), 'Разобрать дубли', [make_sample(row[1], f'{row[2]} · {row[3]}', url_for('person_card', kind='duplicate', record_id=row[0])) for row in pending_duplicates[:sample_limit]]),
                make_data_check('login-conflicts', 'Конфликты логинов', len(login_conflict_rows), 'Система не смогла безопасно назначить логин.', url_for('login_conflicts'), 'Разобрать конфликты', [make_sample(row[1], f'{row[2]} · {row[3]}', url_for('person_card', kind='conflict', record_id=row[0])) for row in login_conflict_rows[:sample_limit]]),
            ],
        },
    ]
    total_issues = sum(check['count'] for section in sections for check in section['checks'])
    return {
        'campaign_year': campaign_year,
        'sections': sections,
        'total_issues': total_issues,
    }

def build_dashboard_tasks(counts):
    tasks = [
        {
            'title': 'Абитуриенты без почты',
            'count': counts.get('no_email', 0),
            'description': 'Нужно добавить почту перед миграцией.',
            'url': url_for('abiturients', has_email='0'),
            'label': 'Открыть',
        },
        {
            'title': 'Не оплачены',
            'count': counts.get('unpaid', 0),
            'description': 'Эти абитуриенты пока не готовы к миграции.',
            'url': url_for('abiturients', has_paid='0'),
            'label': 'Открыть',
        },
        {
            'title': 'Дубли',
            'count': counts.get('duplicates', 0),
            'description': 'Нужно подтвердить или отклонить записи.',
            'url': url_for('duplicates_abiturients'),
            'label': 'Разобрать',
        },
        {
            'title': 'Конфликты логинов',
            'count': counts.get('conflicts', 0),
            'description': 'Нужно назначить корректный логин.',
            'url': url_for('login_conflicts'),
            'label': 'Разобрать',
        },
        {
            'title': 'Студенты без договора',
            'count': counts.get('students_without_dogovor', 0),
            'description': 'Лучше проверить договор, чтобы отличать тёзок.',
            'url': url_for('data_checks') + '#students-without-dogovor',
            'label': 'Проверить',
        },
        {
            'title': 'Другие замечания',
            'count': counts.get('other_data_quality', 0),
            'description': 'Есть дополнительные ошибки или подозрительные записи.',
            'url': url_for('data_checks'),
            'label': 'Проверить',
        },
    ]
    active_tasks = [task for task in tasks if task['count']]
    if active_tasks:
        return active_tasks
    return [{
        'title': 'Критичных задач нет',
        'count': 0,
        'description': 'По текущей кампании явных проблем не найдено.',
        'url': url_for('data_checks'),
        'label': 'Посмотреть проверку',
    }]

def like_pattern(value):
    return f"%{str(value or '').strip()}%"

def global_search_records(query, campaign_year=None, limit=80):
    query = str(query or '').strip()
    if not query:
        return []
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    pattern = like_pattern(query)
    results = []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            '''
            SELECT id, fio, dogovor, login, email, campaign_year
            FROM abiturients
            WHERE campaign_year=? AND (fio LIKE ? OR dogovor LIKE ? OR login LIKE ? OR email LIKE ?)
            ORDER BY fio
            LIMIT ?
            ''',
            (campaign_year, pattern, pattern, pattern, pattern, limit)
        )
        for row in cur.fetchall():
            results.append({
                'kind': 'abiturient',
                'id': row[0],
                'title': row[1],
                'subtitle': f'{row[3]} · {row[2]}',
                'status': 'Абитуриент',
            })

        cur = conn.execute(
            '''
            SELECT username, email, firstname, lastname, cohort1, source_dogovor
            FROM students
            WHERE username LIKE ? OR email LIKE ? OR firstname LIKE ? OR lastname LIKE ? OR cohort1 LIKE ? OR source_dogovor LIKE ?
            ORDER BY lastname, firstname
            LIMIT ?
            ''',
            (pattern, pattern, pattern, pattern, pattern, pattern, limit)
        )
        for row in cur.fetchall():
            fio = ' '.join(part for part in (row[3], row[2]) if part).strip() or row[0]
            results.append({
                'kind': 'student',
                'id': row[0],
                'title': fio,
                'subtitle': f'{row[0]} · {row[4] or "-"} · {row[5] or "без договора"}',
                'status': 'Студент',
            })

        cur = conn.execute(
            '''
            SELECT id, fio, dogovor, login
            FROM pending_duplicates
            WHERE campaign_year=? AND (fio LIKE ? OR dogovor LIKE ? OR login LIKE ?)
            ORDER BY fio
            LIMIT ?
            ''',
            (campaign_year, pattern, pattern, pattern, limit)
        )
        for row in cur.fetchall():
            results.append({
                'kind': 'duplicate',
                'id': row[0],
                'title': row[1],
                'subtitle': f'{row[3]} · {row[2]}',
                'status': 'Дубль',
            })

        cur = conn.execute(
            '''
            SELECT id, fio, dogovor, login
            FROM login_conflicts
            WHERE campaign_year=? AND (fio LIKE ? OR dogovor LIKE ? OR login LIKE ?)
            ORDER BY conflict_time DESC
            LIMIT ?
            ''',
            (campaign_year, pattern, pattern, pattern, limit)
        )
        for row in cur.fetchall():
            results.append({
                'kind': 'conflict',
                'id': row[0],
                'title': row[1],
                'subtitle': f'{row[3]} · {row[2]}',
                'status': 'Конфликт',
            })
    return results[:limit]

def get_person_record(kind, record_id):
    kind = str(kind or '').strip()
    with sqlite3.connect(DB_PATH) as conn:
        if kind == 'abiturient':
            cur = conn.execute('SELECT * FROM abiturients WHERE id=?', (record_id,))
        elif kind == 'student':
            cur = conn.execute('SELECT * FROM students WHERE username=?', (record_id,))
        elif kind == 'duplicate':
            cur = conn.execute('SELECT * FROM pending_duplicates WHERE id=?', (record_id,))
        elif kind == 'conflict':
            cur = conn.execute('SELECT * FROM login_conflicts WHERE id=?', (record_id,))
        else:
            return None
        row = cur.fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in cur.description]
    return {
        'kind': kind,
        'id': record_id,
        'fields': dict(zip(columns, row)),
    }

PERSON_KIND_LABELS = {
    'abiturient': 'Абитуриент',
    'student': 'Студент',
    'duplicate': 'Дублирующая запись',
    'conflict': 'Конфликт логина',
}

PERSON_KIND_TITLES = {
    'abiturient': 'Карточка абитуриента',
    'student': 'Карточка студента',
    'duplicate': 'Карточка возможного дубля',
    'conflict': 'Карточка конфликта логина',
}

PERSON_FIELD_LABELS = {
    'id': 'Номер записи',
    'fio': 'ФИО',
    'dogovor': 'Номер договора',
    'login': 'Логин Moodle',
    'username': 'Логин Moodle',
    'password': 'Пароль Moodle',
    'campaign_year': 'Приемная кампания',
    'source_campaign_year': 'Кампания поступления',
    'fam': 'Фамилия',
    'imotch': 'Имя и отчество',
    'firstname': 'Имя',
    'lastname': 'Фамилия',
    'email': 'Электронная почта',
    'paid': 'Оплата договора',
    'comment': 'Комментарий',
    'created_at': 'Дата добавления',
    'conflict_time': 'Дата конфликта',
    'cohort1': 'Академическая группа',
    'source_dogovor': 'Договор при поступлении',
    'source_fio': 'ФИО при поступлении',
}

PERSON_FIELD_HELP = {
    'id': 'Внутренний номер записи для точного поиска и поддержки.',
    'fio': 'Полное имя человека в текущей записи.',
    'dogovor': 'Номер договора, по нему удобнее всего отличать тезок.',
    'login': 'Учетная запись, с которой человек входит в Moodle.',
    'username': 'Учетная запись, с которой человек входит в Moodle.',
    'password': 'Пароль к учетной записи Moodle.',
    'campaign_year': 'Год приемной кампании, к которой относится запись.',
    'source_campaign_year': 'Год приемной кампании, из которой пришел студент.',
    'fam': 'Фамилия отдельно, используется при формировании логина и списков.',
    'imotch': 'Имя и отчество отдельно, используется при формировании логина и списков.',
    'firstname': 'Имя студента в Moodle.',
    'lastname': 'Фамилия студента в Moodle.',
    'email': 'Почта для связи и восстановления доступа.',
    'paid': 'Показывает, отмечена ли оплата договора.',
    'comment': 'Заметка сотрудника по этой записи.',
    'created_at': 'Когда запись была добавлена в систему.',
    'conflict_time': 'Когда система обнаружила конфликт логина.',
    'cohort1': 'Группа, к которой сейчас привязан студент.',
    'source_dogovor': 'Договор, по которому студент был найден при миграции.',
    'source_fio': 'ФИО из исходной записи абитуриента.',
}

PERSON_SECTION_FIELDS = {
    'abiturient': [
        ('Основные данные', ['fio', 'dogovor', 'campaign_year', 'paid']),
        ('Контакты и доступ', ['login', 'email']),
        ('ФИО по частям', ['fam', 'imotch']),
        ('Дополнительно', ['comment', 'created_at', 'id']),
    ],
    'student': [
        ('Основные данные', ['lastname', 'firstname', 'source_fio', 'cohort1']),
        ('Контакты и доступ', ['username', 'password', 'email']),
        ('Данные при поступлении', ['source_dogovor', 'source_campaign_year']),
        ('Служебная информация', ['id']),
    ],
    'duplicate': [
        ('Основные данные', ['fio', 'dogovor', 'campaign_year']),
        ('Контакты и доступ', ['login']),
        ('ФИО по частям', ['fam', 'imotch']),
        ('Служебная информация', ['id']),
    ],
    'conflict': [
        ('Основные данные', ['fio', 'dogovor', 'campaign_year']),
        ('Контакты и доступ', ['login']),
        ('ФИО по частям', ['fam', 'imotch']),
        ('Служебная информация', ['conflict_time', 'id']),
    ],
}

PERSON_SUMMARY_FIELDS = {
    'abiturient': ['fio', 'dogovor', 'login', 'paid'],
    'student': ['source_fio', 'cohort1', 'username', 'source_dogovor'],
    'duplicate': ['fio', 'dogovor', 'login'],
    'conflict': ['fio', 'dogovor', 'login'],
}

def is_blank_person_value(value):
    return value is None or str(value).strip() in {'', '-'}

def is_paid_person_value(value):
    return str(value).strip().casefold() in {'1', 'true', 'yes', 'да', 'оплачен', 'оплачено', 'paid'}

def humanize_person_field_name(key):
    return str(key or '').replace('_', ' ').strip().capitalize() or 'Поле'

def format_person_field_value(key, value):
    if key == 'paid':
        return 'Договор оплачен' if is_paid_person_value(value) else 'Договор не оплачен'
    if key == 'password' and value == '******':
        return 'Скрыт для безопасности'
    if key == 'email' and is_blank_person_value(value):
        return 'Почта не указана'
    if key == 'comment' and is_blank_person_value(value):
        return 'Комментария нет'
    if is_blank_person_value(value):
        return 'Не указано'
    return str(value)

def get_person_field_state(key, value):
    if key == 'paid':
        return 'success' if is_paid_person_value(value) else 'warning'
    if is_blank_person_value(value):
        return 'muted'
    if key == 'email':
        return 'success'
    return ''

def build_person_card_item(key, value):
    return {
        'key': key,
        'label': PERSON_FIELD_LABELS.get(key, humanize_person_field_name(key)),
        'help': PERSON_FIELD_HELP.get(key, 'Дополнительная информация по записи.'),
        'value': format_person_field_value(key, value),
        'state': get_person_field_state(key, value),
    }

def get_student_display_name(fields):
    source_fio = fields.get('source_fio')
    if not is_blank_person_value(source_fio):
        return str(source_fio)
    fio = ' '.join(part for part in (fields.get('lastname'), fields.get('firstname')) if not is_blank_person_value(part)).strip()
    return fio or fields.get('username') or 'Студент'

def get_person_display_name(kind, fields):
    if kind == 'student':
        return get_student_display_name(fields)
    return fields.get('fio') or fields.get('login') or fields.get('username') or PERSON_KIND_LABELS.get(kind, 'Запись')

def build_person_card_view(record):
    kind = record.get('kind')
    fields = record.get('fields') or {}
    seen = set()
    sections = []

    for section_title, keys in PERSON_SECTION_FIELDS.get(kind, [('Данные записи', list(fields.keys()))]):
        items = []
        for key in keys:
            if key in fields:
                seen.add(key)
                items.append(build_person_card_item(key, fields.get(key)))
        if items:
            sections.append({'title': section_title, 'items': items})

    remaining_items = [
        build_person_card_item(key, value)
        for key, value in fields.items()
        if key not in seen
    ]
    if remaining_items:
        sections.append({'title': 'Дополнительные поля', 'items': remaining_items})

    summary = [
        {
            'label': 'Тип записи',
            'value': PERSON_KIND_LABELS.get(kind, 'Запись'),
            'state': 'info',
        }
    ]
    for key in PERSON_SUMMARY_FIELDS.get(kind, []):
        if key in fields:
            item = build_person_card_item(key, fields.get(key))
            summary.append({
                'label': item['label'],
                'value': item['value'],
                'state': item['state'],
            })

    return {
        'title': PERSON_KIND_TITLES.get(kind, 'Карточка записи'),
        'subtitle': get_person_display_name(kind, fields),
        'summary': summary,
        'sections': sections,
    }

def parse_paid_value(value):
    value = str(value or '').strip().casefold()
    if value in {'1', 'true', 'yes', 'да', 'оплачен', 'оплачено', 'paid'}:
        return 1
    if value in {'0', 'false', 'no', 'нет', 'не оплачен', 'не оплачено', 'unpaid'}:
        return 0
    return None

def find_row_value_casefold(row, aliases):
    normalized = {str(key).strip().casefold(): key for key in row.index}
    for alias in aliases:
        key = normalized.get(alias.casefold())
        if key is not None:
            return row.get(key)
    return None

def process_abiturients_updates(file_path, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    df = read_tabular_upload(file_path)
    df.columns = [str(column).strip() for column in df.columns]
    if not any(column.casefold() in {'договор', 'dogovor', 'source_dogovor'} for column in df.columns):
        raise ValueError('В файле обновлений нужен столбец Договор')

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            'SELECT id, dogovor FROM abiturients WHERE campaign_year=?',
            (campaign_year,)
        ).fetchall()
        dogovor_to_id = {
            normalize_dogovor_key(dogovor): abiturient_id
            for abiturient_id, dogovor in rows
            if normalize_dogovor_key(dogovor)
        }

    updated_email = 0
    updated_paid = 0
    not_found = []
    not_found_rows = []
    errors = []
    backup_path = create_database_backup('before_abiturients_updates')
    with sqlite3.connect(DB_PATH) as conn:
        for row_number, (_, row) in enumerate(df.iterrows(), start=2):
            dogovor = find_row_value_casefold(row, {'Договор', 'dogovor', 'source_dogovor'})
            dogovor_key = normalize_dogovor_key(clean_upload_text(dogovor))
            if not dogovor_key:
                errors.append({
                    'row': row_number,
                    'field': 'Договор',
                    'message': 'Не указан номер договора. Строка пропущена.',
                })
                continue
            abiturient_id = dogovor_to_id.get(dogovor_key)
            if not abiturient_id:
                dogovor_text = clean_upload_text(dogovor)
                not_found.append(dogovor_text)
                not_found_rows.append({
                    'row': row_number,
                    'field': 'Договор',
                    'message': f'Договор не найден в кампании {campaign_year}: {dogovor_text}',
                })
                continue

            email = clean_upload_text(find_row_value_casefold(row, {'Email', 'email', 'Почта', 'почта'}))
            if email and not is_valid_email(email):
                errors.append({
                    'row': row_number,
                    'field': 'Email',
                    'message': f'Почта выглядит некорректно: {email}',
                })
                email = ''
            paid_raw = find_row_value_casefold(row, {'paid', 'Оплата', 'оплата', 'Оплачен', 'оплачен'})
            paid_text = clean_upload_text(paid_raw)
            paid_value = parse_paid_value(paid_raw)
            if paid_text and paid_value is None:
                errors.append({
                    'row': row_number,
                    'field': 'Оплата',
                    'message': f'Не удалось распознать значение оплаты: {paid_text}. Используйте да/нет, 1/0, оплачен/не оплачен.',
                })
            if email:
                conn.execute('UPDATE abiturients SET email=? WHERE id=?', (email, abiturient_id))
                updated_email += 1
            if paid_value is not None:
                conn.execute('UPDATE abiturients SET paid=? WHERE id=?', (paid_value, abiturient_id))
                updated_paid += 1

        log_action(
            'abiturients_updates_import',
            'campaign',
            campaign_year,
            (
                f"rows={len(df)}; email={updated_email}; paid={updated_paid}; "
                f"not_found={len(not_found)}; errors={len(errors)}; "
                f"backup={os.path.basename(backup_path) if backup_path else ''}"
            ),
            conn
        )
    return {
        'total': int(len(df)),
        'updated_email': updated_email,
        'updated_paid': updated_paid,
        'not_found': not_found,
        'not_found_rows': not_found_rows,
        'errors': errors,
    }

def build_abiturients_updates_template():
    output = io.BytesIO()
    template_df = pd.DataFrame(columns=['Договор', 'Email', 'Оплата'])
    help_df = pd.DataFrame([
        {
            'Поле': 'Договор',
            'Что указать': 'Номер договора абитуриента. Это обязательное поле, по нему система ищет запись.',
            'Пример': '2026-СД-0001-11И',
        },
        {
            'Поле': 'Email',
            'Что указать': 'Новая электронная почта. Можно оставить пустым, если почту обновлять не нужно.',
            'Пример': 'student@example.ru',
        },
        {
            'Поле': 'Оплата',
            'Что указать': 'Статус оплаты договора. Подойдут значения: да/нет, 1/0, оплачен/не оплачен.',
            'Пример': 'да',
        },
    ])
    example_df = pd.DataFrame([
        {
            'Договор': '2026-СД-0001-11И',
            'Email': 'student@example.ru',
            'Оплата': 'да',
        },
        {
            'Договор': '2026-ЛД-0002-9И',
            'Email': '',
            'Оплата': 'нет',
        },
    ])
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        template_df.to_excel(writer, sheet_name='Шаблон', index=False)
        help_df.to_excel(writer, sheet_name='Подсказка', index=False)
        example_df.to_excel(writer, sheet_name='Пример', index=False)
    output.seek(0)
    return output

def get_login_attempt_key(username):
    return (get_client_ip(), str(username or '').strip().lower())

def prune_login_attempts(conn, now=None):
    now = now or time()
    conn.execute(
        'DELETE FROM login_attempts WHERE attempted_at < ?',
        (now - LOGIN_WINDOW_SECONDS,)
    )

def get_recent_login_attempts(key):
    now = time()
    ip_address, username = key
    with sqlite3.connect(DB_PATH) as conn:
        prune_login_attempts(conn, now)
        cur = conn.execute(
            '''
            SELECT attempted_at
            FROM login_attempts
            WHERE ip_address=? AND username=? AND attempted_at >= ?
            ORDER BY attempted_at ASC
            ''',
            (ip_address, username, now - LOGIN_WINDOW_SECONDS)
        )
        return [row[0] for row in cur.fetchall()]

def get_login_lockout(username):
    key = get_login_attempt_key(username)
    attempts = get_recent_login_attempts(key)
    if len(attempts) < LOGIN_MAX_ATTEMPTS:
        return 0
    return max(1, int(LOGIN_WINDOW_SECONDS - (time() - attempts[0])))

def record_login_failure(username):
    now = time()
    key = get_login_attempt_key(username)
    ip_address, normalized_username = key
    with sqlite3.connect(DB_PATH) as conn:
        prune_login_attempts(conn, now)
        conn.execute(
            'INSERT INTO login_attempts (ip_address, username, attempted_at) VALUES (?, ?, ?)',
            (ip_address, normalized_username, now)
        )

def clear_login_failures(username):
    ip_address, normalized_username = get_login_attempt_key(username)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'DELETE FROM login_attempts WHERE ip_address=? AND username=?',
            (ip_address, normalized_username)
        )

def is_password_hash(value):
    return str(value or '').startswith(PASSWORD_HASH_PREFIXES)

def hash_user_password(password):
    return generate_password_hash(str(password or ''))

def verify_user_password(stored_password, candidate_password):
    if stored_password in (None, '') or candidate_password in (None, ''):
        return False
    stored_password = str(stored_password or '')
    candidate_password = str(candidate_password or '')
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, candidate_password)
    return hmac.compare_digest(stored_password, candidate_password)

def migrate_user_passwords(conn):
    if not table_exists(conn, 'users'):
        return

    cur = conn.execute('SELECT id, password FROM users')
    for user_id, password in cur.fetchall():
        if password is not None and not is_password_hash(password):
            conn.execute(
                'UPDATE users SET password=? WHERE id=?',
                (hash_user_password(password), user_id)
            )

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        create_abiturients_table(conn)
        migrate_abiturients_table(conn)
        migrate_legacy_students_abiturients_table(conn)
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS pending_duplicates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fio TEXT,
                dogovor TEXT,
                login TEXT,
                campaign_year TEXT NOT NULL DEFAULT '{LEGACY_CAMPAIGN_YEAR}',
                fam TEXT,
                imotch TEXT
            )
        ''')
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS login_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fio TEXT,
                dogovor TEXT,
                login TEXT,
                campaign_year TEXT NOT NULL DEFAULT '{LEGACY_CAMPAIGN_YEAR}',
                fam TEXT,
                imotch TEXT,
                conflict_time TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                fio TEXT,
                position TEXT,
                role TEXT,
                approved INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                username TEXT NOT NULL,
                attempted_at REAL NOT NULL
            )
        ''')
        migrate_user_passwords(conn)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                email TEXT,
                firstname TEXT,
                lastname TEXT,
                cohort1 TEXT,
                source_campaign_year TEXT,
                source_dogovor TEXT,
                source_fio TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS students_duplicates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                password TEXT,
                email TEXT,
                firstname TEXT,
                lastname TEXT,
                cohort1 TEXT
            )
        ''')
        conn.execute(f'''
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                group_year TEXT NOT NULL DEFAULT '{DEFAULT_CAMPAIGN_YEAR}',
                is_hidden INTEGER DEFAULT 0
            )
        ''')
        ensure_group_year_column(conn)
        ensure_campaign_column(conn, 'pending_duplicates')
        ensure_campaign_column(conn, 'login_conflicts')
        ensure_students_origin_columns(conn)
        create_audit_log_table(conn)
        create_campaign_settings_table(conn)
        create_login_generation_settings_table(conn)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_abiturients_campaign_year ON abiturients (campaign_year)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pending_duplicates_campaign_year ON pending_duplicates (campaign_year)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_login_conflicts_campaign_year ON login_conflicts (campaign_year)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_login_attempts_key_time ON login_attempts (ip_address, username, attempted_at)')

init_db()

# Initialize default admin user from local environment only.
_default_admin_password = os.environ.get('ADMIN_DEFAULT_PASSWORD')
with sqlite3.connect(DB_PATH) as conn:
    admin_exists = conn.execute(
        "SELECT 1 FROM users WHERE username=?",
        ("admin",)
    ).fetchone()
    if not admin_exists:
        if not _default_admin_password:
            raise RuntimeError(
                "ADMIN_DEFAULT_PASSWORD is not set. Copy .env.example to .env "
                "and set a strong local admin password before first launch."
            )
        conn.execute(
            "INSERT INTO users (username, password, role, approved) VALUES (?, ?, ?, ?)",
            ("admin", hash_user_password(_default_admin_password), "admin", 1)
        )

def get_campaign_years():
    years = set(BASE_CAMPAIGN_YEARS)
    with sqlite3.connect(DB_PATH) as conn:
        for table in ('abiturients', 'pending_duplicates', 'login_conflicts'):
            if 'campaign_year' not in get_table_columns(conn, table):
                continue
            cur = conn.execute(
                f"SELECT DISTINCT campaign_year FROM {table} WHERE campaign_year IS NOT NULL AND campaign_year != ''"
            )
            years.update(str(row[0]) for row in cur.fetchall() if row[0])
        if table_exists(conn, 'students') and 'source_campaign_year' in get_table_columns(conn, 'students'):
            cur = conn.execute(
                "SELECT DISTINCT source_campaign_year FROM students WHERE source_campaign_year IS NOT NULL AND source_campaign_year != ''"
            )
            years.update(str(row[0]) for row in cur.fetchall() if row[0])
    return sorted(years)

def get_latest_campaign_year():
    years = []
    with sqlite3.connect(DB_PATH) as conn:
        for table in ('abiturients', 'pending_duplicates', 'login_conflicts'):
            if 'campaign_year' not in get_table_columns(conn, table):
                continue
            cur = conn.execute(
                f"SELECT DISTINCT campaign_year FROM {table} WHERE campaign_year IS NOT NULL AND campaign_year != ''"
            )
            years.extend(str(row[0]) for row in cur.fetchall() if row[0])
        if table_exists(conn, 'students') and 'source_campaign_year' in get_table_columns(conn, 'students'):
            cur = conn.execute(
                "SELECT DISTINCT source_campaign_year FROM students WHERE source_campaign_year IS NOT NULL AND source_campaign_year != ''"
            )
            years.extend(str(row[0]) for row in cur.fetchall() if row[0])
    return max(years) if years else DEFAULT_CAMPAIGN_YEAR

def get_active_campaign_year():
    if not has_request_context():
        return DEFAULT_CAMPAIGN_YEAR
    requested_year = request.values.get('campaign_year')
    fallback_year = session.get('campaign_year') or get_latest_campaign_year()
    campaign_year = normalize_campaign_year(requested_year or fallback_year, fallback_year)
    if session.get('user'):
        session['campaign_year'] = campaign_year
    return campaign_year

def get_group_years(selected_year=None, include_base=False):
    years = set(BASE_CAMPAIGN_YEARS if include_base else [])
    if selected_year:
        years.add(normalize_group_year(selected_year, DEFAULT_CAMPAIGN_YEAR))

    with sqlite3.connect(DB_PATH) as conn:
        if table_exists(conn, 'groups'):
            columns = get_table_columns(conn, 'groups')
            if 'group_year' in columns:
                cur = conn.execute(
                    "SELECT DISTINCT group_year FROM groups WHERE group_year IS NOT NULL AND group_year != ''"
                )
                years.update(normalize_group_year(row[0], DEFAULT_CAMPAIGN_YEAR) for row in cur.fetchall() if row[0])
            else:
                cur = conn.execute("SELECT name FROM groups WHERE name IS NOT NULL AND name != ''")
                years.update(infer_group_year(row[0], DEFAULT_CAMPAIGN_YEAR) for row in cur.fetchall() if row[0])

    return sorted(years)

def get_used_logins(campaign_year, rules=None):
    rules = merge_login_generation_rules(rules or get_login_generation_rules())
    used_logins = set()
    with sqlite3.connect(DB_PATH) as conn:
        for table in ('abiturients', 'pending_duplicates', 'login_conflicts'):
            if rules['unique_scope'] == 'global':
                cur = conn.execute(f"SELECT login FROM {table} WHERE login IS NOT NULL")
            else:
                cur = conn.execute(
                    f"SELECT login FROM {table} WHERE campaign_year=? AND login IS NOT NULL",
                    (campaign_year,)
                )
            used_logins.update(str(row[0]).strip() for row in cur.fetchall() if str(row[0]).strip())
        if table_exists(conn, 'students') and 'username' in get_table_columns(conn, 'students'):
            cur = conn.execute(
                "SELECT username FROM students WHERE username IS NOT NULL AND username != ''"
            )
            used_logins.update(str(row[0]).strip() for row in cur.fetchall() if str(row[0]).strip())
    return used_logins

def get_prefixed_logins(table, prefix, campaign_year, rules=None):
    rules = merge_login_generation_rules(rules or get_login_generation_rules())
    with sqlite3.connect(DB_PATH) as conn:
        if rules['unique_scope'] == 'global':
            cur = conn.execute(
                f"SELECT login FROM {table} WHERE login LIKE ?",
                (f'{prefix}%',)
            )
        else:
            cur = conn.execute(
                f"SELECT login FROM {table} WHERE campaign_year=? AND login LIKE ?",
                (campaign_year, f'{prefix}%')
            )
        return set(row[0] for row in cur.fetchall())

def next_numbered_login(prefix, existing_logins, width=3):
    try:
        width = max(1, min(8, int(width)))
    except (TypeError, ValueError):
        width = 3
    number = 1
    while True:
        login = f"{prefix}{number:0{width}d}"
        if login not in existing_logins:
            return login
        number += 1

@app.context_processor
def inject_campaign_context():
    if not has_request_context():
        return {}
    return {
        'campaign_years': get_campaign_years(),
        'active_campaign_year': get_active_campaign_year(),
    }

def is_login_exists(login, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    login = str(login or '').strip()
    if not login:
        return False
    rules = get_login_generation_rules()
    with sqlite3.connect(DB_PATH) as conn:
        if rules['unique_scope'] == 'global':
            cur = conn.execute('SELECT 1 FROM abiturients WHERE login=?', (login,))
        else:
            cur = conn.execute(
                'SELECT 1 FROM abiturients WHERE login=? AND campaign_year=?',
                (login, campaign_year)
            )
        if cur.fetchone() is not None:
            return True
        if table_exists(conn, 'students') and 'username' in get_table_columns(conn, 'students'):
            cur = conn.execute('SELECT 1 FROM students WHERE username=?', (login,))
            if cur.fetchone() is not None:
                return True
        return False

def is_fio_duplicate(fam, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            'SELECT fio FROM abiturients WHERE fam=? AND campaign_year=?',
            (fam, campaign_year)
        )
        return cur.fetchall()

def save_abiturient(fio, dogovor, login, fam, imotch, campaign_year=None):
    dogovor = normalize_dogovor_storage_text(dogovor)
    campaign_year = normalize_campaign_year(campaign_year, infer_campaign_year(dogovor))
    if is_login_exists(login, campaign_year):
        raise sqlite3.IntegrityError(f'Login already exists: {login}')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
            (fio, dogovor, login, campaign_year, fam, imotch)
        )
        conn.commit()

def save_pending_duplicate(fio, dogovor, login, fam, imotch, campaign_year=None):
    dogovor = normalize_dogovor_storage_text(dogovor)
    campaign_year = normalize_campaign_year(campaign_year, infer_campaign_year(dogovor))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO pending_duplicates (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
            (fio, dogovor, login, campaign_year, fam, imotch)
        )
        conn.commit()

def save_login_conflict(fio, dogovor, login, fam, imotch, campaign_year=None):
    dogovor = normalize_dogovor_storage_text(dogovor)
    campaign_year = normalize_campaign_year(campaign_year, infer_campaign_year(dogovor))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO login_conflicts (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
            (fio, dogovor, login, campaign_year, fam, imotch)
        )
        conn.commit()

def clean_upload_text(value):
    if pd.isna(value):
        return ''
    return str(value).strip()

def normalize_fio_key(value):
    return ' '.join(str(value or '').split()).casefold()

def normalize_dogovor_key(value):
    normalized = normalize_dogovor_text(value)
    return normalized if normalized else ''

def get_existing_person_keys(campaign_year):
    keys = set()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            '''
            SELECT fio FROM abiturients
            WHERE campaign_year=? AND fio IS NOT NULL AND fio <> ''
            ''',
            (campaign_year,)
        )
        keys.update(normalize_fio_key(row[0]) for row in cur.fetchall() if normalize_fio_key(row[0]))

        cur = conn.execute(
            '''
            SELECT source_fio, lastname, firstname FROM students
            WHERE source_campaign_year=?
            ''',
            (campaign_year,)
        )
        for source_fio, lastname, firstname in cur.fetchall():
            fio_key = normalize_fio_key(source_fio)
            if not fio_key:
                fio_key = normalize_fio_key(' '.join(part for part in (lastname, firstname) if part))
            if fio_key:
                keys.add(fio_key)
    return keys

def get_existing_dogovor_keys(campaign_year):
    dogovor_keys = {
        'abiturients': set(),
        'pending_duplicates': set(),
        'students': set(),
        'login_conflicts': set(),
    }
    with sqlite3.connect(DB_PATH) as conn:
        sources = (
            ('abiturients', 'SELECT dogovor FROM abiturients WHERE campaign_year=?'),
            ('pending_duplicates', 'SELECT dogovor FROM pending_duplicates WHERE campaign_year=?'),
            ('login_conflicts', 'SELECT dogovor FROM login_conflicts WHERE campaign_year=?'),
        )
        for source_name, query in sources:
            cur = conn.execute(query, (campaign_year,))
            dogovor_keys[source_name].update(
                key for key in (normalize_dogovor_key(row[0]) for row in cur.fetchall()) if key
            )

        cur = conn.execute(
            '''
            SELECT source_dogovor FROM students
            WHERE source_campaign_year=? AND source_dogovor IS NOT NULL AND source_dogovor <> ''
            ''',
            (campaign_year,)
        )
        dogovor_keys['students'].update(
            key for key in (normalize_dogovor_key(row[0]) for row in cur.fetchall()) if key
        )
    return dogovor_keys

def summarize_abiturients_import(df, campaign_year):
    action_counts = df['import_action'].value_counts().to_dict() if not df.empty else {}
    status_counts = df['import_status'].value_counts().to_dict() if not df.empty else {}
    return {
        'campaign_year': campaign_year,
        'total': int(len(df)),
        'ready_count': int(action_counts.get('create', 0)),
        'duplicate_count': int(action_counts.get('duplicate', 0)),
        'conflict_count': int(action_counts.get('conflict', 0)),
        'warning_count': int(df['has_warning'].sum()) if 'has_warning' in df else 0,
        'status_counts': status_counts,
    }

def dataframe_preview_rows(df):
    preview_df = df.copy()
    preview_df = preview_df.where(pd.notnull(preview_df), '')
    rows = preview_df[ABITURIENT_RESULT_COLUMNS].to_dict(orient='records')
    warning_values = preview_df['has_warning'].tolist() if 'has_warning' in preview_df else [False] * len(rows)
    for row, has_warning in zip(rows, warning_values):
        row['has_warning'] = bool(has_warning)
    return rows

def upload_report_item(row, field, message):
    return {
        'row': row,
        'field': field,
        'message': message,
    }

def build_upload_report(title, total, items, summary=None, limit=UPLOAD_REPORT_LIMIT):
    report_items = list(items or [])
    return {
        'title': title,
        'total': int(total or 0),
        'summary': list(summary or []),
        'issue_count': len(report_items),
        'items': report_items[:limit],
        'hidden_count': max(0, len(report_items) - limit),
    }

ABITURIENT_PREVIEW_REPORT_MESSAGES = {
    'Пустое ФИО': 'Не заполнено ФИО. Строка попадет в конфликты, пока ФИО не исправят.',
    'Ошибка договора': 'Не удалось разобрать номер договора. Проверьте год, специальность и базу 9/11.',
    'Договор уже есть у абитуриента': 'Такой договор уже есть в списке абитуриентов. Строка попадет в конфликты.',
    'Договор уже есть у студента': 'Такой договор уже есть у студента. Строка попадет в конфликты, чтобы не создать повтор.',
    'Договор уже ожидает проверки в дублях': 'Такой договор уже находится в дублях. Строка попадет на ручную проверку.',
    'Договор уже есть в конфликтах': 'Такой договор уже есть в конфликтах. Сначала разберите существующий конфликт.',
    'Договор повторяется в файле импорта': 'Такой договор повторяется внутри загруженного файла. Повторная строка попадет в конфликты.',
    'Возможный тёзка, договор другой; будет добавлен': 'ФИО уже встречается в системе, но договор другой. Это может быть тёзка, проверьте перед подтверждением.',
}

def build_abiturients_preview_report(df, summary):
    items = []
    for _, row in df.iterrows():
        status = clean_upload_text(row.get('import_status', ''))
        action = clean_upload_text(row.get('import_action', ''))
        has_warning = bool(row.get('has_warning', False))
        if action == 'create' and not has_warning:
            continue
        field = 'ФИО' if status == 'Пустое ФИО' or 'тёз' in status else 'Договор'
        message = ABITURIENT_PREVIEW_REPORT_MESSAGES.get(status, status)
        items.append(upload_report_item(int(row.get('_row_number', 0)), field, message))

    if not items:
        return None
    return build_upload_report(
        'Отчет по проверке абитуриентов',
        summary['total'],
        items,
        [
            f"К добавлению: {summary['ready_count']}",
            f"В дубли: {summary['duplicate_count']}",
            f"В конфликты: {summary['conflict_count']}",
            f"Возможных тёзок: {summary['warning_count']}",
        ]
    )

def build_abiturients_import_plan(file_path, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    df = read_tabular_upload(file_path)
    df.columns = [str(column).strip() for column in df.columns]
    missing_columns = sorted(ABITURIENT_REQUIRED_COLUMNS - set(df.columns))
    if missing_columns:
        raise ValueError(f"В файле отсутствуют обязательные столбцы: {', '.join(missing_columns)}")
    if df.empty:
        raise ValueError('Файл не содержит строк для импорта')

    df = df.copy()
    df['_row_number'] = range(2, len(df) + 2)
    df['ФИО'] = df['ФИО'].apply(clean_upload_text)
    df['Договор'] = df['Договор'].apply(lambda value: normalize_dogovor_storage_text(clean_upload_text(value)))

    fio_split = df['ФИО'].str.split(' ', n=2, expand=True)
    for column_index in range(3):
        if column_index not in fio_split.columns:
            fio_split[column_index] = ''
    df['Фамилия'] = fio_split[0].fillna('').astype(str).str.strip()
    second_name = fio_split[1].fillna('').astype(str).str.strip()
    third_name = fio_split[2].fillna('').astype(str).str.strip()
    df['Имя_Отчество'] = (second_name + ' ' + third_name).str.strip()
    login_rules = get_login_generation_rules()
    df['login_parts'] = df['Договор'].apply(lambda value: parse_dogovor_parts(value, login_rules))
    df['campaign_year'] = campaign_year

    used_logins = get_used_logins(campaign_year, login_rules)
    error_prefix = login_rules['error_prefix']
    duplicate_prefix = login_rules['duplicate_prefix']
    number_width = login_rules['number_width']
    used_duplicate_logins = set(get_prefixed_logins('pending_duplicates', duplicate_prefix, campaign_year, login_rules))
    known_person_keys = get_existing_person_keys(campaign_year)
    existing_dogovor_keys = get_existing_dogovor_keys(campaign_year)
    planned_dogovor_keys = set()

    logins = []
    actions = []
    statuses = []
    is_duplicate_values = []
    has_warning_values = []

    for _, row in df.iterrows():
        fio = row['ФИО']
        fam = row['Фамилия']
        fio_key = normalize_fio_key(fio)
        dogovor_key = normalize_dogovor_key(row['Договор'])
        login_parts = row['login_parts']

        if not fio or not fam:
            login = next_numbered_login(error_prefix, used_logins, number_width)
            used_logins.add(login)
            logins.append(login)
            actions.append('conflict')
            statuses.append('Пустое ФИО')
            is_duplicate_values.append(False)
            has_warning_values.append(False)
            continue

        if not login_parts:
            login = next_numbered_login(error_prefix, used_logins, number_width)
            used_logins.add(login)
            logins.append(login)
            actions.append('conflict')
            statuses.append('Ошибка договора')
            is_duplicate_values.append(False)
            has_warning_values.append(False)
            continue

        if dogovor_key in existing_dogovor_keys['abiturients']:
            login = next_numbered_login(error_prefix, used_logins, number_width)
            used_logins.add(login)
            logins.append(login)
            actions.append('conflict')
            statuses.append('Договор уже есть у абитуриента')
            is_duplicate_values.append(False)
            has_warning_values.append(False)
            continue

        if dogovor_key in existing_dogovor_keys['students']:
            login = next_numbered_login(error_prefix, used_logins, number_width)
            used_logins.add(login)
            logins.append(login)
            actions.append('conflict')
            statuses.append('Договор уже есть у студента')
            is_duplicate_values.append(False)
            has_warning_values.append(False)
            continue

        if dogovor_key in existing_dogovor_keys['pending_duplicates']:
            login = next_numbered_login(duplicate_prefix, used_duplicate_logins, number_width)
            used_duplicate_logins.add(login)
            used_logins.add(login)
            logins.append(login)
            actions.append('duplicate')
            statuses.append('Договор уже ожидает проверки в дублях')
            is_duplicate_values.append(True)
            has_warning_values.append(False)
            continue

        if dogovor_key in existing_dogovor_keys['login_conflicts']:
            login = next_numbered_login(error_prefix, used_logins, number_width)
            used_logins.add(login)
            logins.append(login)
            actions.append('conflict')
            statuses.append('Договор уже есть в конфликтах')
            is_duplicate_values.append(False)
            has_warning_values.append(False)
            continue

        if dogovor_key in planned_dogovor_keys:
            login = next_numbered_login(error_prefix, used_logins, number_width)
            used_logins.add(login)
            logins.append(login)
            actions.append('conflict')
            statuses.append('Договор повторяется в файле импорта')
            is_duplicate_values.append(False)
            has_warning_values.append(False)
            continue

        login = next_login_from_parts(login_parts, used_logins, login_rules)

        used_logins.add(login)
        planned_dogovor_keys.add(dogovor_key)
        is_possible_namesake = fio_key in known_person_keys
        known_person_keys.add(fio_key)
        logins.append(login)
        actions.append('create')
        statuses.append('Возможный тёзка, договор другой; будет добавлен' if is_possible_namesake else 'Будет добавлен')
        is_duplicate_values.append(False)
        has_warning_values.append(is_possible_namesake)

    df['login'] = logins
    df['import_action'] = actions
    df['import_status'] = statuses
    df['is_duplicate'] = is_duplicate_values
    df['has_warning'] = has_warning_values

    return df, summarize_abiturients_import(df, campaign_year)

def create_abiturients_result_file(df):
    output_path = make_temp_upload_path('xlsx', prefix='result_')
    result_df = df[ABITURIENT_RESULT_COLUMNS].copy()
    result_df.to_excel(output_path, index=False)
    return output_path

def apply_abiturients_import(file_path, campaign_year=None):
    df, summary = build_abiturients_import_plan(file_path, campaign_year)
    backup_path = create_database_backup('before_abiturients_import')
    with sqlite3.connect(DB_PATH) as conn:
        for _, row in df.iterrows():
            action = row['import_action']
            values = (
                row['ФИО'], row['Договор'], row['login'], row['campaign_year'],
                row['Фамилия'], row['Имя_Отчество']
            )
            if action == 'create':
                conn.execute(
                    'INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
                    values
                )
            elif action == 'duplicate':
                conn.execute(
                    'INSERT INTO pending_duplicates (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
                    values
                )
            else:
                conn.execute(
                    'INSERT INTO login_conflicts (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
                    values
                )
        log_action(
            'abiturients_import',
            'campaign',
            summary['campaign_year'],
            (
                f"rows={summary['total']}; create={summary['ready_count']}; "
                f"duplicates={summary['duplicate_count']}; conflicts={summary['conflict_count']}; "
                f"backup={os.path.basename(backup_path) if backup_path else ''}"
            ),
            conn
        )

    return create_abiturients_result_file(df), summary

def process_excel(file_path, campaign_year=None):
    output_path, _summary = apply_abiturients_import(file_path, campaign_year)
    return output_path

def summarize_students_import(df):
    action_counts = df['import_action'].value_counts().to_dict() if not df.empty else {}
    return {
        'total': int(len(df)),
        'ready_count': int(action_counts.get('create', 0)),
        'duplicate_count': int(action_counts.get('duplicate', 0)),
        'skipped_count': int(action_counts.get('skip', 0)),
    }

def build_students_import_plan(file_path):
    df = read_tabular_upload(file_path)
    df.columns = [str(column).strip() for column in df.columns]
    missing_columns = [column for column in STUDENT_UPLOAD_REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        readable_columns = ', '.join(STUDENT_UPLOAD_FIELD_LABELS.get(column, column) for column in missing_columns)
        raise ValueError(f"В файле студентов не хватает столбцов: {readable_columns}")
    if df.empty:
        raise ValueError('Файл студентов не содержит строк для загрузки')

    df = df.copy()
    df['_row_number'] = range(2, len(df) + 2)
    for column in STUDENT_UPLOAD_REQUIRED_COLUMNS:
        df[column] = df[column].apply(clean_upload_text)
    for column in ('source_dogovor', 'source_fio'):
        if column not in df.columns:
            df[column] = ''
        df[column] = df[column].apply(clean_upload_text)
    df['source_campaign_year'] = df['source_dogovor'].apply(
        lambda value: infer_campaign_year(value, DEFAULT_CAMPAIGN_YEAR) if value else ''
    )

    with sqlite3.connect(DB_PATH) as conn:
        existing_usernames = {
            clean_upload_text(row[0])
            for row in conn.execute("SELECT username FROM students WHERE username IS NOT NULL AND username<>''")
        }

    planned_usernames = set()
    actions = []
    statuses = []
    errors = []

    for _, row in df.iterrows():
        row_number = int(row['_row_number'])
        missing_values = [
            STUDENT_UPLOAD_FIELD_LABELS[column]
            for column in STUDENT_UPLOAD_REQUIRED_COLUMNS
            if not row[column]
        ]
        if missing_values:
            actions.append('skip')
            statuses.append(f"Не заполнено: {', '.join(missing_values)}")
            errors.append(upload_report_item(
                row_number,
                'Обязательные поля',
                f"Не заполнено: {', '.join(missing_values)}. Строка будет пропущена."
            ))
            continue

        if not is_valid_email(row['email']):
            actions.append('skip')
            statuses.append('Некорректная почта')
            errors.append(upload_report_item(
                row_number,
                'Email',
                f"Почта выглядит некорректно: {row['email']}. Строка будет пропущена."
            ))
            continue

        username = row['username']
        if username in existing_usernames:
            actions.append('duplicate')
            statuses.append('Логин уже есть у студента')
            errors.append(upload_report_item(
                row_number,
                'Логин',
                f"Логин {username} уже есть у студента. Строка будет перенесена в дубли студентов."
            ))
            continue

        if username in planned_usernames:
            actions.append('duplicate')
            statuses.append('Логин повторяется в файле')
            errors.append(upload_report_item(
                row_number,
                'Логин',
                f"Логин {username} повторяется в загруженном файле. Повторная строка будет перенесена в дубли студентов."
            ))
            continue

        planned_usernames.add(username)
        actions.append('create')
        statuses.append('Будет добавлен')

    df['import_action'] = actions
    df['import_status'] = statuses

    summary = summarize_students_import(df)
    summary['errors'] = errors
    return df, summary

def student_preview_rows(df):
    preview_df = df.copy()
    preview_df = preview_df.where(pd.notnull(preview_df), '')
    rows = preview_df[STUDENT_PREVIEW_COLUMNS].to_dict(orient='records')
    for row in rows:
        action = row.get('import_action')
        if action == 'create':
            row['action_label'] = 'Будет добавлен'
            row['badge_class'] = 'status-success'
        elif action == 'duplicate':
            row['action_label'] = 'В дубли'
            row['badge_class'] = 'status-warning'
        else:
            row['action_label'] = 'Пропущен'
            row['badge_class'] = 'status-danger'
    return rows

def build_students_preview_report(summary):
    report_items = summary.get('errors') or []
    if not report_items:
        return None
    return build_upload_report(
        'Отчет по проверке студентов',
        summary['total'],
        report_items,
        [
            f"К добавлению: {summary['ready_count']}",
            f"В дубли студентов: {summary['duplicate_count']}",
            f"Будет пропущено: {summary['skipped_count']}",
        ]
    )

def apply_students_import(file_path):
    df, summary = build_students_import_plan(file_path)
    backup_path = create_database_backup('before_students_import')
    with sqlite3.connect(DB_PATH) as conn:
        for _, row in df.iterrows():
            action = row['import_action']
            if action == 'create':
                conn.execute(
                    '''
                    INSERT INTO students
                        (username, password, email, firstname, lastname, cohort1, source_campaign_year, source_dogovor, source_fio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        row["username"], row["password"], row["email"],
                        row["firstname"], row["lastname"], row["cohort1"],
                        row["source_campaign_year"], row["source_dogovor"], row["source_fio"]
                    )
                )
            elif action == 'duplicate':
                conn.execute(
                    '''INSERT INTO students_duplicates (username, password, email, firstname, lastname, cohort1)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (
                        row["username"], row["password"], row["email"],
                        row["firstname"], row["lastname"], row["cohort1"]
                    )
                )

        log_action(
            'students_import',
            'students',
            '',
            (
                f"rows={summary['total']}; inserted={summary['ready_count']}; "
                f"duplicates={summary['duplicate_count']}; skipped={summary['skipped_count']}; "
                f"backup={os.path.basename(backup_path) if backup_path else ''}"
            ),
            conn
        )

    return {
        'total': summary['total'],
        'inserted_count': summary['ready_count'],
        'duplicate_count': summary['duplicate_count'],
        'skipped_count': summary['skipped_count'],
        'errors': summary.get('errors') or [],
    }

def process_students_excel(file_path):
    return apply_students_import(file_path)

def normalize_specialty(value):
    value = re.sub(r'\s+', '', str(value or ''))
    key = value.upper().replace('Ё', 'Е')
    return _specialty_aliases.get(key, value)

def normalize_group_base(value):
    value = re.sub(r'\s+', '', str(value or '')).upper()
    return value.replace('I', 'И').replace('M', 'М')

def normalize_group_name(value):
    value = str(value or '').strip()
    value = value.replace('–', '-').replace('—', '-').replace('−', '-')
    value = re.sub(r'\s+', '', value)
    parts = value.split('-')
    if len(parts) < 2:
        return value

    head_match = _group_head_re.fullmatch(parts[0])
    if head_match:
        year_code, specialty = head_match.groups()
        parts[0] = f'{year_code}{normalize_specialty(specialty)}'

    parts[1] = normalize_group_base(parts[1])
    return '-'.join(parts)

def build_group_name(year_code, specialty, base, subgroup='1'):
    year_code = re.sub(r'\D+', '', str(year_code or '').strip())
    if len(year_code) == 4 and year_code.startswith('20'):
        year_code = year_code[-2:]

    specialty = normalize_specialty(specialty)
    base = normalize_group_base(base)
    subgroup = re.sub(r'\D+', '', str(subgroup or '').strip()) or '1'
    if not year_code or not specialty or not base:
        return ''

    return normalize_group_name(f'{year_code}{specialty}-{base}-{subgroup}')

def build_groups_template_csv(group_year=None):
    group_year = normalize_group_year(group_year, DEFAULT_CAMPAIGN_YEAR)
    year_code = group_year[-2:]
    rows = ['group_year;group_name']
    for specialty, base in GROUPS_TEMPLATE_EXAMPLES:
        rows.append(f'{group_year};{build_group_name(year_code, specialty, base)}')
    return '\n'.join(rows) + '\n'

def is_valid_group_name(group_name):
    return bool(_group_name_re.fullmatch(group_name or ''))

def base_group_name(group_name):
    parts = (group_name or '').split('-')
    if len(parts) > 2 and parts[-1].isdigit():
        return '-'.join(parts[:-1])
    return group_name

def group_subgroup_index(group_name):
    parts = (group_name or '').split('-')
    if len(parts) > 2 and parts[-1].isdigit():
        return int(parts[-1])
    return 1

def subgroup_name(root_group, index):
    return root_group if index == 1 else f'{root_group}-{index}'

def find_row_value(row, aliases):
    normalized_row = {
        str(key or '').strip().casefold(): value
        for key, value in row.items()
    }
    for alias in aliases:
        value = normalized_row.get(alias.casefold())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ''

def read_groups_csv(file_path):
    last_error = None
    for encoding in ('utf-8-sig', 'cp1251', 'utf-8'):
        try:
            with open(file_path, newline='', encoding=encoding) as csv_file:
                sample = csv_file.read(4096)
                csv_file.seek(0)
                delimiter = ';' if sample.count(';') >= sample.count(',') else ','
                reader = csv.DictReader(csv_file, delimiter=delimiter)
                if not reader.fieldnames:
                    raise ValueError('в CSV не найдены заголовки')
                return list(reader)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if last_error:
        raise ValueError('не удалось прочитать CSV в кодировке UTF-8 или Windows-1251')
    return []

def group_exists_casefold(existing_groups, group_name):
    return group_name.casefold() in existing_groups

def process_groups_csv(file_path, fallback_group_year=None):
    fallback_group_year = normalize_group_year(fallback_group_year, get_active_campaign_year())
    rows = read_groups_csv(file_path)
    created_groups = []
    skipped_groups = []
    errors = []

    with sqlite3.connect(DB_PATH) as conn:
        existing_groups = {
            row[0].casefold(): row[0]
            for row in conn.execute('SELECT name FROM groups')
        }

        for row_number, row in enumerate(rows, start=2):
            group_name = find_row_value(row, ['group_name', 'name', 'group', 'группа', 'название', 'название_группы'])
            if group_name:
                group_name = normalize_group_name(group_name)
            else:
                group_name = build_group_name(
                    find_row_value(row, ['year_code', 'year', 'год', 'год_поступления']),
                    find_row_value(row, ['specialty', 'spec', 'специальность', 'направление']),
                    find_row_value(row, ['base', 'база', 'база_классов']),
                    find_row_value(row, ['subgroup', 'subgroup_number', 'подгруппа', 'номер_подгруппы']),
                )

            group_year_value = find_row_value(row, ['group_year', 'folder_year', 'year_folder', 'папка', 'год_папки', 'год_групп', 'год_группы'])
            if group_year_value:
                group_year = normalize_group_year(group_year_value, infer_group_year(group_name, fallback_group_year))
                if str(group_year_value).strip() not in (group_year, group_year[-2:]):
                    errors.append(f'строка {row_number}: неверный год папки "{group_year_value}"')
                    continue
            else:
                group_year = infer_group_year(group_name, fallback_group_year)

            if not group_name:
                errors.append(f'строка {row_number}: не указана группа')
                continue
            if not is_valid_group_name(group_name):
                errors.append(f'строка {row_number}: неверный формат группы "{group_name}"')
                continue
            if infer_group_year(group_name, group_year) != group_year:
                errors.append(f'строка {row_number}: группа "{group_name}" не соответствует папке {group_year}')
                continue

            if group_exists_casefold(existing_groups, group_name):
                skipped_groups.append(group_name)
                continue
            conn.execute('INSERT INTO groups (name, group_year) VALUES (?, ?)', (group_name, group_year))
            existing_groups[group_name.casefold()] = group_name
            created_groups.append(f'{group_year}: {group_name}')

    return {
        'created': created_groups,
        'skipped': skipped_groups,
        'errors': errors,
    }

def get_group_student_count(conn, group_name):
    cur = conn.execute('SELECT COUNT(*) FROM students WHERE cohort1=?', (group_name,))
    return cur.fetchone()[0]

def get_next_subgroup_name(conn, group_name, group_year=None):
    root_group = base_group_name(group_name)
    current_index = group_subgroup_index(group_name)
    existing_indices = {current_index}

    if group_year:
        rows = conn.execute('SELECT name FROM groups WHERE group_year=?', (group_year,))
    else:
        rows = conn.execute('SELECT name FROM groups')

    for row in rows:
        existing_name = row[0]
        if base_group_name(existing_name).casefold() == root_group.casefold():
            existing_indices.add(group_subgroup_index(existing_name))

    next_index = current_index + 1
    while next_index in existing_indices:
        next_index += 1
    return f'{root_group}-{next_index}'

def is_last_subgroup(conn, group_name, group_year=None):
    root_group = base_group_name(group_name)
    current_index = group_subgroup_index(group_name)
    max_index = current_index

    if group_year:
        rows = conn.execute('SELECT name FROM groups WHERE group_year=?', (group_year,))
    else:
        rows = conn.execute('SELECT name FROM groups')

    for row in rows:
        existing_name = row[0]
        if base_group_name(existing_name).casefold() == root_group.casefold():
            max_index = max(max_index, group_subgroup_index(existing_name))

    return current_index == max_index

def get_groups_with_counts(conn, group_year=None, include_hidden=False):
    group_year = normalize_group_year(group_year, get_active_campaign_year()) if group_year else None
    groups = []
    if group_year:
        if include_hidden:
            rows = conn.execute(
                'SELECT name, group_year, is_hidden FROM groups WHERE group_year=? ORDER BY is_hidden, name',
                (group_year,)
            )
        else:
            rows = conn.execute(
                'SELECT name, group_year, is_hidden FROM groups WHERE group_year=? AND COALESCE(is_hidden, 0)=0 ORDER BY name',
                (group_year,)
            )
    else:
        if include_hidden:
            rows = conn.execute('SELECT name, group_year, is_hidden FROM groups ORDER BY group_year, is_hidden, name')
        else:
            rows = conn.execute(
                'SELECT name, group_year, is_hidden FROM groups WHERE COALESCE(is_hidden, 0)=0 ORDER BY group_year, name'
            )

    for row in rows:
        name = row[0]
        row_group_year = row[1] or infer_group_year(name, DEFAULT_CAMPAIGN_YEAR)
        is_hidden = bool(row[2])
        count = get_group_student_count(conn, name)
        is_full = count >= MAX_GROUP_STUDENTS
        can_create_next = not is_hidden and is_full and is_last_subgroup(conn, name, row_group_year)
        groups.append({
            'name': name,
            'group_year': row_group_year,
            'is_hidden': is_hidden,
            'count': count,
            'capacity': MAX_GROUP_STUDENTS,
            'fill': f'{count}/{MAX_GROUP_STUDENTS}',
            'is_full': is_full,
            'can_create_next': can_create_next,
            'next_name': get_next_subgroup_name(conn, name, row_group_year) if can_create_next else '',
        })
    return groups

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute('SELECT role, approved FROM users WHERE username=?', (session['user'],))
            user = cur.fetchone()
            if not user or user[0] != 'admin' or user[1] != 1:
                flash('Недостаточно прав')
                return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def vaanedain_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session or session['user'] != 'vaanedain':
            flash('Доступ разрешён только главному администратору!')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/set_campaign', methods=['POST'])
@login_required
def set_campaign():
    campaign_year = normalize_campaign_year(request.form.get('campaign_year'), DEFAULT_CAMPAIGN_YEAR)
    session['campaign_year'] = campaign_year
    session['group_year'] = campaign_year
    next_url = request.form.get('next') or url_for('index')
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = url_for('index')
    return redirect(next_url)

@app.route('/approve_users', methods=['GET', 'POST'])
@admin_required
def approve_users():
    with sqlite3.connect(DB_PATH) as conn:
        if request.method == 'POST':
            user_id = request.form.get('user_id')
            action = request.form.get('action')
            if action == 'approve':
                conn.execute('UPDATE users SET approved=1 WHERE id=?', (user_id,))
                log_action('user_approved', 'user', user_id, conn=conn)
            elif action == 'reject':
                conn.execute('DELETE FROM users WHERE id=?', (user_id,))
                log_action('user_rejected', 'user', user_id, conn=conn)
        cur = conn.execute('SELECT id, username, role FROM users WHERE approved=0')
        pending_users = cur.fetchall()
    return render_template('approve_users.html', pending_users=pending_users)

EXCEL_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

def get_pending_abiturients_import_path(token):
    token = os.path.basename(str(token or ''))
    if not token.startswith(PENDING_ABITURIENTS_IMPORT_PREFIX):
        raise UploadValidationError('Временный файл импорта не найден. Загрузите файл ещё раз.')
    upload_root = os.path.abspath(app.config['UPLOAD_FOLDER'])
    import_path = os.path.abspath(os.path.join(upload_root, token))
    if os.path.commonpath([upload_root, import_path]) != upload_root or not os.path.exists(import_path):
        raise UploadValidationError('Временный файл импорта не найден. Загрузите файл ещё раз.')
    return import_path

def get_pending_students_import_path(token):
    token = os.path.basename(str(token or ''))
    if not token.startswith(PENDING_STUDENTS_IMPORT_PREFIX):
        raise UploadValidationError('Временный файл загрузки студентов не найден. Загрузите файл ещё раз.')
    upload_root = os.path.abspath(app.config['UPLOAD_FOLDER'])
    import_path = os.path.abspath(os.path.join(upload_root, token))
    if os.path.commonpath([upload_root, import_path]) != upload_root or not os.path.exists(import_path):
        raise UploadValidationError('Временный файл загрузки студентов не найден. Загрузите файл ещё раз.')
    return import_path

def build_abiturients_upload_response(file_storage, campaign_year):
    upload_path = None
    result_path = None
    try:
        upload_path = save_upload_to_temp(file_storage, ABITURIENT_UPLOAD_EXTENSIONS)
        result_path, summary = apply_abiturients_import(upload_path, campaign_year)
        flash(
            (
                f"Импорт завершён: добавлено {summary['ready_count']}, "
                f"дублей {summary['duplicate_count']}, конфликтов {summary['conflict_count']}, "
                f"возможных тёзок {summary['warning_count']}."
            ),
            'success'
        )
        return send_temp_download(result_path, 'abiturients_with_logins.xlsx', EXCEL_MIMETYPE)
    except UploadValidationError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Ошибка обработки файла: {exc}', 'error')
    finally:
        cleanup_temp_files(upload_path, result_path)
    return None

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    campaign_year = get_active_campaign_year()
    if request.method == 'POST':
        if not ensure_campaign_open(campaign_year):
            return redirect(url_for('index'), code=303)
        response = build_abiturients_upload_response(request.files.get('file'), campaign_year)
        if response:
            return response
        return redirect(url_for('file_work'), code=303)
    dashboard = get_dashboard_data(campaign_year)
    return render_template('index.html', dashboard=dashboard)

@app.route('/search')
@login_required
def search():
    campaign_year = get_active_campaign_year()
    query = request.args.get('q', '').strip()
    results = global_search_records(query, campaign_year) if query else []
    return render_template('search.html', query=query, results=results, campaign_year=campaign_year)

@app.route('/search_overlay')
@login_required
def search_overlay():
    campaign_year = get_active_campaign_year()
    query = request.args.get('q', '').strip()
    results = global_search_records(query, campaign_year) if query else []
    return jsonify({
        'query': query,
        'total': len(results),
        'results': results,
    })

@app.route('/data_checks')
@login_required
def data_checks():
    campaign_year = get_active_campaign_year()
    report = get_data_quality_report(campaign_year)
    return render_template('data_checks.html', report=report)

@app.route('/person/<kind>/<path:record_id>')
@login_required
def person_card(kind, record_id):
    record = get_person_record(kind, record_id)
    if not record:
        flash('Запись не найдена', 'error')
        return redirect(url_for('search'))
    if record['kind'] == 'student' and session.get('role') != 'admin':
        record['fields']['password'] = '******'
    return render_template('person_card.html', record=record, card_view=build_person_card_view(record))

@app.route('/file_work', methods=['GET', 'POST'])
@login_required
def file_work():
    campaign_year = get_active_campaign_year()
    updates_report = None
    students_report = None
    if request.method == 'GET':
        updates_report = session.pop('abiturients_updates_report', None)
        students_report = session.pop('students_upload_report', None)
    if request.method == 'POST':
        import_action = request.form.get('import_action', 'preview')
        if import_action == 'confirm':
            if not ensure_campaign_open(campaign_year):
                return redirect(url_for('file_work'), code=303)
            pending_path = None
            result_path = None
            try:
                pending_path = get_pending_abiturients_import_path(request.form.get('pending_import'))
                result_path, summary = apply_abiturients_import(pending_path, campaign_year)
                flash(
                    (
                        f"Импорт завершён: добавлено {summary['ready_count']}, "
                        f"дублей {summary['duplicate_count']}, конфликтов {summary['conflict_count']}, "
                        f"возможных тёзок {summary['warning_count']}."
                    ),
                    'success'
                )
                return send_temp_download(result_path, 'abiturients_with_logins.xlsx', EXCEL_MIMETYPE)
            except (UploadValidationError, ValueError) as exc:
                flash(str(exc), 'error')
            except Exception as exc:
                flash(f'Ошибка обработки файла: {exc}', 'error')
            finally:
                cleanup_temp_files(pending_path, result_path)
            return redirect(url_for('file_work'), code=303)

        if import_action == 'cancel':
            try:
                cleanup_temp_files(get_pending_abiturients_import_path(request.form.get('pending_import')))
                flash('Предпросмотр импорта отменён.', 'info')
            except UploadValidationError:
                pass
            return redirect(url_for('file_work'), code=303)

        upload_path = None
        try:
            if not ensure_campaign_open(campaign_year):
                return redirect(url_for('file_work'), code=303)
            upload_path = save_upload_to_temp(
                request.files.get('file'),
                ABITURIENT_UPLOAD_EXTENSIONS,
                prefix=PENDING_ABITURIENTS_IMPORT_PREFIX
            )
            plan_df, preview_summary = build_abiturients_import_plan(upload_path, campaign_year)
            return render_template(
                'file_work.html',
                campaign_year=campaign_year,
                abiturients_preview=preview_summary,
                abiturients_preview_rows=dataframe_preview_rows(plan_df),
                abiturients_report=build_abiturients_preview_report(plan_df, preview_summary),
                updates_report=updates_report,
                students_report=students_report,
                pending_import_token=os.path.basename(upload_path)
            )
        except UploadValidationError as exc:
            flash(str(exc), 'error')
        except Exception as exc:
            cleanup_temp_files(upload_path)
            flash(f'Ошибка обработки файла: {exc}', 'error')
        return redirect(url_for('file_work'), code=303)
    return render_template(
        'file_work.html',
        campaign_year=campaign_year,
        updates_report=updates_report,
        students_report=students_report
    )

@app.route('/abiturients_updates_upload', methods=['POST'])
@login_required
def abiturients_updates_upload():
    if session.get('role') not in {'admin', 'assistant', 'operator'}:
        flash('Недостаточно прав', 'error')
        return redirect(url_for('file_work'), code=303)
    campaign_year = get_active_campaign_year()
    if not ensure_campaign_open(campaign_year):
        return redirect(url_for('file_work'), code=303)
    filepath = None
    try:
        filepath = save_upload_to_temp(request.files.get('updates_file'), ABITURIENT_UPLOAD_EXTENSIONS)
        summary = process_abiturients_updates(filepath, campaign_year)
        report_items = (summary.get('errors') or []) + (summary.get('not_found_rows') or [])
        if report_items:
            session['abiturients_updates_report'] = build_upload_report(
                'Отчет по файлу обновлений',
                summary['total'],
                report_items,
                [
                    f"Обработано строк: {summary['total']}",
                    f"Обновлено почт: {summary['updated_email']}",
                    f"Обновлено статусов оплаты: {summary['updated_paid']}",
                ]
            )
        flash(
            (
                f"Обновления применены: почт {summary['updated_email']}, "
                f"статусов оплаты {summary['updated_paid']}, замечаний {len(report_items)}."
            ),
            'success' if not report_items else 'info'
        )
    except UploadValidationError as exc:
        flash(str(exc), 'error')
    except Exception as exc:
        flash(f'Ошибка обновления данных: {exc}', 'error')
    finally:
        cleanup_temp_files(filepath)
    return redirect(url_for('file_work'), code=303)

@app.route('/abiturients_updates_template/download')
@login_required
def download_abiturients_updates_template():
    return send_file(
        build_abiturients_updates_template(),
        as_attachment=True,
        download_name='abiturients_updates_template.xlsx',
        mimetype=EXCEL_MIMETYPE
    )

@app.route('/abiturients')
@login_required
def abiturients():
    campaign_year = get_active_campaign_year()
    order_by = request.args.get('order_by', 'created_at')
    order_dir = request.args.get('order_dir', 'desc')
    spec = request.args.get('spec')
    base = request.args.get('base')
    year = request.args.get('year')
    is_i = request.args.get('is_i')
    has_email = request.args.get('has_email')
    has_paid = request.args.get('has_paid')
    q = request.args.get('q', '').strip()
    abiturients = get_all_abiturients(order_by, order_dir, spec, base, year, is_i, campaign_year, has_email, has_paid, q)
    login_rules = get_login_generation_rules()
    specs = list(login_rules['spec_codes'].keys())
    bases = list(login_rules['base_codes'].keys())
    years = get_campaign_years()
    return render_template('abiturients.html', abiturients=abiturients, order_by=order_by, order_dir=order_dir, specs=specs, bases=bases, years=years, campaign_year=campaign_year)

def base_filter_variants(base):
    value = str(base or '').strip()
    if not value:
        return []
    variants = [value]
    canonical = canonicalize_base_label(value)
    if canonical and canonical not in variants:
        variants.append(canonical)
    if canonical in {'11И', '9И'}:
        lowercase_alias = f'{canonical[:-1]}и'
        if lowercase_alias not in variants:
            variants.append(lowercase_alias)
    return variants

def get_all_abiturients(order_by='created_at', order_dir='desc', spec=None, base=None, year=None, is_i=None, campaign_year=None, has_email=None, has_paid=None, q=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    valid_columns = {'id', 'fio', 'dogovor', 'login', 'campaign_year', 'fam', 'imotch', 'created_at', 'email', 'paid'}
    if order_by not in valid_columns:
        order_by = 'created_at'
    if order_dir.lower() not in {'asc', 'desc'}:
        order_dir = 'desc'
    query = "SELECT * FROM abiturients WHERE campaign_year=?"
    params = [campaign_year]
    if spec:
        query += " AND dogovor LIKE ?"
        params.append(f"%{spec}%")
    if base:
        variants = base_filter_variants(base)
        query += " AND (" + " OR ".join("dogovor LIKE ?" for _ in variants) + ")"
        params.extend(f"%{variant}%" for variant in variants)
    if year:
        query += " AND dogovor LIKE ?"
        params.append(f"%{year}%")
    if is_i == '1':
        query += " AND login LIKE ?"
        params.append("%i%")
    elif is_i == '0':
        query += " AND login NOT LIKE ?"
        params.append("%i%")
    if has_email == '1':
        query += " AND email IS NOT NULL AND email <> ''"
    elif has_email == '0':
        query += " AND (email IS NULL OR email = '')"
    if has_paid == '1':
        query += " AND paid = 1"
    elif has_paid == '0':
        query += " AND paid = 0"
    q = str(q or '').strip()
    if q:
        query += " AND (fio LIKE ? OR dogovor LIKE ? OR login LIKE ? OR email LIKE ?)"
        params.extend([f"%{q}%"] * 4)
    query += f" ORDER BY {order_by} {order_dir.upper()}"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in rows]

def normalize_student_search(value):
    return ' '.join(str(value or '').split()).casefold()

def student_field_matches(row, field, search_value):
    search_value = normalize_student_search(search_value)
    if not search_value:
        return True
    return search_value in normalize_student_search(row.get(field))

def get_all_students(order_by='username', order_dir='asc', cohort=None, lastname=None, firstname=None, username=None):
    valid_columns = {'username', 'lastname', 'firstname', 'cohort1', 'email'}
    if order_by not in valid_columns:
        order_by = 'username'
    if order_dir.lower() not in {'asc', 'desc'}:
        order_dir = 'asc'
    query = "SELECT username, password, email, firstname, lastname, cohort1 FROM students WHERE 1=1"
    params = []
    if cohort:
        query += " AND cohort1 = ?"
        params.append(cohort)
    query += f" ORDER BY {order_by} {order_dir.upper()}"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        students = [dict(zip(columns, row)) for row in rows]
    return [
        row for row in students
        if student_field_matches(row, 'lastname', lastname)
        and student_field_matches(row, 'firstname', firstname)
        and student_field_matches(row, 'username', username)
    ]

def get_pending_duplicates(campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            'SELECT id, fio, dogovor, login, fam, imotch, campaign_year FROM pending_duplicates WHERE campaign_year=?',
            (campaign_year,)
        )
        return cur.fetchall()

def approve_duplicate(dup_id, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            'SELECT fio, dogovor, login, fam, imotch, campaign_year FROM pending_duplicates WHERE id=? AND campaign_year=?',
            (dup_id, campaign_year)
        )
        row = cur.fetchone()
        if row:
            fio, dogovor, login, fam, imotch, row_campaign_year = row
            conn.execute(
                'INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
                (fio, dogovor, login, row_campaign_year, fam, imotch)
            )
            conn.execute('DELETE FROM pending_duplicates WHERE id=? AND campaign_year=?', (dup_id, campaign_year))

def reject_duplicate(dup_id, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM pending_duplicates WHERE id=? AND campaign_year=?', (dup_id, campaign_year))

@app.route('/duplicates', methods=['GET', 'POST'])
@login_required
def duplicates():
    return render_template('duplicates.html')

def role_required(*roles):
    allowed_roles = set(roles)
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute('SELECT role, approved FROM users WHERE username=?', (session['user'],))
                user = cur.fetchone()
                role = user[0] if user else ''
                role_allowed = role in allowed_roles or role == 'admin'
                if 'assistant' in allowed_roles and role in {'manager', 'operator'}:
                    role_allowed = True
                if not user or not role_allowed or user[1] != 1:
                    flash('Недостаточно прав')
                    return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/abiturients/bulk', methods=['POST'])
@login_required
@role_required('admin', 'assistant', 'operator')
def bulk_abiturients():
    campaign_year = get_active_campaign_year()
    if not ensure_campaign_open(campaign_year):
        return redirect(url_for('abiturients'), code=303)
    action = request.form.get('bulk_action', '').strip()
    if action not in {'mark_paid', 'mark_unpaid', 'delete', 'export'}:
        flash('Неизвестное массовое действие.', 'error')
        return redirect(url_for('abiturients'), code=303)
    if action == 'delete' and session.get('role') != 'admin':
        flash('Удаление доступно только администратору.', 'error')
        return redirect(url_for('abiturients'), code=303)
    selected_ids = [item for item in request.form.getlist('abiturient_ids') if str(item).isdigit()]
    if not selected_ids:
        flash('Выберите хотя бы одну запись.', 'error')
        return redirect(url_for('abiturients'), code=303)

    placeholders = ','.join('?' for _ in selected_ids)
    params = selected_ids + [campaign_year]
    if action == 'export':
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                f'SELECT * FROM abiturients WHERE id IN ({placeholders}) AND campaign_year=? ORDER BY fio',
                params
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
        df = pd.DataFrame([dict(zip(columns, row)) for row in rows])
        output = io.BytesIO()
        df.to_excel(output, index=False)
        output.seek(0)
        log_action('abiturients_bulk_exported', 'campaign', campaign_year, f"rows={len(rows)}")
        return send_file(output, as_attachment=True, download_name='selected_abiturients.xlsx', mimetype=EXCEL_MIMETYPE)

    backup_path = create_database_backup('before_bulk_abiturients')
    with sqlite3.connect(DB_PATH) as conn:
        if action == 'mark_paid':
            conn.execute(f'UPDATE abiturients SET paid=1 WHERE id IN ({placeholders}) AND campaign_year=?', params)
            flash(f'Отмечено оплаченных: {len(selected_ids)}', 'success')
        elif action == 'mark_unpaid':
            conn.execute(f'UPDATE abiturients SET paid=0 WHERE id IN ({placeholders}) AND campaign_year=?', params)
            flash(f'Снята отметка оплаты: {len(selected_ids)}', 'success')
        elif action == 'delete':
            conn.execute(f'DELETE FROM abiturients WHERE id IN ({placeholders}) AND campaign_year=?', params)
            flash(f'Удалено записей: {len(selected_ids)}', 'success')
        log_action(
            'abiturients_bulk_action',
            'campaign',
            campaign_year,
            f"action={action}; rows={len(selected_ids)}; backup={os.path.basename(backup_path) if backup_path else ''}",
            conn
        )
    return redirect(url_for('abiturients'), code=303)

@app.route('/duplicates_abiturients', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def duplicates_abiturients():
    campaign_year = get_active_campaign_year()
    if request.method == 'POST':
        if not ensure_campaign_open(campaign_year):
            return redirect(url_for('duplicates_abiturients'), code=303)
        action = request.form.get('action')
        if action == 'reject_all':
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute('DELETE FROM pending_duplicates WHERE campaign_year=?', (campaign_year,))
        else:
            dup_id = request.form.get('dup_id')
            if action == 'approve':
                approve_duplicate(dup_id, campaign_year)
            elif action == 'reject':
                reject_duplicate(dup_id, campaign_year)
    duplicates = get_pending_duplicates(campaign_year)
    return render_template('duplicates_abiturients.html', duplicates=duplicates, campaign_year=campaign_year)

@app.route('/delete_abiturient', methods=['POST'])
@login_required
@role_required('admin')
def delete_abiturient():
    campaign_year = get_active_campaign_year()
    if not ensure_campaign_open(campaign_year):
        return redirect(url_for('abiturients'), code=303)
    abiturient_id = request.form.get('id')
    login = request.form.get('login')
    if abiturient_id:
        backup_path = create_database_backup('before_delete_abiturient')
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('DELETE FROM abiturients WHERE id=? AND campaign_year=?', (abiturient_id, campaign_year))
            log_action(
                'abiturient_deleted',
                'abiturient',
                abiturient_id,
                f"campaign_year={campaign_year}; backup={os.path.basename(backup_path) if backup_path else ''}",
                conn
            )
    elif login:
        backup_path = create_database_backup('before_delete_abiturient')
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('DELETE FROM abiturients WHERE login=? AND campaign_year=?', (login, campaign_year))
            log_action(
                'abiturient_deleted',
                'abiturient',
                login,
                f"campaign_year={campaign_year}; backup={os.path.basename(backup_path) if backup_path else ''}",
                conn
            )
    return redirect(url_for('abiturients'))

@app.route('/toggle_abiturient_paid', methods=['POST'])
@login_required
@role_required('admin')
def toggle_abiturient_paid():
    campaign_year = get_active_campaign_year()
    if not ensure_campaign_open(campaign_year):
        return redirect(url_for('abiturients'), code=303)
    abiturient_id = request.form.get('id')
    paid = 1 if request.form.get('paid') == '1' else 0
    query_params = {
        'spec': request.form.get('spec', ''),
        'base': request.form.get('base', ''),
        'year': request.form.get('year', ''),
        'is_i': request.form.get('is_i', ''),
        'has_email': request.form.get('has_email', ''),
        'has_paid': request.form.get('has_paid', ''),
        'order_by': request.form.get('order_by', 'created_at'),
        'order_dir': request.form.get('order_dir', 'desc')
    }
    if abiturient_id:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('UPDATE abiturients SET paid=? WHERE id=? AND campaign_year=?', (paid, abiturient_id, campaign_year))
            log_action('abiturient_paid_changed', 'abiturient', abiturient_id, f"paid={paid}; campaign_year={campaign_year}", conn)
    return redirect(url_for('abiturients', **{k: v for k, v in query_params.items() if v}))

@app.route('/abiturients/download')
@login_required
def download_abiturients():
    campaign_year = get_active_campaign_year()
    order_by = request.args.get('order_by', 'created_at')
    order_dir = request.args.get('order_dir', 'desc')
    spec = request.args.get('spec')
    base = request.args.get('base')
    year = request.args.get('year')
    is_i = request.args.get('is_i')
    has_email = request.args.get('has_email')
    has_paid = request.args.get('has_paid')
    q = request.args.get('q', '').strip()
    abiturients = get_all_abiturients(order_by, order_dir, spec, base, year, is_i, campaign_year, has_email, has_paid, q)
    log_action('abiturients_exported', 'campaign', campaign_year, f"rows={len(abiturients)}")
    df = pd.DataFrame(abiturients)
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="abiturients_logins.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/download_template')
def download_template():
    template_path = os.path.join(app.static_folder, 'template.xlsx')
    return send_file(template_path, as_attachment=True, download_name='template.xlsx')

@app.route('/login_conflicts')
@login_required
def login_conflicts():
    campaign_year = get_active_campaign_year()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            '''
            SELECT id, fio, dogovor, login, fam, imotch, campaign_year, conflict_time
            FROM login_conflicts
            WHERE campaign_year=?
            ORDER BY conflict_time DESC
            ''',
            (campaign_year,)
        )
        conflicts = cur.fetchall()
    return render_template('login_conflicts.html', conflicts=conflicts, campaign_year=campaign_year)

@app.route('/edit_conflict/<int:conflict_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_conflict(conflict_id):
    campaign_year = get_active_campaign_year()
    if request.method == 'POST':
        if not ensure_campaign_open(campaign_year):
            return redirect(url_for('login_conflicts'), code=303)
        new_login = request.form.get('login', '').strip()
        if not new_login:
            flash('Логин не может быть пустым')
            return redirect(url_for('edit_conflict', conflict_id=conflict_id))
        
        backup_path = create_database_backup('before_resolve_login_conflict')
        with sqlite3.connect(DB_PATH) as conn:
            # Проверяем уникальность логина
            if is_login_exists(new_login, campaign_year):
                flash(f'Логин {new_login} уже используется!')
                return redirect(url_for('edit_conflict', conflict_id=conflict_id))
            
            # Получаем данные конфликта
            cur = conn.execute(
                'SELECT fio, dogovor, fam, imotch, campaign_year FROM login_conflicts WHERE id=? AND campaign_year=?',
                (conflict_id, campaign_year)
            )
            conflict = cur.fetchone()
            if not conflict:
                flash('Запись не найдена')
                return redirect(url_for('login_conflicts'))
            
            fio, dogovor, fam, imotch, row_campaign_year = conflict
            
            # Сохраняем в основную таблицу абитуриентов
            try:
                conn.execute(
                    'INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
                    (fio, dogovor, new_login, row_campaign_year, fam, imotch)
                )
                # Удаляем из конфликтов
                conn.execute('DELETE FROM login_conflicts WHERE id=? AND campaign_year=?', (conflict_id, campaign_year))
                log_action(
                    'login_conflict_resolved',
                    'login_conflict',
                    conflict_id,
                    (
                        f"new_login={new_login}; campaign_year={row_campaign_year}; "
                        f"backup={os.path.basename(backup_path) if backup_path else ''}"
                    ),
                    conn
                )
                conn.commit()
                flash(f'Абитуриент успешно добавлен с логином {new_login}')
                return redirect(url_for('login_conflicts'))
            except sqlite3.IntegrityError:
                flash(f'Логин {new_login} уже существует!')
                return redirect(url_for('edit_conflict', conflict_id=conflict_id))
    
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            'SELECT id, fio, dogovor, login, fam, imotch, campaign_year FROM login_conflicts WHERE id=? AND campaign_year=?',
            (conflict_id, campaign_year)
        )
        conflict = cur.fetchone()
    
    if not conflict:
        flash('Запись не найдена')
        return redirect(url_for('login_conflicts'))
    
    return render_template('edit_conflict.html', conflict=conflict)

@app.route('/delete_conflict/<int:conflict_id>', methods=['POST'])
@login_required
@role_required('admin')
def delete_conflict(conflict_id):
    campaign_year = get_active_campaign_year()
    if not ensure_campaign_open(campaign_year):
        return redirect(url_for('login_conflicts'), code=303)
    backup_path = create_database_backup('before_delete_login_conflict')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM login_conflicts WHERE id=? AND campaign_year=?', (conflict_id, campaign_year))
        log_action(
            'login_conflict_deleted',
            'login_conflict',
            conflict_id,
            f"campaign_year={campaign_year}; backup={os.path.basename(backup_path) if backup_path else ''}",
            conn
        )
        conn.commit()
    flash('Запись удалена')
    return redirect(url_for('login_conflicts'))

@login_required
@role_required('admin')
def delete_database():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        init_db()
    return redirect(url_for('index'))

@app.route('/manual_create', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def manual_create():
    message = None
    conflict_info = None
    campaign_year = get_active_campaign_year()
    years = get_campaign_years()
    login_rules = get_login_generation_rules()
    specs = list(login_rules['spec_codes'].keys())
    bases = list(login_rules['base_codes'].keys())
    if request.method == 'POST':
        year = normalize_campaign_year(request.form.get('year'), campaign_year)
        campaign_year = year
        if not ensure_campaign_open(campaign_year):
            return redirect(url_for('manual_create'), code=303)
        session['campaign_year'] = campaign_year
        spec = request.form.get('spec')
        base = request.form.get('base')
        fio = request.form.get('fio').strip()
        fam, imotch = fio.split(' ', 1) if ' ' in fio else (fio, '')
        dogovor = f"{year} {spec} {base}"

        login_parts = parse_dogovor_parts(dogovor, login_rules)
        if not login_parts:
            # Сохраняем ошибочный логин в конфликты
            error_login = next_numbered_login(
                login_rules['error_prefix'],
                get_used_logins(campaign_year, login_rules),
                login_rules['number_width']
            )
            save_login_conflict(fio, dogovor, error_login, fam, imotch, campaign_year)
            message = f"Ошибка парсинга договора! Запись отправлена в раздел 'Конфликты логинов' с логином {error_login}."
        else:
            existing_logins = get_used_logins(campaign_year, login_rules)
            login = next_login_from_parts(login_parts, existing_logins, login_rules)

            if is_fio_duplicate(fam, campaign_year):
                dubl_logins = get_prefixed_logins(
                    'pending_duplicates',
                    login_rules['duplicate_prefix'],
                    campaign_year,
                    login_rules
                )
                dubl_login = next_numbered_login(
                    login_rules['duplicate_prefix'],
                    dubl_logins,
                    login_rules['number_width']
                )
                save_pending_duplicate(fio, dogovor, dubl_login, fam, imotch, campaign_year)
                message = f"Дублирующее ФИО! Запись отправлена в раздел 'Дублирующиеся ФИО'. Логин: {dubl_login}"
            else:
                try:
                    save_abiturient(fio, dogovor, login, fam, imotch, campaign_year)
                    message = f"Логин успешно создан: {login}"
                except sqlite3.IntegrityError:
                    save_login_conflict(fio, dogovor, login, fam, imotch, campaign_year)
                    with sqlite3.connect(DB_PATH) as conn:
                        cur = conn.execute(
                            'SELECT fio, dogovor FROM abiturients WHERE login=? AND campaign_year=?',
                            (login, campaign_year)
                        )
                        conflict_info = cur.fetchone()
                    message = f"Конфликт логина! Запись отправлена в раздел 'Конфликты логинов'."
        if message:
            log_action('manual_abiturient_create', 'campaign', campaign_year, message)
        return render_template('manual_create.html', message=message, conflict_info=conflict_info, years=years, specs=specs, bases=bases, campaign_year=campaign_year)

    return render_template('manual_create.html', years=years, specs=specs, bases=bases, campaign_year=campaign_year)

@app.route('/login', methods=['GET', 'POST'])
def login():
    session.pop('user', None)
    session.pop('role', None)
    username = ''
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not validate_login_csrf_token():
            flash('Сессия формы устарела. Попробуйте ещё раз.', 'error')
            return render_template('login.html', login_csrf_token=refresh_login_csrf_token(), username=username)
        lockout_seconds = get_login_lockout(username)
        if lockout_seconds:
            lockout_minutes = max(1, (lockout_seconds + 59) // 60)
            flash(f'Слишком много попыток входа. Попробуйте через {lockout_minutes} мин.', 'error')
            return render_template('login.html', login_csrf_token=get_login_csrf_token(), username=username)
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                'SELECT id, password, role, approved FROM users WHERE username=?',
                (username,)
            )
            user = cur.fetchone()
            password_ok = bool(user and verify_user_password(user[1], password))
            if password_ok and not is_password_hash(user[1]):
                conn.execute('UPDATE users SET password=? WHERE id=?', (hash_user_password(password), user[0]))
            if password_ok and user[3] == 1:
                clear_login_failures(username)
                session.clear()
                session['user'] = username
                session['role'] = user[2]
                return redirect(url_for('index'))
            elif password_ok and user[3] == 0:
                flash('Ожидайте одобрения администратора.', 'error')
            else:
                record_login_failure(username)
                flash('Неверный логин или пароль.', 'error')
    return render_template('login.html', login_csrf_token=get_login_csrf_token(), username=username)

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы.', 'success')
    return redirect(url_for('login'))

@app.route('/edit_abiturient/<login>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_abiturient(login):
    campaign_year = get_active_campaign_year()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            'SELECT fio, dogovor, login, fam, imotch, email, comment, campaign_year, paid FROM abiturients WHERE login=? AND campaign_year=?',
            (login, campaign_year)
        )
        abiturient = cur.fetchone()
    if not abiturient:
        flash('Абитуриент не найден')
        return redirect(url_for('abiturients'))

    if request.method == 'POST':
        if not ensure_campaign_open(campaign_year):
            return redirect(url_for('abiturients'), code=303)
        fio, fam, imotch = split_fio(request.form.get('fio', ''))
        if not fio:
            flash('ФИО не может быть пустым')
            return render_template('edit_abiturient.html', abiturient=abiturient)
        email = request.form.get('email', '').strip()
        paid = 1 if request.form.get('paid') == '1' else 0
        new_login = request.form.get('login', '').strip()
        comment = request.form.get('comment', '').strip()
        backup_path = create_database_backup('before_edit_abiturient')
        if new_login != login:
            if is_login_exists(new_login, campaign_year):
                flash('Такой логин уже существует!')
                return render_template('edit_abiturient.html', abiturient=abiturient)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    'UPDATE abiturients SET fio=?, fam=?, imotch=?, email=?, paid=?, login=?, comment=? WHERE login=? AND campaign_year=?',
                    (fio, fam, imotch, email, paid, new_login, comment, login, campaign_year)
                )
                log_action(
                    'abiturient_updated',
                    'abiturient',
                    login,
                    f"new_login={new_login}; campaign_year={campaign_year}; backup={os.path.basename(backup_path) if backup_path else ''}",
                    conn
                )
        else:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    'UPDATE abiturients SET fio=?, fam=?, imotch=?, email=?, paid=?, comment=? WHERE login=? AND campaign_year=?',
                    (fio, fam, imotch, email, paid, comment, login, campaign_year)
                )
                log_action(
                    'abiturient_updated',
                    'abiturient',
                    login,
                    f"campaign_year={campaign_year}; backup={os.path.basename(backup_path) if backup_path else ''}",
                    conn
                )
        flash('Данные обновлены')
        return redirect(url_for('abiturients'))

    return render_template('edit_abiturient.html', abiturient=abiturient)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        role = 'assistant'
        if not username or len(password) < MIN_PASSWORD_LENGTH:
            flash(f'Логин обязателен, пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов')
            return render_template('register.html')
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute('SELECT 1 FROM users WHERE username=?', (username,))
            if cur.fetchone():
                flash('Пользователь уже существует')
                return render_template('register.html')
            conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', (username, hash_user_password(password), role))
        flash('Заявка на регистрацию отправлена. Ожидайте одобрения администратора.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/setup', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def login_generation_setup():
    if request.method == 'POST':
        mode = request.form.get('mode', 'custom')
        try:
            rules = build_login_rules_from_form(request.form, mode)
            save_login_generation_settings(rules, setup_completed=True, updated_by=session.get('user', ''))
            log_action('login_generation_settings_saved', 'settings', 'login_generation', f"mode={rules['mode']}")
            flash('Правила формирования логинов сохранены.', 'success')
            return redirect(url_for('index'))
        except (ValueError, re.error) as exc:
            flash(f'Не удалось сохранить правила: {exc}', 'error')
    return render_template('login_rules_setup.html', **get_login_rules_form_context())

@app.route('/admin_panel')
@admin_required
def admin_panel():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT id, username, role, approved FROM users')
        all_users = cur.fetchall()
    return render_template('admin_panel.html', all_users=all_users)

@app.route('/backups')
@admin_required
def backups():
    return render_template('backups.html', backups=list_database_backups())

@app.route('/backups/download/<backup_name>')
@admin_required
def download_backup(backup_name):
    backup_path = get_backup_path(backup_name)
    log_action('database_backup_download', 'backup', backup_name)
    return send_file(backup_path, as_attachment=True, download_name=backup_name, mimetype='application/octet-stream')

@app.route('/backups/restore', methods=['POST'])
@admin_required
def restore_backup():
    backup_name = request.form.get('backup_name')
    try:
        backup_path = get_backup_path(backup_name)
        rollback_path = create_database_backup('before_restore')
        shutil.copy2(backup_path, DB_PATH)
        init_db()
        log_action(
            'database_restore',
            'backup',
            backup_name,
            f"rollback_backup={os.path.basename(rollback_path) if rollback_path else ''}"
        )
        flash(f'База восстановлена из резервной копии {backup_name}.', 'success')
    except Exception as exc:
        flash(f'Не удалось восстановить базу: {exc}', 'error')
    return redirect(url_for('backups'))

@app.route('/audit_logs')
@admin_required
def audit_logs():
    return render_template('audit_logs.html', audit_logs=get_audit_logs())

@app.route('/campaigns', methods=['GET', 'POST'])
@admin_required
def campaigns():
    if request.method == 'POST':
        campaign_year = normalize_campaign_year(request.form.get('campaign_year'), get_active_campaign_year())
        is_archived = 1 if request.form.get('is_archived') == '1' else 0
        backup_path = create_database_backup('before_campaign_archive_toggle')
        with sqlite3.connect(DB_PATH) as conn:
            create_campaign_settings_table(conn)
            conn.execute(
                '''
                INSERT INTO campaign_settings (campaign_year, is_archived, archived_at, archived_by)
                VALUES (?, ?, datetime('now', 'localtime'), ?)
                ON CONFLICT(campaign_year) DO UPDATE SET
                    is_archived=excluded.is_archived,
                    archived_at=excluded.archived_at,
                    archived_by=excluded.archived_by
                ''',
                (campaign_year, is_archived, session.get('user', ''))
            )
            log_action(
                'campaign_archive_changed',
                'campaign',
                campaign_year,
                f"is_archived={is_archived}; backup={os.path.basename(backup_path) if backup_path else ''}",
                conn
            )
        flash(f"Кампания {campaign_year}: {'архивирована' if is_archived else 'открыта'}.", 'success')
        return redirect(url_for('campaigns'))
    return render_template('campaigns.html', campaigns=get_campaign_settings())

@app.route('/delete_user', methods=['POST'])
@admin_required
def delete_user():
    user_id = request.form.get('user_id')
    backup_path = create_database_backup('before_delete_user')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM users WHERE id=?', (user_id,))
        log_action(
            'user_deleted',
            'user',
            user_id,
            f"backup={os.path.basename(backup_path) if backup_path else ''}",
            conn
        )
    return redirect(url_for('admin_panel'))

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT id, username, role, approved FROM users WHERE id=?', (user_id,))
        user = cur.fetchone()
        if not user:
            flash('Пользователь не найден')
            return redirect(url_for('admin_panel'))
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            password = (request.form.get('password') or '').strip()
            role = request.form.get('role')
            approved = int(request.form.get('approved', 0))
            if role not in ROLE_LABELS:
                role = 'viewer'
            if not username:
                flash('Логин не может быть пустым')
                return render_template('edit_user.html', user=user)
            duplicate = conn.execute(
                'SELECT 1 FROM users WHERE username=? AND id<>?',
                (username, user_id)
            ).fetchone()
            if duplicate:
                flash('Пользователь с таким логином уже существует')
                return render_template('edit_user.html', user=user)
            if password:
                if len(password) < MIN_PASSWORD_LENGTH:
                    flash(f'Новый пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов')
                    return render_template('edit_user.html', user=user)
                conn.execute(
                    'UPDATE users SET username=?, password=?, role=?, approved=? WHERE id=?',
                    (username, hash_user_password(password), role, approved, user_id)
                )
            else:
                conn.execute(
                    'UPDATE users SET username=?, role=?, approved=? WHERE id=?',
                    (username, role, approved, user_id)
                )
            log_action('user_updated', 'user', user_id, f"username={username}; role={role}; approved={approved}", conn)
            return redirect(url_for('admin_panel'))
    return render_template('edit_user.html', user=user)

@app.route('/add_user', methods=['GET', 'POST'])
@admin_required
def add_user():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        fio = (request.form.get('fio') or '').strip()
        position = (request.form.get('position') or '').strip()
        role = request.form.get('role')
        if role not in ROLE_LABELS:
            role = 'viewer'
        approved = int(request.form.get('approved', 1))
        if not username or len(password) < MIN_PASSWORD_LENGTH:
            flash(f'Логин обязателен, пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов')
            return render_template('add_user.html')
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute('SELECT 1 FROM users WHERE username=?', (username,))
            if cur.fetchone():
                flash('Пользователь с таким логином уже существует')
                return render_template('add_user.html')
            conn.execute(
                'INSERT INTO users (username, password, fio, position, role, approved) VALUES (?, ?, ?, ?, ?, ?)',
                (username, hash_user_password(password), fio, position, role, approved)
            )
            log_action('user_created', 'user', username, f"role={role}; approved={approved}", conn)
        flash('Пользователь успешно добавлен!')
        return redirect(url_for('admin_panel'))
    return render_template('add_user.html')

@app.route('/clear_abiturients', methods=['POST'])
@admin_required
def clear_abiturients():
    campaign_year = get_active_campaign_year()
    if not ensure_campaign_open(campaign_year):
        return redirect(url_for('admin_panel'), code=303)
    backup_path = create_database_backup('before_clear_abiturients')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM abiturients WHERE campaign_year=?', (campaign_year,))
        conn.execute('DELETE FROM pending_duplicates WHERE campaign_year=?', (campaign_year,))
        conn.execute('DELETE FROM login_conflicts WHERE campaign_year=?', (campaign_year,))
        log_action(
            'abiturients_campaign_cleared',
            'campaign',
            campaign_year,
            f"backup={os.path.basename(backup_path) if backup_path else ''}",
            conn
        )
    flash(f'Абитуриенты, дубли и конфликты кампании {campaign_year} успешно очищены.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/students_upload', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def students_upload():
    message = None
    if request.method == 'POST':
        students_import_action = request.form.get('students_import_action', 'preview')
        if students_import_action == 'confirm':
            filepath = None
            try:
                filepath = get_pending_students_import_path(request.form.get('pending_students_import'))
                summary = apply_students_import(filepath)
                flash(
                    (
                        f"Загрузка студентов завершена: добавлено {summary['inserted_count']}, "
                        f"дублей {summary['duplicate_count']}, пропущено {summary['skipped_count']}."
                    ),
                    'success' if not summary.get('errors') else 'info'
                )
            except (UploadValidationError, ValueError) as e:
                flash(str(e), 'error')
            except Exception as e:
                flash(f"Ошибка: {e}", 'error')
            finally:
                cleanup_temp_files(filepath)
            return redirect(url_for('file_work'), code=303)

        if students_import_action == 'cancel':
            try:
                cleanup_temp_files(get_pending_students_import_path(request.form.get('pending_students_import')))
                flash('Предпросмотр загрузки студентов отменён.', 'info')
            except UploadValidationError:
                pass
            return redirect(url_for('file_work'), code=303)

        filepath = None
        try:
            filepath = save_upload_to_temp(
                request.files.get('file'),
                STUDENTS_UPLOAD_EXTENSIONS,
                prefix=PENDING_STUDENTS_IMPORT_PREFIX
            )
            plan_df, summary = build_students_import_plan(filepath)
            return render_template(
                'file_work.html',
                campaign_year=get_active_campaign_year(),
                students_preview=summary,
                students_preview_rows=student_preview_rows(plan_df),
                students_report=build_students_preview_report(summary),
                pending_students_import_token=os.path.basename(filepath)
            )
        except (UploadValidationError, ValueError) as e:
            message = str(e)
            flash(message, 'error')
            cleanup_temp_files(filepath)
        except Exception as e:
            message = f"Ошибка: {e}"
            flash(message, 'error')
            cleanup_temp_files(filepath)
        return redirect(url_for('file_work'), code=303)
    return render_template('students_upload.html', message=message)

@app.route('/students')
@login_required
def students():
    lastname = request.args.get('lastname', '').strip()
    firstname = request.args.get('firstname', '').strip()
    username = request.args.get('username', '').strip()
    cohort = request.args.get('cohort', '').strip()
    order_by = request.args.get('order_by', 'username')
    order_dir = request.args.get('order_dir', 'asc')
    students = get_all_students(order_by, order_dir, cohort, lastname, firstname, username)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT DISTINCT cohort1 FROM students ORDER BY cohort1')
        cohorts = [row[0] for row in cur.fetchall()]

    return render_template('students.html', students=students, cohorts=cohorts, order_by=order_by, order_dir=order_dir)

@app.route('/students_duplicates')
@login_required
@role_required('admin')
def students_duplicates():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT username, password, email, firstname, lastname, cohort1 FROM students_duplicates')
        duplicates = cur.fetchall()
    return render_template('students_duplicates.html', duplicates=duplicates)

@app.route('/students_list')
@login_required
def students_list():
    order_by = request.args.get('order_by', 'username')
    order_dir = request.args.get('order_dir', 'asc')
    cohort = request.args.get('cohort')
    lastname = request.args.get('lastname')
    firstname = request.args.get('firstname')
    username = request.args.get('username')
    students = get_all_students(order_by, order_dir, cohort, lastname, firstname, username)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT DISTINCT cohort1 FROM students ORDER BY cohort1')
        cohorts = [row[0] for row in cur.fetchall()]
    return render_template('students_list.html', students=students, cohorts=cohorts, order_by=order_by, order_dir=order_dir)

@app.route('/students/download')
@login_required
def download_students():
    order_by = request.args.get('order_by', 'username')
    order_dir = request.args.get('order_dir', 'asc')
    cohort = request.args.get('cohort')
    lastname = request.args.get('lastname')
    firstname = request.args.get('firstname')
    username = request.args.get('username')
    students = get_all_students(order_by, order_dir, cohort, lastname, firstname, username)
    log_action('students_exported', 'students', '', f"rows={len(students)}")
    export_students = students
    if session.get('role') != 'admin':
        export_students = [dict(student, password='******') for student in students]
    df = pd.DataFrame(export_students)
    output = io.BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="students.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/edit_student/<username>', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def edit_student(username):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT username, password, email, firstname, lastname, cohort1 FROM students WHERE username=?', (username,))
        student = cur.fetchone()
    if not student:
        flash('Студент не найден')
        return redirect(url_for('students_list'))
    if request.method == 'POST':
        backup_path = create_database_backup('before_edit_student')
        password = request.form.get('password', '').strip()
        email = request.form.get('email', '').strip()
        firstname = request.form.get('firstname', '').strip()
        lastname = request.form.get('lastname', '').strip()
        cohort1 = request.form.get('cohort1', '').strip()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('UPDATE students SET password=?, email=?, firstname=?, lastname=?, cohort1=? WHERE username=?',
                         (password, email, firstname, lastname, cohort1, username))
            log_action(
                'student_updated',
                'student',
                username,
                f"cohort1={cohort1}; backup={os.path.basename(backup_path) if backup_path else ''}",
                conn
            )
        flash('Данные обновлены')
        return redirect(url_for('students_list'))
    return render_template('edit_student.html', student=student)

@app.route('/delete_student', methods=['POST'])
@login_required
@role_required('admin')
def delete_student():
    username = request.form.get('username')
    if username:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                '''
                SELECT username, email, firstname, lastname, source_campaign_year, source_dogovor, source_fio
                FROM students
                WHERE username=?
                ''',
                (username,)
            )
            student = cur.fetchone()
            if not student:
                flash('Студент не найден')
                return redirect(url_for('students_list'))

            username, email, firstname, lastname, source_campaign_year, source_dogovor, source_fio = student
            if source_campaign_year and is_campaign_archived(source_campaign_year):
                flash(ARCHIVED_CAMPAIGN_MESSAGE, 'error')
                return redirect(url_for('students_list'))

            backup_path = create_database_backup('before_delete_student')
            if source_campaign_year:
                campaign_year = normalize_campaign_year(source_campaign_year, source_campaign_year)
                fio = source_fio or ' '.join(part for part in [lastname, firstname] if part).strip()
                _, fallback_fam, fallback_imotch = split_fio(fio)
                fam = lastname or fallback_fam
                imotch = firstname or fallback_imotch

                abiturient_exists = conn.execute(
                    'SELECT 1 FROM abiturients WHERE login=? AND campaign_year=?',
                    (username, campaign_year)
                ).fetchone()
                if abiturient_exists:
                    flash(f'Абитуриент {username} уже есть в кампании {campaign_year}')
                else:
                    conn.execute(
                        '''
                        INSERT INTO abiturients (fio, dogovor, login, campaign_year, fam, imotch, email)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''',
                        (fio, source_dogovor or '', username, campaign_year, fam, imotch, email)
                    )
                    flash(f'Студент {username} возвращен в абитуриенты кампании {campaign_year}')

            conn.execute('DELETE FROM students WHERE username=?', (username,))
            log_action(
                'student_deleted',
                'student',
                username,
                f"backup={os.path.basename(backup_path) if backup_path else ''}",
                conn
            )
            flash('Студент удален')
    return redirect(url_for('students_list'))

@app.route('/abiturients_to_students', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'assistant')
def abiturients_to_students():
    campaign_year = get_active_campaign_year()
    group_year = normalize_group_year(request.values.get('group_year'), campaign_year)
    group_years = get_group_years(group_year)
    with sqlite3.connect(DB_PATH) as conn:
        # Получаем список групп из таблицы groups
        groups = get_groups_with_counts(conn, group_year)
        # Получаем список абитуриентов
        cur = conn.execute(
            'SELECT id, fio, login, fam, imotch, email, paid FROM abiturients WHERE campaign_year=? ORDER BY fio',
            (campaign_year,)
        )
        abiturients = [
            {
                'id': row[0],
                'fio': row[1],
                'login': row[2],
                'lastname': row[3],
                'firstname': row[4],
                'email': (row[5] or '').strip(),
                'paid': bool(row[6]),
                'has_email': bool((row[5] or '').strip()),
            }
            for row in cur.fetchall()
        ]
    if request.method == 'POST':
        if not ensure_campaign_open(campaign_year):
            return redirect(url_for('abiturients_to_students', group_year=group_year), code=303)
        cohort1 = request.form.get('cohort1', '').strip()
        ids = request.form.getlist('abiturient_ids')
        auto_split = request.form.get('auto_split') == '1'
        if not cohort1 or not ids:
            flash('Выберите группу и хотя бы одного абитуриента')
            return redirect(url_for('abiturients_to_students', group_year=group_year))
        with sqlite3.connect(DB_PATH) as conn:
            group_exists = conn.execute(
                'SELECT 1 FROM groups WHERE name=? AND group_year=? AND COALESCE(is_hidden, 0)=0',
                (cohort1, group_year)
            ).fetchone()
            if not group_exists:
                flash('Выберите видимую группу из справочника академических групп')
                return redirect(url_for('abiturients_to_students', group_year=group_year))

            skipped_without_email = []
            skipped_duplicates = []
            selected_abiturients = []
            for ab_id in ids:
                cur = conn.execute(
                    'SELECT fio, dogovor, login, fam, imotch, email, paid FROM abiturients WHERE id=? AND campaign_year=?',
                    (ab_id, campaign_year)
                )
                ab = cur.fetchone()
                if ab:
                    fio, dogovor, username, lastname, firstname, email, paid = ab
                    email = (email or '').strip()
                    if not email or not paid:
                        skipped_without_email.append(username or fio or str(ab_id))
                        continue

                    student_exists = conn.execute('SELECT 1 FROM students WHERE username=?', (username,)).fetchone()
                    if student_exists:
                        skipped_duplicates.append(username)
                        continue

                    selected_abiturients.append((ab_id, username, email, firstname, lastname, fio, dogovor))

            current_count = get_group_student_count(conn, cohort1)
            free_places = MAX_GROUP_STUDENTS - current_count
            if selected_abiturients and len(selected_abiturients) > free_places and not auto_split:
                next_group = get_next_subgroup_name(conn, cohort1, group_year)
                flash(f'В группе {cohort1} свободно мест: {max(free_places, 0)}/{MAX_GROUP_STUDENTS}. Создайте или выберите следующую подгруппу: {next_group}')
                if skipped_without_email:
                    names = ', '.join(skipped_without_email[:10])
                    suffix = '...' if len(skipped_without_email) > 10 else ''
                    flash(f'Не перенесены без почты: {names}{suffix}')
                if skipped_duplicates:
                    names = ', '.join(skipped_duplicates[:10])
                    suffix = '...' if len(skipped_duplicates) > 10 else ''
                    flash(f'Не перенесены, уже есть в студентах: {names}{suffix}')
                return redirect(url_for('abiturients_to_students', group_year=group_year))

            backup_path = create_database_backup('before_abiturients_migration') if selected_abiturients else None
            migrated_count = 0
            next_group_after_full = ''
            assignments = []
            if auto_split:
                remaining_abiturients = list(selected_abiturients)
                target_group = cohort1
                while remaining_abiturients:
                    target_count = get_group_student_count(conn, target_group)
                    free_in_group = MAX_GROUP_STUDENTS - target_count
                    if free_in_group <= 0:
                        target_group = get_next_subgroup_name(conn, target_group, group_year)
                        conn.execute(
                            'INSERT OR IGNORE INTO groups (name, group_year) VALUES (?, ?)',
                            (target_group, group_year)
                        )
                        continue
                    current_batch = remaining_abiturients[:free_in_group]
                    remaining_abiturients = remaining_abiturients[free_in_group:]
                    assignments.extend((abiturient, target_group) for abiturient in current_batch)
                    if remaining_abiturients:
                        next_group_name = get_next_subgroup_name(conn, target_group, group_year)
                        conn.execute(
                            'INSERT OR IGNORE INTO groups (name, group_year) VALUES (?, ?)',
                            (next_group_name, group_year)
                        )
                        target_group = next_group_name
            else:
                assignments = [(abiturient, cohort1) for abiturient in selected_abiturients]

            touched_groups = set()
            for abiturient, target_group in assignments:
                ab_id, username, email, firstname, lastname, fio, dogovor = abiturient
                conn.execute(
                    '''
                    INSERT INTO students
                        (username, password, email, firstname, lastname, cohort1, source_campaign_year, source_dogovor, source_fio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (username, 'cron', email, firstname, lastname, target_group, campaign_year, dogovor, fio)
                )
                conn.execute('DELETE FROM abiturients WHERE id=? AND campaign_year=?', (ab_id, campaign_year))
                migrated_count += 1
                touched_groups.add(target_group)
            if get_group_student_count(conn, cohort1) >= MAX_GROUP_STUDENTS:
                next_group_after_full = get_next_subgroup_name(conn, cohort1, group_year)
            if migrated_count:
                log_action(
                    'abiturients_migrated_to_students',
                    'group',
                    cohort1,
                    (
                        f"campaign_year={campaign_year}; group_year={group_year}; "
                        f"count={migrated_count}; auto_split={int(auto_split)}; "
                        f"groups={','.join(sorted(touched_groups))}; backup={os.path.basename(backup_path) if backup_path else ''}"
                    ),
                    conn
                )

        if migrated_count:
            flash(f'Мигрировано студентов: {migrated_count}')
            if auto_split:
                flash('Автораспределение по подгруппам выполнено.')
            else:
                flash(f'Группа {cohort1}: {current_count + migrated_count}/{MAX_GROUP_STUDENTS}')
        if next_group_after_full:
            flash(f'Группа {cohort1} заполнена. Следующая подгруппа: {next_group_after_full}')
        if skipped_without_email:
            names = ', '.join(skipped_without_email[:10])
            suffix = '...' if len(skipped_without_email) > 10 else ''
            flash(f'Не перенесены без почты: {names}{suffix}')
        if skipped_duplicates:
            names = ', '.join(skipped_duplicates[:10])
            suffix = '...' if len(skipped_duplicates) > 10 else ''
            flash(f'Не перенесены, уже есть в студентах: {names}{suffix}')
        if not migrated_count and not skipped_without_email and not skipped_duplicates:
            flash('Не удалось найти выбранных абитуриентов для текущей кампании')

        target = 'students_list' if migrated_count and not skipped_without_email and not skipped_duplicates else 'abiturients_to_students'
        if target == 'abiturients_to_students':
            return redirect(url_for(target, group_year=group_year))
        return redirect(url_for(target))
    return render_template(
        'abiturients_to_students.html',
        abiturients=abiturients,
        groups=groups,
        campaign_year=campaign_year,
        group_year=group_year,
        group_years=group_years,
    )

@app.route('/migration_wizard')
@login_required
@role_required('admin', 'assistant')
def migration_wizard():
    campaign_year = get_active_campaign_year()
    group_year = normalize_group_year(request.args.get('group_year'), campaign_year)
    dashboard = get_dashboard_data(campaign_year)
    with sqlite3.connect(DB_PATH) as conn:
        groups = get_groups_with_counts(conn, group_year)
    return render_template(
        'migration_wizard.html',
        dashboard=dashboard,
        groups=groups,
        group_year=group_year,
        group_years=get_group_years(group_year),
    )

@app.route('/add_group', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def add_group():
    group_year = normalize_group_year(request.values.get('group_year'), get_active_campaign_year())
    show_hidden = request.values.get('show_hidden') == '1'

    def add_group_url():
        args = {'group_year': group_year}
        if show_hidden:
            args['show_hidden'] = '1'
        return url_for('add_group', **args)

    if request.method == 'POST':
        group_action = request.form.get('group_action', '').strip()
        if group_action == 'force_subgroup':
            source_group = normalize_group_name(request.form.get('source_group', ''))
            if not source_group:
                flash('Выберите исходную группу для новой подгруппы')
                return redirect(add_group_url())

            with sqlite3.connect(DB_PATH) as conn:
                source_exists = conn.execute(
                    'SELECT 1 FROM groups WHERE name=? AND group_year=? AND COALESCE(is_hidden, 0)=0',
                    (source_group, group_year)
                ).fetchone()
                if not source_exists:
                    flash('Исходная группа не найдена или скрыта')
                    return redirect(add_group_url())

                next_group = get_next_subgroup_name(conn, source_group, group_year)
                if infer_group_year(next_group, group_year) != group_year:
                    flash(f'Для папки {group_year} новая подгруппа должна начинаться с {group_year[-2:]}')
                    return redirect(add_group_url())

                existing = {
                    row[0].casefold(): row[0]
                    for row in conn.execute('SELECT name FROM groups')
                }
                if group_exists_casefold(existing, next_group):
                    flash(f'Подгруппа {next_group} уже существует')
                    return redirect(add_group_url())

                conn.execute(
                    'INSERT INTO groups (name, group_year) VALUES (?, ?)',
                    (next_group, group_year)
                )
                flash(f'Принудительно создана подгруппа {next_group} для {source_group}')
            return redirect(add_group_url())

        if group_action in ('hide', 'show', 'delete'):
            group_name = normalize_group_name(request.form.get('group_name', ''))
            if not group_name:
                flash('Выберите группу')
                return redirect(add_group_url())

            with sqlite3.connect(DB_PATH) as conn:
                group_row = conn.execute(
                    'SELECT name FROM groups WHERE name=? AND group_year=?',
                    (group_name, group_year)
                ).fetchone()
                if not group_row:
                    flash('Группа не найдена в выбранной папке')
                    return redirect(add_group_url())

                if group_action == 'hide':
                    conn.execute(
                        'UPDATE groups SET is_hidden=1 WHERE name=? AND group_year=?',
                        (group_name, group_year)
                    )
                    flash(f'Группа {group_name} скрыта')
                elif group_action == 'show':
                    conn.execute(
                        'UPDATE groups SET is_hidden=0 WHERE name=? AND group_year=?',
                        (group_name, group_year)
                    )
                    flash(f'Группа {group_name} снова отображается')
                elif group_action == 'delete':
                    student_count = get_group_student_count(conn, group_name)
                    if student_count:
                        flash(f'Нельзя удалить группу {group_name}: в ней есть студенты ({student_count}). Можно скрыть группу.')
                    else:
                        conn.execute(
                            'DELETE FROM groups WHERE name=? AND group_year=?',
                            (group_name, group_year)
                        )
                        flash(f'Группа {group_name} удалена')
            return redirect(add_group_url())

        groups_file = request.files.get('groups_file')
        if groups_file and groups_file.filename:
            filepath = None
            if get_upload_extension(groups_file) not in GROUPS_UPLOAD_EXTENSIONS:
                flash('Загрузите файл групп в формате CSV')
                return redirect(add_group_url())

            filepath = save_upload_to_temp(groups_file, GROUPS_UPLOAD_EXTENSIONS)
            try:
                result = process_groups_csv(filepath, group_year)
            except Exception as exc:
                flash(f'Ошибка загрузки групп: {exc}')
                return redirect(add_group_url())

            finally:
                cleanup_temp_files(filepath)

            if result['created']:
                flash(f'Добавлено групп: {len(result["created"])}')
            if result['skipped']:
                flash(f'Пропущено дублей: {len(result["skipped"])}')
            if result['errors']:
                errors = '; '.join(result['errors'][:5])
                suffix = '...' if len(result['errors']) > 5 else ''
                flash(f'Ошибки в CSV: {errors}{suffix}')
            if not result['created'] and not result['errors']:
                flash('Новые группы не добавлены')
            return redirect(add_group_url())

        source_group = normalize_group_name(request.form.get('source_group', ''))
        group_name = normalize_group_name(request.form.get('group_name', ''))
        if group_name:
            if not is_valid_group_name(group_name):
                flash('Название группы должно быть в формате 26ФМ-11-1')
                return redirect(add_group_url())
            if infer_group_year(group_name, group_year) != group_year:
                flash(f'Для папки {group_year} название группы должно начинаться с {group_year[-2:]}')
                return redirect(add_group_url())
            with sqlite3.connect(DB_PATH) as conn:
                if source_group:
                    source_exists = conn.execute(
                        'SELECT 1 FROM groups WHERE name=? AND group_year=? AND COALESCE(is_hidden, 0)=0',
                        (source_group, group_year)
                    ).fetchone()
                    source_count = get_group_student_count(conn, source_group) if source_exists else 0
                    expected_group = get_next_subgroup_name(conn, source_group, group_year) if source_exists else ''
                    if not source_exists or source_count < MAX_GROUP_STUDENTS or not is_last_subgroup(conn, source_group, group_year) or group_name != expected_group:
                        flash('Дополнительную подгруппу можно создать только для последней заполненной группы')
                        return redirect(add_group_url())

                existing = {
                    row[0].casefold(): row[0]
                    for row in conn.execute('SELECT name FROM groups')
                }
                if group_exists_casefold(existing, group_name):
                    flash('Такая группа уже существует')
                    return redirect(add_group_url())
                try:
                    conn.execute('INSERT INTO groups (name, group_year) VALUES (?, ?)', (group_name, group_year))
                    if source_group:
                        flash(f'Создана дополнительная подгруппа {group_name} для {source_group}')
                    else:
                        flash('Группа добавлена')
                except sqlite3.IntegrityError:
                    flash('Такая группа уже существует')
        else:
            flash('Название группы не может быть пустым')
        return redirect(add_group_url())
    # Список всех групп для отображения
    with sqlite3.connect(DB_PATH) as conn:
        groups = get_groups_with_counts(conn, group_year, include_hidden=show_hidden)
    visible_groups = [group for group in groups if not group['is_hidden']]
    group_years = get_group_years(group_year, include_base=True)
    return render_template(
        'add_group.html',
        groups=groups,
        visible_groups=visible_groups,
        group_year=group_year,
        group_years=group_years,
        group_year_code=group_year[-2:],
        show_hidden=show_hidden,
    )

@app.route('/groups_template/download')
@login_required
@role_required('admin')
def download_groups_template():
    group_year = normalize_group_year(request.args.get('group_year'), get_active_campaign_year())
    output = io.BytesIO(build_groups_template_csv(group_year).encode('utf-8-sig'))
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='groups_template.csv', mimetype='text/csv')

if __name__ == "__main__":
    app_host = os.environ.get("APP_HOST", "127.0.0.1")
    app_port = int(os.environ.get("APP_PORT", "5000"))
    app_debug = os.environ.get("APP_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    app.run(host=app_host, port=app_port, debug=app_debug)
