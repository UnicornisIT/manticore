# Аудит массового выбора строк

Дата проверки: 4 сентября 2026 года.

## 1. Исходная проблема

На странице «Список абитуриентов и логинов» заголовочный checkbox действительно находил все строки через `querySelectorAll`, но внутри цикла каждый раз заново читал `selectAllAbiturients.checked` и синхронно отправлял событие `change` строки. После первой выбранной строки старый `syncBulkToolbar()` видел частичный выбор и сбрасывал заголовочный checkbox в `false`. Поэтому все следующие итерации цикла уже снимали выбор, и выбранной оставалась только первая строка.

Исправление выполнено не локальной заменой одного селектора, а переносом всех существующих bulk-selection таблиц на общий контроллер.

## 2. Найденные реализации

Аудит охватил `templates/`, `static/js/`, `static/css/`, `static/css/pages/`, `app.py`, `desktop/` и `tests/` по checkbox-, select-all-, selected-ID-, bulk- и DOM-selector-паттернам. В проекте найдены две реальные таблицы с выбором строк:

| Page | Template | JS | Bulk action | Old behavior | Migrated |
| --- | --- | --- | --- | --- | --- |
| Список абитуриентов и логинов | `templates/abiturients.html` | Inline Applicants selection удалён; `static/js/table-selection.js` | Отметить/снять оплату, экспорт, удаление | Select all оставлял только первую строку; логика счётчика и header state была локальной | Да |
| Сверка и перенос в студенты | `templates/abiturients_to_students.html` | Inline `selectAllReady` удалён; `static/js/table-selection.js` | Перенос сверенных кандидатов, предпросмотр распределения по логинам | Глобальный selector по `candidate_ids`, без общего Set, счётчика и `indeterminate` | Да |

Другие найденные checkbox относятся к настройкам или подтверждениям, а не к выбору строк: показ скрытых групп, правила генерации логинов, признак оплаты в карточке, подтверждение дубликата ФИО, автораспределение/распределение по логинам и режим Desktop setup. Они намеренно не переведены и не получили select-all. Повторяющихся `id` у row-checkbox не найдено.

## 3. Общая архитектура

Общий Vanilla JS-контроллер находится в `static/js/table-selection.js` и подключается один раз из `templates/base.html`.

- Каждая независимая область помечается `data-table-selection`.
- Header, строки, счётчик, toolbar и action controls помечаются соответственно `data-select-all`, `data-row-select`, `data-selection-count`, `data-selection-toolbar` и `data-selection-action`.
- Выбор хранится в `Set` стабильных значений `value`, совпадающих с ID сущности. Индексы и позиции DOM-строк не используются.
- Один делегированный `change`-handler обслуживает область; повторный render не размножает listeners.
- Каждый контроллер scoped своим контейнером, поэтому несколько таблиц и служебные checkbox не пересекаются.
- `MutationObserver` синхронизирует добавление/удаление строк, `hidden`, `disabled` и inline `style`-изменения. Для нестандартного class/model-based rerender предусмотрено событие `table-selection:refresh`.
- Контроллер обновляет `.is-selected`, `aria-selected`, счётчик, toolbar и disabled-state массовых кнопок. Старый глобальный обработчик selected-row из `modern-ui.js` удалён.

## 4. Selection semantics

- **Select all:** выбираются только текущие отображаемые, не disabled row-checkbox в текущем контейнере.
- **Header:** 0 из N — unchecked; 1..N-1 — `indeterminate`; N из N — checked. Следующий click по checked header очищает отображаемые строки.
- **Фильтры:** Applicants использует серверный GET и полностью перезагружает result set, поэтому выбор предсказуемо сбрасывается. Скрытые строки при клиентской фильтрации исключаются после refresh и не попадают в Select all.
- **Сортировка:** client-side перестановка существующих строк сохраняет выбор по ID. Серверная сортировка Applicants создаёт новый view lifecycle и сбрасывает выбор.
- **Пагинация:** у обеих текущих bulk-selection таблиц пагинации нет. Если она появится, текущая семантика page-local: отправляются явные ID текущей отображаемой страницы, без скрытого `select_all=true`.
- **Rerender:** выбор пересвязывается по ID, отсутствующие, скрытые и disabled записи удаляются из selection; повторная инициализация уже подключённого контейнера игнорируется.
- **Disabled rows:** не выбираются, не учитываются в полном состоянии header и удаляются из selection при изменении доступности.

## 5. Backend integration

Applicants отправляет повторяющееся form field `abiturient_ids`, миграция — `candidate_ids`. Оба route получают полный массив через общий `unique_numeric_form_values()`: сохраняется порядок, принимаются только положительные числовые ID, дубликаты удаляются.

Дальнейшие запросы к базе, проверки campaign/status, проверка роли для удаления и существующая authorization model сохранены. Frontend не используется как security boundary. Пустая выборка отклоняется backend; UI одновременно скрывает/блокирует недоступное массовое действие.

## 6. Изменённые файлы

Файлы именно этой унификации:

- `static/js/table-selection.js`
- `templates/base.html`
- `static/js/modern-ui.js`
- `templates/abiturients.html`
- `templates/abiturients_to_students.html`
- `static/css/pages/abiturients_to_students.css`
- `app.py`
- `tests/test_app.py`
- `BULK_SELECTION_AUDIT.md`

В рабочем дереве присутствуют и другие изменения проекта; они не относятся к этому аудиту и не были откатываны.

## 7. Tests

Добавлены регрессии:

- обе страницы рендерят semantic data attributes и общий script, а старый Applicants handler отсутствует;
- Applicants bulk route обрабатывает все переданные ID, удаляет дубликаты и игнорирует invalid/negative значения;
- проверяется фактическое изменение трёх выбранных записей и корректное число строк в audit log.

Сохранены и повторно запущены существующие проверки массовой оплаты/снятия оплаты и миграции/распределения кандидатов. Базовая DOM-матрица 0/partial/all/clear, клавиатура, disabled rows и темы проверена в реальном браузере, поскольку в репозитории нет отдельного DOM test runner.

Полный запуск `python -m unittest discover -s tests -q`: **107 tests, OK**. `node --check static/js/table-selection.js`: **OK**. `git diff --check`: **OK**; выведены только информационные предупреждения Git о будущей нормализации LF/CRLF.

## 8. Manual checks

Реально открыты локальные страницы `/abiturients` и `/abiturients_to_students` с тестовой SQLite-базой.

На Applicants проверено: 0/5, Select all 5/5, снятие одной строки 4/5 с `indeterminate`, partial → all, all → clear, одиночный выбор, toolbar/counter, selected rows, Space на header, серверная сортировка и сброс после нового result set. Массовая операция «Отметить оплату» отправлена для всех пяти записей и подтверждена через штатный dialog.

На migration проверено пять кандидатов в состоянии «Ждёт приказ»: все row-checkbox disabled, header disabled, counter 0, обе массовые кнопки disabled. Это подтверждает исключение недоступных строк из selection.

Selected row проверен в тёмной/system и светлой темах: фон и текст берутся из общих semantic tokens и остаются читаемыми.

## 9. Web/Desktop

Web-интерфейс проверен фактическими кликами в Chromium-based in-app browser на локальном Flask server. Настоящий Windows Desktop/WebView2 клиент перезапущен из `.venv-desktop` после изменений; журнал подтверждает открытие `/abiturients` и загрузку актуального `/static/js/table-selection.js`. Selection использует тот же Flask/Jinja/static bundle; отдельной desktop-копии JS нет.

Автоматизированное чтение DOM нативного WebView2 в текущей среде недоступно, поэтому полная интерактивная матрица checkbox выполнена в браузерной поверхности, а Desktop подтверждён запуском приложения и общим кодовым путём.

## 10. Остаточные ограничения

- В проекте сейчас нет страницы с двумя bulk-selection таблицами одновременно, модальной selection-таблицы и server-side pagination. Изоляция реализована архитектурно через container scope, но эти варианты не имеют отдельного продуктового UI-сценария для ручной проверки.
- Для внешнего class/model-based client filter после изменения видимости нужно отправить `table-selection:refresh`; child-list, `hidden`, `disabled` и inline `style` отслеживаются автоматически.
- Cross-filter и cross-page selection намеренно не сохраняются: скрытого выбора невидимых записей нет.
