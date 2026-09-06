"""Contingent document adapters and pure reconciliation; no database access."""
import re
from collections import Counter, defaultdict
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


SPECIALTIES = {
    'ЛЕЧЕБНОЕ ДЕЛО': 'ЛД', 'АКУШЕРСКОЕ ДЕЛО': 'АД',
    'СЕСТРИНСКОЕ ДЕЛО': 'СД', 'СТОМАТОЛОГИЯ ОРТОПЕДИЧЕСКАЯ': 'СТО',
    'СТОМАТОЛОГИЯ ПРОФИЛАКТИЧЕСКАЯ': 'СТП', 'СТОМАТОЛОГИЧЕСКОЕ ДЕЛО': 'СТД',
    'ФАРМАЦИЯ': 'ФМ', 'ЛАБОРАТОРНАЯ ДИАГНОСТИКА': 'ЛАБД',
}
LABELS = {
    'MATCHED': 'Совпадает', 'WRONG_GROUP': 'Другая группа в Manticore',
    'NOT_FOUND': 'Не найден в Manticore',
    'EXTRA_IN_MANTICORE': 'Есть в Manticore, отсутствует в списке',
    'AMBIGUOUS_MATCH': 'Неоднозначное совпадение',
    'GROUP_MAPPING_REQUIRED': 'Требуется сопоставить группу',
    'AUTO_NORMALIZED_GROUP': 'Группа сопоставлена автоматически',
    'DUPLICATE_IN_SOURCE': 'Повтор в документе',
    'MULTIPLE_SOURCE_GROUPS': 'Студент указан в нескольких группах',
    'INVALID_ROW': 'Ошибка строки', 'METADATA_WARNING': 'Предупреждение документа',
}


def clean(value):
    return ' '.join(str(value or '').split())


def normalize_name(value):
    return clean(value).casefold()


def normalize_group(value):
    return re.sub(r'\s+', '', clean(value).upper().translate(str.maketrans('–—−', '---')))


def group_kind(value):
    name = normalize_group(value)
    if re.fullmatch(r'\d{2}[А-ЯЁ]+-(?:(?:9|11)[А-ЯЁ]*|М)(?:-\d+)?', name):
        return 'modern'
    if re.fullmatch(r'\d{3}|[1-6][А-ЯЁ]{1,4}', name):
        return 'legacy'
    return None


def student_row(text, position):
    raw = text
    value = re.sub(r'^\s*\d+[.)]?\s+', '', raw).strip()
    note_match = re.search(r'(?i)\s+(?=(?:пр\.|приб\.|перевод\b|приказ\b|отчисл\w*\b))', value)
    name = value[:note_match.start()] if note_match else value
    note = value[note_match.end():] if note_match else ''
    number = re.search(r'№\s*([^\s,;]+)', note)
    date = re.search(r'\b(\d{2}\.\d{2}\.\d{2}(?:\d{2})?)\b', note)
    order_date = None
    if date:
        try:
            order_date = datetime.strptime(date[1], '%d.%m.%Y' if len(date[1]) == 10 else '%d.%m.%y').date().isoformat()
        except ValueError:
            pass
    return dict(raw_full_name=name, normalized_full_name=normalize_name(name),
                source_position=position, source_note=note, order_number=number[1] if number else None,
                order_date=order_date, raw_text=raw,
                valid=bool(re.fullmatch(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*(?:\s+[^\W\d_]+(?:[-’'][^\W\d_]+)*){1,5}", clean(name))))


def parse_docx(data, filename):
    """Walk body order and actual cell paragraphs, preserving Word numbering text."""
    try:
        with ZipFile(BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024 or len(archive.infolist()) > 3000:
                raise ValueError('Документ слишком большой после распаковки.')
            if 'word/document.xml' not in archive.namelist():
                raise ValueError('Файл не является DOCX.')
        doc = Document(BytesIO(data))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Не удалось прочитать DOCX.') from exc
    result = dict(source_filename=filename, document_type='', academic_year=None,
                  education_period=None, specialties=[], groups=[], warnings=[])
    current = None
    specialty = ''
    table_groups = False
    all_text = []

    def consume(text, position, table=False, numbered=False):
        nonlocal current, specialty, table_groups
        value = clean(text)
        if not value:
            return
        all_text.append(value)
        if value.upper() in SPECIALTIES:
            specialty = value.upper()
            if specialty not in result['specialties']:
                result['specialties'].append(specialty)
            current = None
            return
        if group_kind(value):
            current = dict(source_name=text, normalized_name=normalize_group(value), specialty=specialty,
                           students=[], is_legacy=group_kind(value) == 'legacy', id=str(len(result['groups'])))
            result['groups'].append(current)
            table_groups |= table
            return
        if re.search(r'\b20\d{2}\s*[–—/-]\s*20\d{2}\b', value):
            years = re.findall(r'20\d{2}', value)
            key = 'academic_year' if int(years[1]) - int(years[0]) == 1 else 'education_period'
            result[key] = f'{years[0]}–{years[1]}'
            return
        if re.match(r'(?i)^(начало обучения|срок обучения|выпуск|№|фио|ф\.и\.о|список|учебный)', value) or re.fullmatch(r'\d+[.)]?', value):
            return
        if current:
            row = student_row(text, position)
            # Metadata is excluded above; uncertain content remains visible as INVALID_ROW.
            current['students'].append(row)

    for block_index, element in enumerate(doc.element.body):
        if element.tag.endswith('}p'):
            paragraph = Paragraph(element, doc)
            consume(paragraph.text, f'Абзац {block_index + 1}', numbered=bool(paragraph._p.xpath('./w:pPr/w:numPr')))
        elif element.tag.endswith('}tbl'):
            table = Table(element, doc)
            seen = set()
            for ri, row in enumerate(table.rows):
                for ci, cell in enumerate(row.cells):
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    for pi, paragraph in enumerate(cell.paragraphs):
                        consume(paragraph.text, f'Таблица {block_index + 1}, строка {ri + 1}, ячейка {ci + 1}, абзац {pi + 1}', table=True)
    if not result['groups'] or not any(g['students'] for g in result['groups']):
        raise ValueError('Не удалось определить структуру списка контингента.')
    result['document_type'] = 'DEPARTMENT_CONTINGENT_TABLES' if table_groups else 'SINGLE_GROUP_CONTINGENT'
    file_years = re.findall(r'20\d{2}', filename)
    content = result['academic_year'] or result['education_period']
    result['filename_year'] = file_years[0] if file_years else None
    result['metadata_conflict'] = bool(file_years and content and file_years[0] != content[:4])
    if result['metadata_conflict']:
        result['warnings'].append(f'Обнаружено несоответствие учебного года. Название файла: {filename}; в документе: {content}. Выберите контекст сверки.')
    return result


def map_groups(document, groups, overrides=None):
    overrides = overrides or {}
    mapped = []
    for source in document['groups']:
        item = dict(source, target='', mapping_status='GROUP_MAPPING_REQUIRED', candidates=[])
        if source['is_legacy']:
            item['mapping_status'] = 'LEGACY_IGNORED'
            mapped.append(item)
            continue
        name = source['normalized_name']
        code = SPECIALTIES.get(source['specialty'])
        eligible = [g for g in groups if not code or re.match(r'^\d{2}' + re.escape(code) + '-', normalize_group(g['name']))]
        exact = [g['name'] for g in eligible if normalize_group(g['name']) == name]
        siblings = [g['name'] for g in eligible if re.fullmatch(re.escape(name) + r'-\d+', normalize_group(g['name']))]
        item['candidates'] = exact or siblings or [g['name'] for g in eligible]
        chosen = overrides.get(source['id'])
        if chosen and chosen in [g['name'] for g in eligible]:
            item.update(target=chosen, mapping_status='MANUAL_MAPPING')
        elif len(exact) == 1:
            item.update(target=exact[0], mapping_status='EXACT_MATCH')
        elif len(siblings) == 1 and normalize_group(siblings[0]) == name + '-1':
            item.update(target=siblings[0], mapping_status='AUTO_NORMALIZED_GROUP')
        mapped.append(item)
    return mapped


def reconcile(mapped, students, choices=None):
    choices = choices or {}
    names = defaultdict(list)
    for student in students:
        names[normalize_name(student['fio'])].append(student)
    occurrences = defaultdict(list)
    for group in mapped:
        if not group['is_legacy']:
            for row in group['students']:
                occurrences[row['normalized_full_name']].append(group['normalized_name'])
    rows, present = [], defaultdict(set)
    for group in mapped:
        if group['is_legacy']:
            continue
        for index, source in enumerate(group['students']):
            key = f"{group['id']}:{index}"
            candidates = names[source['normalized_full_name']] if source['valid'] else []
            student = candidates[0] if len(candidates) == 1 else next((s for s in candidates if s['username'] == choices.get(key)), None)
            status = 'MATCHED'
            occurrence = occurrences[source['normalized_full_name']]
            if not source['valid']:
                status = 'INVALID_ROW'
            elif len(set(occurrence)) > 1:
                status = 'MULTIPLE_SOURCE_GROUPS'
            elif len(occurrence) > 1:
                status = 'DUPLICATE_IN_SOURCE'
            elif not group['target']:
                status = 'GROUP_MAPPING_REQUIRED'
            elif len(candidates) > 1 and not student:
                status = 'AMBIGUOUS_MATCH'
            elif not student:
                status = 'NOT_FOUND'
            elif student['group'] != group['target']:
                status = 'WRONG_GROUP'
            # Unresolved identical names are possible members, never falsely called absent.
            present[group['target']].update(s['username'] for s in ([student] if student else candidates))
            rows.append(dict(source, id=key, specialty=group['specialty'], source_group=group['source_name'],
                             target_group=group['target'], current_group=student['group'] if student else '',
                             fio=source['raw_full_name'], username=student['username'] if student else '',
                             status=status, candidates=candidates, comment=''))
    for target in sorted({g['target'] for g in mapped if g['target']}):
        group = next(g for g in mapped if g['target'] == target)
        for student in students:
            if student['group'] == target and student['username'] not in present[target]:
                rows.append(dict(id='extra:' + student['username'], specialty=group['specialty'],
                                 source_group=group['source_name'], target_group=target, current_group=target,
                                 fio=student['fio'], username=student['username'], status='EXTRA_IN_MANTICORE',
                                 source_note='', candidates=[], comment='', raw_text='', source_position=''))
    return rows
