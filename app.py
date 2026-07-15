import os
import re
import csv
import pandas as pd
from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash, has_request_context
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import io
import hmac
from functools import wraps
from datetime import date
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return False

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32).hex()
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_FILENAME = os.environ.get('DB_FILENAME', 'baze.db')
DB_PATH = os.path.join(app.config['UPLOAD_FOLDER'], DB_FILENAME)

_campaign_year_re = re.compile(r'^20\d{2}$')
_dogovor_year_re = re.compile(r'20\d{2}')

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
    "11и": "11i", "9и": "9i",
    "11И": "11i", "9И": "9i",
    "11": "11", "9": "9", 
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

def normalize_dogovor_text(dogovor):
    normalized = str(dogovor or '').strip()
    normalized = _dogovor_dash_re.sub('-', normalized).replace(' ', '-')
    return normalized.upper().translate(_dogovor_latin_lookalikes)

def parse_dogovor(dogovor):
    # Нормализуем дефисы, пробелы и регистр
    normalized = normalize_dogovor_text(dogovor)
    year_match = re.search(r'20\d{2}', normalized)
    spec_match = None

    # Определяем специальность - ищем в нормализованной строке
    for spec in sorted(spec_codes.keys(), key=len, reverse=True):
        if spec.upper() in normalized:
            spec_match = spec
            break

    # Определяем базу образования по последнему элементу после дефиса
    base_match = None
    parts = normalized.split('-')
    if len(parts) >= 2:
        last_part = parts[-1].strip()
        # Проверяем по словарю base_codes
        for base in sorted(base_codes.keys(), key=len, reverse=True):
            if last_part == base.upper():
                base_match = base
                break

    if not (year_match and spec_match and base_match):
        return "error"

    year_code = year_match.group()[-2:]
    spec_code = spec_codes[spec_match]
    base_code = base_codes[base_match]

    return f"{year_code}{spec_code}{base_code}"

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
        conn.execute('CREATE INDEX IF NOT EXISTS idx_abiturients_campaign_year ON abiturients (campaign_year)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pending_duplicates_campaign_year ON pending_duplicates (campaign_year)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_login_conflicts_campaign_year ON login_conflicts (campaign_year)')

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

def get_used_logins(campaign_year):
    used_logins = set()
    with sqlite3.connect(DB_PATH) as conn:
        for table in ('abiturients', 'pending_duplicates', 'login_conflicts'):
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

def get_prefixed_logins(table, prefix, campaign_year):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            f"SELECT login FROM {table} WHERE campaign_year=? AND login LIKE ?",
            (campaign_year, f'{prefix}%')
        )
        return set(row[0] for row in cur.fetchall())

def next_numbered_login(prefix, existing_logins):
    number = 1
    while True:
        login = f"{prefix}{number:03d}"
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
    with sqlite3.connect(DB_PATH) as conn:
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
    campaign_year = normalize_campaign_year(campaign_year, infer_campaign_year(dogovor))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO pending_duplicates (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
            (fio, dogovor, login, campaign_year, fam, imotch)
        )
        conn.commit()

def save_login_conflict(fio, dogovor, login, fam, imotch, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, infer_campaign_year(dogovor))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO login_conflicts (fio, dogovor, login, campaign_year, fam, imotch) VALUES (?, ?, ?, ?, ?, ?)',
            (fio, dogovor, login, campaign_year, fam, imotch)
        )
        conn.commit()

def process_excel(file_path, campaign_year=None):
    campaign_year = normalize_campaign_year(campaign_year, get_active_campaign_year())
    df = pd.read_excel(file_path, engine="openpyxl")
    fio_split = df["ФИО"].str.split(' ', n=2, expand=True)
    df["Фамилия"] = fio_split[0]
    df["Имя_Отчество"] = fio_split[1] + fio_split[2].apply(lambda x: f' {x}' if pd.notnull(x) else '')

    df["login_prefix"] = df["Договор"].apply(parse_dogovor)

    logins = []
    used_logins = get_used_logins(campaign_year)
    error_count = 1

    for idx, row in df.iterrows():
        prefix = row["login_prefix"]
        if prefix == "error":
            login = f"error{error_count:03d}"
            while login in used_logins:
                error_count += 1
                login = f"error{error_count:03d}"
            logins.append(login)
            used_logins.add(login)
            error_count += 1
            continue
        number = 1
        while True:
            login = f"{prefix}{number:03d}"
            if login not in used_logins:
                logins.append(login)
                used_logins.add(login)
                break
            number += 1
    df["login"] = logins
    df["campaign_year"] = campaign_year

    df["is_duplicate"] = df["Фамилия"].apply(lambda fam: bool(is_fio_duplicate(fam, campaign_year)))

    for idx, row in df.iterrows():
        if row["login_prefix"] == "error":
            save_login_conflict(row["ФИО"], row["Договор"], row["login"], row["Фамилия"], row["Имя_Отчество"], campaign_year)
            continue

        if not row["is_duplicate"]:
            try:
                save_abiturient(row["ФИО"], row["Договор"], row["login"], row["Фамилия"], row["Имя_Отчество"], campaign_year)
            except sqlite3.IntegrityError:
                save_login_conflict(row["ФИО"], row["Договор"], row["login"], row["Фамилия"], row["Имя_Отчество"], campaign_year)
                continue
        else:
            dubl_logins = get_prefixed_logins('pending_duplicates', 'dubl', campaign_year)
            dubl_login = next_numbered_login('dubl', dubl_logins)
            save_pending_duplicate(row["ФИО"], row["Договор"], dubl_login, row["Фамилия"], row["Имя_Отчество"], campaign_year)

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], "abiturients_with_logins.xlsx")
    df[["campaign_year", "ФИО", "Договор", "login", "Фамилия", "Имя_Отчество", "is_duplicate"]].to_excel(output_path, index=False)
    return output_path

def process_students_excel(file_path):
    import pandas as pd
    if file_path.lower().endswith('.csv'):
        df = pd.read_csv(file_path, sep=';')
    else:
        df = pd.read_excel(file_path, engine="openpyxl")
    required_cols = {"username", "password", "email", "firstname", "lastname", "cohort1"}
    if not required_cols.issubset(df.columns):
        raise ValueError("В файле отсутствуют необходимые столбцы")
    with sqlite3.connect(DB_PATH) as conn:
        for _, row in df.iterrows():
            username = row["username"]
            cur = conn.execute('SELECT 1 FROM students WHERE username=?', (username,))
            if cur.fetchone():
                # Дубликат — добавляем в students_duplicates
                conn.execute(
                    '''INSERT INTO students_duplicates (username, password, email, firstname, lastname, cohort1)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (row["username"], row["password"], row["email"], row["firstname"], row["lastname"], row["cohort1"])
                )
            else:
                # Нет дубля — добавляем в students
                conn.execute(
                    '''INSERT INTO students (username, password, email, firstname, lastname, cohort1)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (row["username"], row["password"], row["email"], row["firstname"], row["lastname"], row["cohort1"])
                )
    return True

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
            elif action == 'reject':
                conn.execute('DELETE FROM users WHERE id=?', (user_id,))
        cur = conn.execute('SELECT id, username, role FROM users WHERE approved=0')
        pending_users = cur.fetchall()
    return render_template('approve_users.html', pending_users=pending_users)

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    campaign_year = get_active_campaign_year()
    if request.method == 'POST':
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        result_path = process_excel(filepath, campaign_year)
        return send_file(result_path, as_attachment=True)
    return render_template('index.html')

@app.route('/file_work', methods=['GET', 'POST'])
@login_required
def file_work():
    campaign_year = get_active_campaign_year()
    if request.method == 'POST':
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        result_path = process_excel(filepath, campaign_year)
        return send_file(result_path, as_attachment=True)
    return render_template('file_work.html', campaign_year=campaign_year)

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
    abiturients = get_all_abiturients(order_by, order_dir, spec, base, year, is_i, campaign_year, has_email, has_paid)
    specs = list(spec_codes.keys())
    bases = list(base_codes.keys())
    years = get_campaign_years()
    return render_template('abiturients.html', abiturients=abiturients, order_by=order_by, order_dir=order_dir, specs=specs, bases=bases, years=years, campaign_year=campaign_year)

def get_all_abiturients(order_by='created_at', order_dir='desc', spec=None, base=None, year=None, is_i=None, campaign_year=None, has_email=None, has_paid=None):
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
        query += " AND dogovor LIKE ?"
        params.append(f"%{base}%")
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
                if not user or user[0] not in allowed_roles or user[1] != 1:
                    flash('Недостаточно прав')
                    return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/duplicates_abiturients', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def duplicates_abiturients():
    campaign_year = get_active_campaign_year()
    if request.method == 'POST':
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
    abiturient_id = request.form.get('id')
    login = request.form.get('login')
    if abiturient_id:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('DELETE FROM abiturients WHERE id=? AND campaign_year=?', (abiturient_id, campaign_year))
    elif login:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('DELETE FROM abiturients WHERE login=? AND campaign_year=?', (login, campaign_year))
    return redirect(url_for('abiturients'))

@app.route('/toggle_abiturient_paid', methods=['POST'])
@login_required
@role_required('admin')
def toggle_abiturient_paid():
    campaign_year = get_active_campaign_year()
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
    abiturients = get_all_abiturients(order_by, order_dir, spec, base, year, is_i, campaign_year, has_email)
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
        new_login = request.form.get('login', '').strip()
        if not new_login:
            flash('Логин не может быть пустым')
            return redirect(url_for('edit_conflict', conflict_id=conflict_id))
        
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM login_conflicts WHERE id=? AND campaign_year=?', (conflict_id, campaign_year))
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
    specs = list(spec_codes.keys())
    bases = list(base_codes.keys())
    if request.method == 'POST':
        year = normalize_campaign_year(request.form.get('year'), campaign_year)
        campaign_year = year
        session['campaign_year'] = campaign_year
        spec = request.form.get('spec')
        base = request.form.get('base')
        fio = request.form.get('fio').strip()
        fam, imotch = fio.split(' ', 1) if ' ' in fio else (fio, '')
        dogovor = f"{year} {spec} {base}"

        prefix = parse_dogovor(dogovor)
        if prefix == "error":
            # Сохраняем ошибочный логин в конфликты
            error_login = next_numbered_login('error', get_used_logins(campaign_year))
            save_login_conflict(fio, dogovor, error_login, fam, imotch, campaign_year)
            message = f"Ошибка парсинга договора! Запись отправлена в раздел 'Конфликты логинов' с логином {error_login}."
        else:
            existing_logins = get_used_logins(campaign_year)

            number = 1
            while True:
                login = f"{prefix}{number:03d}"
                if login not in existing_logins:
                    break
                number += 1

            if is_fio_duplicate(fam, campaign_year):
                dubl_logins = get_prefixed_logins('pending_duplicates', 'dubl', campaign_year)
                dubl_login = next_numbered_login('dubl', dubl_logins)
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
        return render_template('manual_create.html', message=message, conflict_info=conflict_info, years=years, specs=specs, bases=bases, campaign_year=campaign_year)

    return render_template('manual_create.html', years=years, specs=specs, bases=bases, campaign_year=campaign_year)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
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
                session['user'] = username
                session['role'] = user[2]
                return redirect(url_for('index'))
            elif password_ok and user[3] == 0:
                flash('Ожидайте одобрения администратора')
            else:
                flash('Неверный логин или пароль')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
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
        fio, fam, imotch = split_fio(request.form.get('fio', ''))
        if not fio:
            flash('ФИО не может быть пустым')
            return render_template('edit_abiturient.html', abiturient=abiturient)
        email = request.form.get('email', '').strip()
        paid = 1 if request.form.get('paid') == '1' else 0
        new_login = request.form.get('login', '').strip()
        comment = request.form.get('comment', '').strip()
        if new_login != login:
            if is_login_exists(new_login, campaign_year):
                flash('Такой логин уже существует!')
                return render_template('edit_abiturient.html', abiturient=abiturient)
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    'UPDATE abiturients SET fio=?, fam=?, imotch=?, email=?, paid=?, login=?, comment=? WHERE login=? AND campaign_year=?',
                    (fio, fam, imotch, email, paid, new_login, comment, login, campaign_year)
                )
        else:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    'UPDATE abiturients SET fio=?, fam=?, imotch=?, email=?, paid=?, comment=? WHERE login=? AND campaign_year=?',
                    (fio, fam, imotch, email, paid, comment, login, campaign_year)
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

@app.route('/admin_panel')
@admin_required
def admin_panel():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT id, username, role, approved FROM users')
        all_users = cur.fetchall()
    return render_template('admin_panel.html', all_users=all_users)

@app.route('/delete_user', methods=['POST'])
@admin_required
def delete_user():
    user_id = request.form.get('user_id')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM users WHERE id=?', (user_id,))
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
        flash('Пользователь успешно добавлен!')
        return redirect(url_for('admin_panel'))
    return render_template('add_user.html')

@app.route('/clear_abiturients', methods=['POST'])
@admin_required
def clear_abiturients():
    campaign_year = get_active_campaign_year()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM abiturients WHERE campaign_year=?', (campaign_year,))
        conn.execute('DELETE FROM pending_duplicates WHERE campaign_year=?', (campaign_year,))
        conn.execute('DELETE FROM login_conflicts WHERE campaign_year=?', (campaign_year,))
    flash(f'Абитуриенты, дубли и конфликты кампании {campaign_year} успешно очищены.', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/students_upload', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def students_upload():
    message = None
    if request.method == 'POST':
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        try:
            process_students_excel(filepath)
            message = "Студенты успешно добавлены!"
        except Exception as e:
            message = f"Ошибка: {e}"
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
    df = pd.DataFrame(students)
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
        password = request.form.get('password', '').strip()
        email = request.form.get('email', '').strip()
        firstname = request.form.get('firstname', '').strip()
        lastname = request.form.get('lastname', '').strip()
        cohort1 = request.form.get('cohort1', '').strip()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('UPDATE students SET password=?, email=?, firstname=?, lastname=?, cohort1=? WHERE username=?',
                         (password, email, firstname, lastname, cohort1, username))
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
        cohort1 = request.form.get('cohort1', '').strip()
        ids = request.form.getlist('abiturient_ids')
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
            if selected_abiturients and len(selected_abiturients) > free_places:
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

            migrated_count = 0
            next_group_after_full = ''
            for ab_id, username, email, firstname, lastname, fio, dogovor in selected_abiturients:
                conn.execute(
                    '''
                    INSERT INTO students
                        (username, password, email, firstname, lastname, cohort1, source_campaign_year, source_dogovor, source_fio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (username, 'cron', email, firstname, lastname, cohort1, campaign_year, dogovor, fio)
                )
                conn.execute('DELETE FROM abiturients WHERE id=? AND campaign_year=?', (ab_id, campaign_year))
                migrated_count += 1
            if current_count + migrated_count >= MAX_GROUP_STUDENTS:
                next_group_after_full = get_next_subgroup_name(conn, cohort1, group_year)

        if migrated_count:
            flash(f'Мигрировано студентов: {migrated_count}')
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
            filename = secure_filename(groups_file.filename) or 'groups_upload.csv'
            if not filename.lower().endswith('.csv'):
                flash('Загрузите файл групп в формате CSV')
                return redirect(add_group_url())

            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            groups_file.save(filepath)
            try:
                result = process_groups_csv(filepath, group_year)
            except Exception as exc:
                flash(f'Ошибка загрузки групп: {exc}')
                return redirect(add_group_url())

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
