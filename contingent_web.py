"""File-work integration. Temporary snapshots live outside the business database."""
import io
import json
import os
import re
import secrets
import sqlite3
import time
from collections import Counter
from pathlib import Path

import pandas as pd
from flask import Blueprint, abort, flash, redirect, request, session, url_for

from contingent import LABELS, map_groups, parse_docx, reconcile


def register_contingent(app, services):
    s = services
    bp = Blueprint('contingent', __name__, url_prefix='/file_work/contingent')

    def folder():
        path = Path(app.config['UPLOAD_FOLDER']) / 'contingent_previews'
        path.mkdir(exist_ok=True, parents=True)
        for old in path.glob('*.json'):
            if old.stat().st_mtime < time.time() - 86400:
                old.unlink(missing_ok=True)
        return path

    def save(state):
        path = folder() / (state['token'] + '.json')
        temporary = path.with_suffix('.' + secrets.token_hex(8) + '.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)

    def load(token):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            abort(404)
        path = folder() / (token + '.json')
        if not path.exists():
            abort(404, 'Сверка устарела. Загрузите файл повторно.')
        state = json.loads(path.read_text(encoding='utf-8'))
        if state['owner'] != session.get('user'):
            abort(403)
        return state

    def snapshot(year):
        with sqlite3.connect(s['DB_PATH']) as conn:
            conn.row_factory = sqlite3.Row
            groups = [dict(row) for row in conn.execute(
                'SELECT name, group_year FROM groups WHERE group_year=? AND COALESCE(is_hidden,0)=0', (year,))]
            students = [dict(username=r['username'], fio=' '.join(filter(None, [r['lastname'], r['firstname']])),
                             group=r['cohort1'] or '') for r in conn.execute('SELECT username, lastname, firstname, cohort1 FROM students')]
        return groups, students

    def render(state, **extra):
        groups, _ = snapshot(state['year'])
        mapped = map_groups(state['document'], groups, state.get('mappings'))
        return s['render_file_work_page'](state['year'], 'contingent', contingent=state,
            contingent_groups=mapped, contingent_labels=LABELS,
            contingent_counts=Counter(r['status'] for r in state.get('rows', [])), **extra)

    @bp.post('/upload')
    @s['login_required']
    @s['role_required']('admin', 'assistant')
    def upload():
        try:
            file = request.files.get('file')
            s['validate_uploaded_file'](file, {'docx'})
            data = file.read(s['MAX_UPLOAD_BYTES'] + 1)
            if len(data) > s['MAX_UPLOAD_BYTES']:
                raise ValueError('Превышен допустимый размер файла.')
            document = parse_docx(data, os.path.basename(file.filename.replace('\\', '/')))
        except (ValueError, s['UploadValidationError']) as exc:
            flash(str(exc), 'error')
            return s['file_work_redirect']('contingent')
        state = dict(token=secrets.token_hex(16), owner=session['user'], document=document,
                     year=s['get_active_campaign_year'](), created=time.time(), mappings={}, choices={})
        save(state)
        return redirect(url_for('contingent.detail', token=state['token']), code=303)

    @bp.route('/<token>', methods=['GET', 'POST'])
    @s['login_required']
    @s['role_required']('admin', 'assistant')
    def detail(token):
        state = load(token)
        if request.method == 'POST':
            year = request.form.get('year', '')
            if not re.fullmatch(r'20\d{2}', year):
                abort(400, 'Выберите год контекста сверки.')
            if state['document']['metadata_conflict'] and request.form.get('metadata_confirmed') != 'yes':
                flash('Подтвердите выбранный контекст: год в файле отличается от документа.', 'error')
                return render(state)
            state['year'] = year
            state['mappings'] = {key[6:]: value for key, value in request.form.items() if key.startswith('group:') and value}
            state['choices'] = {key[8:]: value for key, value in request.form.items() if key.startswith('student:') and value}
            groups, students = snapshot(year)
            state['rows'] = reconcile(map_groups(state['document'], groups, state['mappings']), students, state['choices'])
            state.pop('prepared', None)
            save(state)
            return redirect(url_for('contingent.detail', token=token), code=303)
        return render(state)

    @bp.get('/<token>/export')
    @s['login_required']
    @s['role_required']('admin', 'assistant')
    def export(token):
        state = load(token)
        columns = {'specialty': 'Специальность', 'source_group': 'Группа по списку', 'target_group': 'Группа Manticore',
                   'fio': 'ФИО', 'current_group': 'Текущая группа', 'status': 'Статус', 'source_note': 'Примечание', 'comment': 'Комментарий'}
        rows = state.get('rows', [])
        if request.args.get('discrepancies') == '1':
            rows = [r for r in rows if r['status'] != 'MATCHED']
        data = []
        for row in rows:
            item = {}
            for key, label in columns.items():
                value = LABELS.get(row[key], row[key]) if key == 'status' else row.get(key, '')
                # Spreadsheet formula injection must not turn source notes into executable cells.
                item[label] = "'" + value if str(value).startswith(('=', '+', '-', '@')) else value
            data.append(item)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            pd.DataFrame(data, columns=list(columns.values())).to_excel(writer, sheet_name='Сверка контингента', index=False)
        output.seek(0)
        return s['send_file'](output, as_attachment=True, download_name='Сверка контингента.xlsx',
                              mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @bp.post('/<token>/prepare')
    @s['login_required']
    @s['role_required']('admin')
    def prepare(token):
        state = load(token)
        selected = set(request.form.getlist('selected'))
        rows = [r for r in state.get('rows', []) if r['id'] in selected and r['status'] == 'WRONG_GROUP']
        prepared, projected = [], Counter()
        for row in rows:
            item = dict(row, error='', capacity_state='available')
            try:
                result = s['transfer_student_to_group'](row['username'], row['target_group'], actor_role='admin' if s['current_user_is_admin']() else '',
                    expected_source=row['current_group'], preview=True, capacity_override=True)
                count = result['target_count'] + projected[row['target_group']]
                item['capacity_state'] = 'over' if count > result['capacity'] else 'full' if count == result['capacity'] else 'available'
                item['message'] = result['message']
                projected[row['target_group']] += 1
            except s['StudentTransferError'] as exc:
                item['error'] = str(exc)
            prepared.append(item)
        state['prepared'] = prepared
        state['confirmation'] = secrets.token_hex(16)
        save(state)
        return render(state, confirmation=True)

    @bp.post('/<token>/apply')
    @s['login_required']
    @s['role_required']('admin')
    def apply(token):
        state = load(token)
        if not state.get('confirmation') or request.form.get('confirmation') != state['confirmation']:
            abort(409, 'Подготовьте переводы повторно.')
        # Consume confirmation before writes. Every row also rechecks expected_source under the service lock.
        prepared = state.pop('prepared', [])
        state.pop('confirmation', None)
        save(state)
        outcomes = []
        if s['is_campaign_archived'](state['year']):
            abort(409, 'Кампания архивирована.')
        for row in prepared:
            if row['error']:
                outcomes.append(dict(fio=row['fio'], message=row['error'], ok=False))
                continue
            try:
                if s['is_campaign_archived'](state['year']):
                    raise s['StudentTransferError']('archived_campaign', 'Кампания архивирована.')
                result = s['transfer_student_to_group'](row['username'], row['target_group'], actor_role='admin' if s['current_user_is_admin']() else '',
                    expected_source=row['current_group'], capacity_override=request.form.get('capacity_override') == 'yes',
                    comment=f"Сверка контингента; файл={state['document']['source_filename']}; строка={row['source_position']}; {row['source_note']}")
                outcomes.append(dict(fio=row['fio'], message=result['message'], ok=True))
            except s['StudentTransferError'] as exc:
                message = 'Данные изменились после сверки. Выполните сверку повторно.' if exc.code == 'concurrent_update' else str(exc)
                outcomes.append(dict(fio=row['fio'], message=message, ok=False))
            except (sqlite3.Error, OSError):
                outcomes.append(dict(fio=row['fio'], message='Не удалось сохранить перевод. Повторите сверку.', ok=False))
        state['outcomes'] = outcomes
        groups, students = snapshot(state['year'])
        state['rows'] = reconcile(map_groups(state['document'], groups, state['mappings']), students, state['choices'])
        save(state)
        return redirect(url_for('contingent.detail', token=token), code=303)

    app.register_blueprint(bp)
