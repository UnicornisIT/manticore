import os
import re
import pandas as pd
from flask import Flask, render_template, request, send_file, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import sqlite3
import io
from functools import wraps
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_FILENAME = os.environ.get('DB_FILENAME', 'baze.db')
DB_PATH = os.path.join(app.config['UPLOAD_FOLDER'], DB_FILENAME)

spec_codes = {
    "ЛД": "1", "АД": "2", "СД": "3", "СтО": "4",
    "СтПр": "5", "ФМ": "6", "ЛабД": "7", "СтД": "8"
}

base_codes = {
    "2НМ": "inm", "2М": "im",
    "НМ": "nm", "М": "m",
    "11и": "11i", "9и": "9i",
    "11И": "11i", "9И": "9i",
    "11": "11", "9": "9", 
}

def parse_dogovor(dogovor):
    year_match = re.search(r'20\d{2}', dogovor)
    spec_match = None

    # Определяем специальность
    for spec in sorted(spec_codes.keys(), key=len, reverse=True):
        if spec in dogovor:
            spec_match = spec
            break

    # Определяем базу образования по последнему дефису
    base_match = None
    parts = dogovor.strip().split('-')
    if len(parts) >= 2:
        last_part = parts[-1].strip()
        # Проверяем по словарю base_codes
        for base in sorted(base_codes.keys(), key=len, reverse=True):
            if last_part == base:
                base_match = base
                break

    if not (year_match and spec_match and base_match):
        return "error"

    year_code = year_match.group()[-2:]
    spec_code = spec_codes[spec_match]
    base_code = base_codes[base_match]

    return f"{year_code}{spec_code}{base_code}"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS abiturients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fio TEXT,
                dogovor TEXT,
                login TEXT UNIQUE,
                fam TEXT,
                imotch TEXT,
                email TEXT,
                comment TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS pending_duplicates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fio TEXT,
                dogovor TEXT,
                login TEXT,
                fam TEXT,
                imotch TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS login_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fio TEXT,
                dogovor TEXT,
                login TEXT,
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
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                email TEXT,
                firstname TEXT,
                lastname TEXT,
                cohort1 TEXT
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE
            )
        ''')

init_db()

# Initialize default admin user (CHANGE PASSWORD AFTER FIRST LOGIN!)
_default_admin_password = os.environ.get('ADMIN_DEFAULT_PASSWORD', 'admin123')
with sqlite3.connect(DB_PATH) as conn:
    conn.execute("INSERT OR IGNORE INTO users (username, password, role, approved) VALUES (?, ?, ?, ?)", ("admin", _default_admin_password, "admin", 1))

def is_login_exists(login):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT 1 FROM abiturients WHERE login=?', (login,))
        return cur.fetchone() is not None

def is_fio_duplicate(fam):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT fio FROM abiturients WHERE fam=?', (fam,))
        return cur.fetchall()

def save_abiturient(fio, dogovor, login, fam, imotch):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO abiturients (fio, dogovor, login, fam, imotch) VALUES (?, ?, ?, ?, ?)',
            (fio, dogovor, login, fam, imotch)
        )

def save_pending_duplicate(fio, dogovor, login, fam, imotch):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO pending_duplicates (fio, dogovor, login, fam, imotch) VALUES (?, ?, ?, ?, ?)',
            (fio, dogovor, login, fam, imotch)
        )

def save_login_conflict(fio, dogovor, login, fam, imotch):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO login_conflicts (fio, dogovor, login, fam, imotch) VALUES (?, ?, ?, ?, ?)',
            (fio, dogovor, login, fam, imotch)
        )

def process_excel(file_path):
    df = pd.read_excel(file_path, engine="openpyxl")
    fio_split = df["ФИО"].str.split(' ', n=2, expand=True)
    df["Фамилия"] = fio_split[0]
    df["Имя_Отчество"] = fio_split[1] + fio_split[2].apply(lambda x: f' {x}' if pd.notnull(x) else '')

    df["login_prefix"] = df["Договор"].apply(parse_dogovor)

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT login FROM abiturients')
        existing_logins = set(row[0] for row in cur.fetchall())

    logins = []
    used_logins = set(existing_logins)

    for idx, row in df.iterrows():
        prefix = row["login_prefix"]
        number = 1
        while True:
            login = f"{prefix}{number:03d}"
            if login not in used_logins:
                logins.append(login)
                used_logins.add(login)
                break
            number += 1
    df["login"] = logins

    df["is_duplicate"] = df["Фамилия"].apply(lambda fam: bool(is_fio_duplicate(fam)))

    for idx, row in df.iterrows():
        if not row["is_duplicate"]:
            try:
                save_abiturient(row["ФИО"], row["Договор"], row["login"], row["Фамилия"], row["Имя_Отчество"])
            except sqlite3.IntegrityError:
                save_login_conflict(row["ФИО"], row["Договор"], row["login"], row["Фамилия"], row["Имя_Отчество"])
                continue
        else:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute('SELECT login FROM pending_duplicates WHERE login LIKE "dubl%"')
                dubl_logins = set(row[0] for row in cur.fetchall())
            dubl_number = 1
            while True:
                dubl_login = f"dubl{dubl_number:03d}"
                if dubl_login not in dubl_logins:
                    break
                dubl_number += 1
            save_pending_duplicate(row["ФИО"], row["Договор"], dubl_login, row["Фамилия"], row["Имя_Отчество"])

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], "abiturients_with_logins.xlsx")
    df[["ФИО", "Договор", "login", "Фамилия", "Имя_Отчество", "is_duplicate"]].to_excel(output_path, index=False)
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

@app.route('/approve_users', methods=['GET', 'POST'])
@vaanedain_required
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
    if request.method == 'POST':
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        result_path = process_excel(filepath)
        return send_file(result_path, as_attachment=True)
    return render_template('index.html')

@app.route('/file_work', methods=['GET', 'POST'])
@login_required
def file_work():
    if request.method == 'POST':
        file = request.files['file']
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        result_path = process_excel(filepath)
        return send_file(result_path, as_attachment=True)
    return render_template('file_work.html')

@app.route('/abiturients')
@login_required
def abiturients():
    order_by = request.args.get('order_by', 'created_at')
    order_dir = request.args.get('order_dir', 'desc')
    spec = request.args.get('spec')
    base = request.args.get('base')
    year = request.args.get('year')
    is_i = request.args.get('is_i')
    abiturients = get_all_abiturients(order_by, order_dir, spec, base, year, is_i)
    specs = list(spec_codes.keys())
    bases = list(base_codes.keys())
    years = [str(y) for y in range(2020, 2031)]
    return render_template('abiturients.html', abiturients=abiturients, order_by=order_by, order_dir=order_dir, specs=specs, bases=bases, years=years)

def get_all_abiturients(order_by='created_at', order_dir='desc', spec=None, base=None, year=None, is_i=None):
    valid_columns = {'id', 'fio', 'dogovor', 'login', 'fam', 'imotch', 'created_at', 'email'}
    if order_by not in valid_columns:
        order_by = 'created_at'
    if order_dir.lower() not in {'asc', 'desc'}:
        order_dir = 'desc'
    query = "SELECT * FROM abiturients WHERE 1=1"
    params = []
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
    query += f" ORDER BY {order_by} {order_dir.upper()}"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in rows]

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
    if lastname:
        query += " AND LOWER(lastname) LIKE ?"
        params.append(f"%{lastname.lower()}%")
    if firstname:
        query += " AND LOWER(firstname) LIKE ?"
        params.append(f"%{firstname.lower()}%")
    if username:
        query += " AND LOWER(username) LIKE ?"
        params.append(f"%{username.lower()}%")
    query += f" ORDER BY {order_by} {order_dir.upper()}"
    print("SQL:", query)
    print("PARAMS:", params)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(query, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in rows]

def get_pending_duplicates():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT id, fio, dogovor, login, fam, imotch FROM pending_duplicates')
        return cur.fetchall()

def approve_duplicate(dup_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT fio, dogovor, login, fam, imotch FROM pending_duplicates WHERE id=?', (dup_id,))
        row = cur.fetchone()
        if row:
            conn.execute(
                'INSERT INTO abiturients (fio, dogovor, login, fam, imotch) VALUES (?, ?, ?, ?, ?)',
                row
            )
            conn.execute('DELETE FROM pending_duplicates WHERE id=?', (dup_id,))

def reject_duplicate(dup_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM pending_duplicates WHERE id=?', (dup_id,))

@app.route('/duplicates', methods=['GET', 'POST'])
@login_required
def duplicates():
    return render_template('duplicates.html')

def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute('SELECT role, approved FROM users WHERE username=?', (session['user'],))
                user = cur.fetchone()
                if not user or user[0] != role or user[1] != 1:
                    flash('Недостаточно прав')
                    return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/duplicates_abiturients', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def duplicates_abiturients():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'reject_all':
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute('DELETE FROM pending_duplicates')
        else:
            dup_id = request.form.get('dup_id')
            if action == 'approve':
                approve_duplicate(dup_id)
            elif action == 'reject':
                reject_duplicate(dup_id)
    duplicates = get_pending_duplicates()
    return render_template('duplicates_abiturients.html', duplicates=duplicates)

@app.route('/delete_abiturient', methods=['POST'])
@login_required
@role_required('admin')
def delete_abiturient():
    login = request.form.get('login')
    if login:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('DELETE FROM abiturients WHERE login=?', (login,))
    return redirect(url_for('abiturients'))

@app.route('/abiturients/download')
@login_required
def download_abiturients():
    order_by = request.args.get('order_by', 'created_at')
    order_dir = request.args.get('order_dir', 'desc')
    spec = request.args.get('spec')
    base = request.args.get('base')
    year = request.args.get('year')
    is_i = request.args.get('is_i')
    abiturients = get_all_abiturients(order_by, order_dir, spec, base, year, is_i)
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
def login_conflicts():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT fio, dogovor, login, fam, imotch, conflict_time FROM login_conflicts ORDER BY conflict_time DESC')
        conflicts = cur.fetchall()
    return render_template('login_conflicts.html', conflicts=conflicts)

@app.route('/delete_database', methods=['POST'])
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
    if request.method == 'POST':
        year = request.form.get('year')
        spec = request.form.get('spec')
        base = request.form.get('base')
        fio = request.form.get('fio').strip()
        fam, imotch = fio.split(' ', 1) if ' ' in fio else (fio, '')
        dogovor = f"{year} {spec} {base}"

        prefix = parse_dogovor(dogovor)
        if prefix == "error":
            message = "Ошибка: не удалось сгенерировать префикс логина. Проверьте данные."
        else:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute('SELECT login FROM abiturients')
                existing_logins = set(row[0] for row in cur.fetchall())
                cur = conn.execute('SELECT login FROM login_conflicts')
                existing_logins.update(row[0] for row in cur.fetchall())
                cur = conn.execute('SELECT login FROM pending_duplicates')
                existing_logins.update(row[0] for row in cur.fetchall())

            number = 1
            while True:
                login = f"{prefix}{number:03d}"
                if login not in existing_logins:
                    break
                number += 1

            if is_fio_duplicate(fam):
                with sqlite3.connect(DB_PATH) as conn:
                    cur = conn.execute('SELECT login FROM pending_duplicates WHERE login LIKE "dubl%"')
                    dubl_logins = set(row[0] for row in cur.fetchall())
                dubl_number = 1
                while True:
                    dubl_login = f"dubl{dubl_number:03d}"
                    if dubl_login not in dubl_logins:
                        break
                    dubl_number += 1
                save_pending_duplicate(fio, dogovor, dubl_login, fam, imotch)
                message = f"Дублирующее ФИО! Запись отправлена в раздел 'Дублирующиеся ФИО'. Логин: {dubl_login}"
            else:
                try:
                    save_abiturient(fio, dogovor, login, fam, imotch)
                    message = f"Логин успешно создан: {login}"
                except sqlite3.IntegrityError:
                    save_login_conflict(fio, dogovor, login, fam, imotch)
                    with sqlite3.connect(DB_PATH) as conn:
                        cur = conn.execute('SELECT fio, dogovor FROM abiturients WHERE login=?', (login,))
                        conflict_info = cur.fetchone()
                    message = f"Конфликт логина! Запись отправлена в раздел 'Конфликты логинов'."
        return render_template('manual_create.html', message=message, conflict_info=conflict_info)

    years = [str(y) for y in range(2020, 2031)]
    specs = list(spec_codes.keys())
    bases = list(base_codes.keys())
    return render_template('manual_create.html', years=years, specs=specs, bases=bases)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute('SELECT role, approved FROM users WHERE username=? AND password=?', (username, password))
            user = cur.fetchone()
            if user and user[1] == 1:
                session['user'] = username
                session['role'] = user[0]
                return redirect(url_for('index'))
            elif user and user[1] == 0:
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
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT fio, dogovor, login, fam, imotch, email, comment FROM abiturients WHERE login=?', (login,))
        abiturient = cur.fetchone()
    if not abiturient:
        flash('Абитуриент не найден')
        return redirect(url_for('abiturients'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        new_login = request.form.get('login', '').strip()
        comment = request.form.get('comment', '').strip()
        if new_login != login:
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.execute('SELECT 1 FROM abiturients WHERE login=?', (new_login,))
                if cur.fetchone():
                    flash('Такой логин уже существует!')
                    return render_template('edit_abiturient.html', abiturient=abiturient)
                conn.execute('UPDATE abiturients SET email=?, login=?, comment=? WHERE login=?', (email, new_login, comment, login))
        else:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute('UPDATE abiturients SET email=?, comment=? WHERE login=?', (email, comment, login))
        flash('Данные обновлены')
        return redirect(url_for('abiturients'))

    return render_template('edit_abiturient.html', abiturient=abiturient)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        role = 'assistant'
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute('SELECT 1 FROM users WHERE username=?', (username,))
            if cur.fetchone():
                flash('Пользователь уже существует')
                return render_template('register.html')
            conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)', (username, password, role))
        flash('Заявка на регистрацию отправлена. Ожидайте одобрения администратора.')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/admin_panel')
@vaanedain_required
def admin_panel():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT id, username, password, role, approved FROM users')
        all_users = cur.fetchall()
    return render_template('admin_panel.html', all_users=all_users)

@app.route('/delete_user', methods=['POST'])
@vaanedain_required
def delete_user():
    user_id = request.form.get('user_id')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM users WHERE id=?', (user_id,))
    return redirect(url_for('admin_panel'))

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
@vaanedain_required
def edit_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT id, username, password, role, approved FROM users WHERE id=?', (user_id,))
        user = cur.fetchone()
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            role = request.form.get('role')
            approved = int(request.form.get('approved', 0))
            conn.execute('UPDATE users SET username=?, password=?, role=?, approved=? WHERE id=?',
                         (username, password, role, approved, user_id))
            return redirect(url_for('admin_panel'))
    return render_template('edit_user.html', user=user)

@app.route('/add_user', methods=['GET', 'POST'])
@vaanedain_required
def add_user():
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        fio = request.form.get('fio').strip()
        position = request.form.get('position').strip()
        role = request.form.get('role')
        approved = int(request.form.get('approved', 1))
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute('SELECT 1 FROM users WHERE username=?', (username,))
            if cur.fetchone():
                flash('Пользователь с таким логином уже существует')
                return render_template('add_user.html')
            conn.execute('INSERT INTO users (username, password, role, approved) VALUES (?, ?, ?, ?)', (username, password, role, approved))
        flash('Пользователь успешно добавлен!')
        return redirect(url_for('admin_panel'))
    return render_template('add_user.html')

@app.route('/clear_abiturients', methods=['POST'])
@vaanedain_required
def clear_abiturients():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('DELETE FROM abiturients')
        conn.execute('DELETE FROM pending_duplicates')
    flash('Таблица абитуриентов и дублирующие записи успешно очищены.', 'success')
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
    cohort = request.args.get('cohort', '')
    order_by = request.args.get('order_by', 'username')
    order_dir = request.args.get('order_dir', 'asc')
    allowed_order = ['username', 'lastname', 'firstname', 'cohort1', 'email']
    if order_by not in allowed_order:
        order_by = 'username'
    if order_dir not in ['asc', 'desc']:
        order_dir = 'asc'

    query = 'SELECT username, password, email, firstname, lastname, cohort1 FROM students WHERE 1=1'
    params = []

    if lastname:
        query += " AND LOWER(lastname) LIKE ?"
        params.append(f"%{lastname.lower()}%")
    if firstname:
        query += " AND LOWER(firstname) LIKE ?"
        params.append(f"%{firstname.lower()}%")
    if username:
        query += ' AND LOWER(username) LIKE ?'
        params.append(f'%{username.lower()}%')
    if cohort:
        query += ' AND cohort1 = ?'
        params.append(cohort)

    query += f' ORDER BY {order_by} {order_dir}'

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(query, params)
        students = cur.fetchall()
        cur2 = conn.execute('SELECT DISTINCT cohort1 FROM students ORDER BY cohort1')
        cohorts = [row[0] for row in cur2.fetchall()]

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
            conn.execute('DELETE FROM students WHERE username=?', (username,))
    return redirect(url_for('students_list'))

@app.route('/abiturients_to_students', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def abiturients_to_students():
    with sqlite3.connect(DB_PATH) as conn:
        # Получаем список групп из таблицы groups
        cur = conn.execute('SELECT name FROM groups ORDER BY name')
        groups = [row[0] for row in cur.fetchall()]
        # Получаем список абитуриентов
        cur = conn.execute('SELECT id, fio, login, fam, imotch FROM abiturients')
        abiturients = [
            {'id': row[0], 'fio': row[1], 'login': row[2], 'lastname': row[3], 'firstname': row[4]}
            for row in cur.fetchall()
        ]
    if request.method == 'POST':
        cohort1 = request.form.get('cohort1')
        ids = request.form.getlist('abiturient_ids')
        if not cohort1 or not ids:
            flash('Выберите группу и хотя бы одного абитуриента')
            return redirect(url_for('abiturients_to_students'))
        with sqlite3.connect(DB_PATH) as conn:
            for ab_id in ids:
                cur = conn.execute('SELECT fio, login, fam, imotch FROM abiturients WHERE id=?', (ab_id,))
                ab = cur.fetchone()
                if ab:
                    fio, username, lastname, firstname = ab
                    password = username  # или сгенерировать
                    email = ''
                    conn.execute(
                        'INSERT INTO students (username, password, email, firstname, lastname, cohort1) VALUES (?, ?, ?, ?, ?, ?)',
                        (username, password, email, firstname, lastname, cohort1)
                    )
                    conn.execute('DELETE FROM abiturients WHERE id=?', (ab_id,))
        flash('Миграция завершена')
        return redirect(url_for('students_list'))
    return render_template('abiturients_to_students.html', abiturients=abiturients, groups=groups)

@app.route('/add_group', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def add_group():
    if request.method == 'POST':
        group_name = request.form.get('group_name', '').strip()
        if group_name:
            with sqlite3.connect(DB_PATH) as conn:
                try:
                    conn.execute('INSERT INTO groups (name) VALUES (?)', (group_name,))
                    flash('Группа добавлена')
                except sqlite3.IntegrityError:
                    flash('Такая группа уже существует')
        else:
            flash('Название группы не может быть пустым')
        return redirect(url_for('add_group'))
    # Список всех групп для отображения
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute('SELECT name FROM groups ORDER BY name')
        groups = [row[0] for row in cur.fetchall()]
    return render_template('add_group.html', groups=groups)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)