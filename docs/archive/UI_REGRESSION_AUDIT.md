# UI Regression Audit

Дата проверки: 4 сентября 2026 года. Аудит выполнен на локально запущенном Flask-приложении с тем же HTML/CSS/JS, который загружается Windows WebView2-клиентом.

## 1. Найденные проблемы

| Severity | Page/component | Theme | Problem | Root cause |
| --- | --- | --- | --- | --- |
| High | Global search / Ctrl+K | Dark, System Dark | Светлая шапка, несогласованные input, Esc и Close, разрыв между шапкой и результатами | Legacy-компонент использовал жёсткие светлые цвета поверх частичной modern-theme реализации |
| High | Data checks / sample rows | Dark | Hover/active строка могла получить светлый фон при светлом тексте | Глобальный selector `ul li a:hover, ul li a.active` из `legacy.css` применялся ко всем спискам приложения |
| Medium | Global search / keyboard | Все | Не было выбора результатов стрелками и Enter не открывал выбранный результат | В JS отсутствовала модель selected result и ARIA-состояние option/listbox |
| Medium | Global search / small window | Все | Высота и верхний отступ диалога не были ограничены низким viewport | Диалог полагался на фиксированный legacy margin и не имел viewport-bound max-height |
| Medium | Tables / selected rows | Dark | Общий selected state не гарантировал согласованную пару foreground/background и не учитывал `aria-selected` | Состояние задавало только accent background для одного CSS-класса |
| Low | Add group / capacity status | Dark | Красный, зелёный и muted-текст были зашиты inline и не следовали теме | Жёсткие hex-цвета в шаблоне |
| Low | File work and manual create | Dark | Разделители и optional hint оставались светло-серыми inline-значениями | Жёсткие inline border/text цвета |
| Low | Global search / input | Все | Placeholder и введённое ФИО располагались слишком близко к границе поля | Для palette input внутренний горизонтальный padding был принудительно обнулён |
| High | Global search / opener | Все | Верхняя кнопка и shortcut могли не открыть palette | Открытие было косвенно связано со скрытой sidebar-кнопкой и распределено между двумя JS-модулями |
| High | Предварительный список / KPI | Все | Проблемные показатели нельзя было раскрыть до конкретных людей | KPI вычислялись как отдельные суммы без стабильных issue codes и общей выборки |
| High | Предварительный список / Excel | Web, Desktop | Нельзя было скачать конкретную проблему или текущую выборку | Единственный endpoint всегда выгружал весь roster |
| High | Предварительный список / «Требуют внимания» | Все | Не было единого дедуплицированного списка причин | Признак `needs_attention` не представлял отдельные причины и не включал все KPI-категории |
| Medium | Предварительный список / таблица | Все | Не было column filters, AND-комбинации, сортировки и выбора столбцов | Таблица была статическим Jinja-рендером без общей модели состояния |
| Medium | Предварительный список / группы | Все | Счётчики и пустые accordion-группы не реагировали на фильтрацию | Отсутствовал клиентский filtering pipeline |
| Medium | Issue dialog | Все | Отсутствовали modal, поиск, keyboard close и issue export | Для KPI не существовало общего drill-down компонента |
|Medium | XLSX | Все | Экспорт не закреплял заголовок и не включал autofilter/автоширину | Использовался прямой `DataFrame.to_excel` без оформления workbook |
| Medium | Desktop / downloads | WebView2 | Скачивания из WebView2 были отключены настройкой pywebview по умолчанию | Не был включён штатный download handler WebView2 |

Итого: найдено 17, исправлено 17.

## 2. Исправления

- Global search: все поверхности, границы, текст, placeholder, кнопка закрытия, метаданные, результаты, hover/selected/error переведены на semantic tokens. Диалог получил ограничение по viewport и собственный scroll. Файлы: `static/css/legacy.css`, `static/css/modern.css`, `static/css/tokens.css`.
- Global search keyboard UX: добавлены циклические Arrow Up/Down, Enter для выбранного результата, `role=listbox/option`, `aria-selected` и прокрутка выбранной строки в видимую область. Файлы: `static/js/app.js`, `templates/base.html`.
- Global search input: добавлен локальный горизонтальный padding, чтобы placeholder, ФИО и поисковый запрос не прилипали к focus-ring. Файл: `static/css/modern.css`.
- Global search opener: клик по верхней строке/плашке `Ctrl K` и физическое сочетание Ctrl+K теперь напрямую вызывают `openSearch()` в основном модуле palette; удалена косвенная дублирующая связь из `modern-ui.js`. Файлы: `templates/base.html`, `static/js/app.js`, `static/js/modern-ui.js`.
- Data checks: опасный глобальный selector ограничен навигацией; hover/focus-within sample rows используют согласованную token-пару. Файлы: `static/css/legacy.css`, `static/css/modern.css`.
- Tables: `.is-selected` и `[aria-selected=true]` используют `--bg-selected` вместе с `--text-primary`. Файлы: `static/css/modern.css`, `static/css/tokens.css`.
- Forms: добавлены тематические disabled, placeholder и Chromium/WebView autofill states. Файл: `static/css/modern.css`.
- Inline theme leaks: capacity status, optional hint и разделители заменены тематическими классами. Файлы: `templates/add_group.html`, `templates/manual_create.html`, `templates/file_work.html` и соответствующие page CSS.
- Layout: content допускает корректное сжатие и перенос длинных значений без расширения всей страницы. Файл: `static/css/modern.css`.

## 3. Design-system changes

- Добавлены semantic tokens `--bg-hover` и `--bg-selected` для Light, Dark и System Dark.
- Selected/hover states теперь всегда задают совместимые background и foreground.
- Legacy navigation selector ограничен `.main-nav`, чтобы не протекать в page components.
- Palette оформлен как единый elevated component и получил унифицированные empty/loading/result/error/focus/selected states.
- Hardcoded inline status/text/divider colors заменены на `--success`, `--danger`, `--text-muted` и `--border`.
- Native Chromium/WebView controls наследуют `color-scheme`; placeholder, disabled и autofill имеют явные тематические состояния.

## 4. Order preview

- Реализованы стабильные issue types: `missing_email`, `unpaid`, `not_found`, `unassigned`, `name_review`, `specialty_conflict`, `attention_required`.
- Активные KPI открывают встроенный modal; нулевые KPI disabled. В modal есть описание, фактический count, поиск, таблица, Excel и закрытие по Escape/кнопке/backdrop.
- `attention_required` содержит человека один раз, а поле «Проблемы» перечисляет все его причины.
- Общий клиентский state комбинирует global search, Email empty/not-empty, multi-select групп и status по AND. Группы без результатов скрываются, счётчики обновляются, показывается `X из Y` и filter chips.
- Добавлены сортировка заголовков с `aria-sort`, управление видимостью столбцов и отдельные full/filtered export controls. Скрытие столбца не снимает фильтр.

## 5. Backend

- `classify_enrollment_order_roster_row()` — единый источник issue membership; `issue_rows` используется KPI, JSON drill-down и issue XLSX.
- Добавлены авторизованные endpoints issue details и issue download в scope конкретного `upload_id`.
- `filter_enrollment_order_roster_rows()` воспроизводит серверную часть filter state для filtered export; Excel не строится чтением DOM.
- XLSX создаётся через существующие pandas/openpyxl: настоящий workbook, bold header, freeze row, autofilter и ограниченная автоширина. Кириллица и безопасное Windows-имя сохраняются.

## 6. Desktop

- Full, filtered и issue Excel используют обычные Flask `send_file(..., as_attachment=True)` endpoints.
- Для WebView2 включён штатный `webview.settings['ALLOW_DOWNLOADS']`; отдельная версия UI и отдельные download API не создавались.

## 7. Проверенные страницы

В живом приложении открыты: Dashboard (`index`), Applicants, Students, Students list, Data checks, Migration wizard (`abiturients_to_students`), File work, Enrollment order upload, Admin panel, Campaigns, Duplicates, Applicant duplicates, Student duplicates, Login conflicts, Audit logs, Backups, Approve users, Add user, Add group, Manual create, Desktop settings, Search и Documentation. `/groups` не является отдельным route; фактическая страница управления группами — `/add_group`.

Статически проверены также все шаблоны: `base`, `_ui_macros`, `_campaign_switcher`, `_csrf_field`, `_student_transfer_timeline`, `edit_abiturient`, `edit_student`, `edit_conflict`, `edit_user`, `person_card`, `migration_wizard`, `enrollment_order_student_roster`, `students_upload`, `login`, `register`, `login_rules_setup`; desktop views `setup`, `startup`, `connection_error` и их CSS/JS.

Страницы edit/person card и data-bearing result states не удалось открыть с реальной записью, потому что аудит выполнялся на изолированной пустой БД; их templates, CSS и backend rendering покрыты тестами.

## 8. Проверенные состояния

Default, hover, focus, focus-visible, active, selected, `aria-selected`, checked/table selection, disabled, invalid styles, empty, loading, no-results, error styles, long text wrapping, overlay, scrollbar, Escape, Ctrl+K, Enter, Arrow Up и Arrow Down. Проверены sidebar/navigation, buttons, forms, tables/wrappers, cards, badges, flash/alerts, confirm modal, file picker, empty states и command palette.

## 9. Проверенные темы и разрешения

- Фактически переключены Light, Dark и System; для System проверено наследование текущей OS color scheme.
- Viewport smoke matrix: 1920×1080, 1600×900, 1366×768, 1280×720, 1024×768, 1024×640 и узкий 760×640.
- На ключевых страницах проверено отсутствие глобального horizontal overflow. На 1024×640 и 760×640 command palette остаётся внутри viewport.
- Предоставленные скриншоты Windows desktop/WebView2 использованы для воспроизведения. Основной desktop client загружает ту же design system; desktop-specific setup/startup/error views проверены архитектурно.

## 10. Tests

- `.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -q` — 105 tests, OK; roster test дополнен проверками KPI=details=XLSX, кириллицы, причин и Email+Group filtered export.
- `node --check static/js/enrollment-order-roster.js` — OK.
- `.\\build_windows_desktop.ps1 -UnsignedDevelopmentBuild -SkipDependencies` — OK; созданы `dist/Manticore.exe` и `dist/installer/Manticore-Setup-0.0.1-alpha.exe`.
- Browser route smoke — все фактические проверенные routes вернули 200; static assets — 200/304.
- Browser console — 0 errors, 0 warnings, 0 uncaught exceptions.
- `.\\.venv\\Scripts\\python.exe -m compileall -q .` — OK.
- `git diff --check` — OK.

## 11. Остаточные проблемы

- Физическое Windows DPI 100/125/150/200% не переключалось; layout проверен на семи CSS viewport, а фиксированные critical размеры ограничены responsive rules.
- Production signed EXE не собирался; после UI-изменений успешно собраны unsigned development EXE и installer.
- В пустой audit DB нельзя визуально воспроизвести все data-dependent result/validation/error состояния и карточку реального человека; соответствующие templates/styles и backend paths проверены статически и unit-тестами.
- Автоматические pixel-by-pixel baseline screenshots в репозиторий не добавлялись, чтобы не вводить новую test/runtime dependency.
- Новый data-dependent issue modal проверен unit/integration и статически; повторный отдельный browser screenshot-run с наполненной fixture-БД в этой итерации не выполнялся.
