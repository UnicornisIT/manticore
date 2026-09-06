"""Run an isolated, loopback-only UI audit: python tests/frontend/serve_layout_audit.py.

Sign in with the printed disposable credentials. Append ?layout_audit=1 to
/file_work, /add_group, /students_upload or /edit_student/layout-fixture.
The probe uses synthetic File/DragEvent objects and never submits uploads.
"""
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    with tempfile.TemporaryDirectory(prefix='manticore_layout_') as directory:
        password = secrets.token_urlsafe(18)
        os.environ.update(
            UPLOAD_FOLDER=directory, DB_FILENAME='layout.db',
            SECRET_KEY=secrets.token_urlsafe(32), ADMIN_DEFAULT_PASSWORD=password,
            APP_DEBUG='false',
        )
        import app
        from flask import request

        app.save_login_generation_settings(
            app.get_default_login_generation_rules(), setup_completed=True,
            updated_by='layout-audit',
        )
        with sqlite3.connect(app.DB_PATH) as conn:
            conn.execute(
                'INSERT INTO students (username,password,email,firstname,lastname,cohort1) '
                'VALUES (?,?,?,?,?,?)',
                ('layout-fixture', 'fixture', 'fixture@example.test', 'Тест', 'Макет', '26ФМ-11-1'),
            )
        conn.close()

        @app.app.after_request
        def add_probe(response):
            if request.args.get('layout_audit') == '1' and response.mimetype == 'text/html':
                probe = Path(__file__).with_name('upload_layout.browser.js').read_text(encoding='utf-8')
                response.set_data(response.get_data(as_text=True).replace(
                    '</body>', '<script>' + probe + '</script></body>',
                ))
            return response

        print(f'Local audit: http://127.0.0.1:5052/login — admin / {password}', flush=True)
        try:
            app.app.run(host='127.0.0.1', port=5052, use_reloader=False)
        finally:
            app.conn.close()


if __name__ == '__main__':
    main()
