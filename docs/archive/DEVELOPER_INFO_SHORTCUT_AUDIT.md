# Аудит developer information и Ctrl+K

Дата: 2026-09-05. Проверена текущая рабочая копия, включая уже существовавшие незакоммиченные изменения. Они сохранены.

## До изменений

Поиск выполнен по UnicornisIT, developer, разработчик, разработано, GitHub, Telegram/telegram, mailto:, email, footer, about, «О программе» в исходниках, шаблонах, static, desktop, README и документации. Виртуальные окружения, бинарные и сгенерированные build/dist-файлы не считались действующим UI. Каталогов config/, app/, routes/, blueprints/ в проекте нет: конфигурация и маршруты находятся в app.py.

| Данные | Файл текущей рабочей копии до доработки | Template / endpoint / страница | Фактическая доступность |
|---|---|---|---|
| UnicornisIT, GitHub, Telegram, email, версия | templates/base.html:191–207 | base.html, наследуемый templates/login.html; login, /login — вход. Также templates/index.html; index, / — главная | Footer включался Jinja только для index/login, но static/css/modern.css:292–293 скрывал его через display:none для login-page-body и app-authenticated. Modern CSS подключён после legacy. На /login это подтверждено живым DOM, computed style и снимком экрана до изменения. |
| Отдельный email разработчика | templates/login_rules_setup.html:44 | login_rules_setup.html; login_generation_setup, /setup — «Свои правила», доступ администратора | Действующий, не скрытый CSS блок. Тот же текст проверен на живой странице после замены литерала общей metadata. Отдельный снимок /setup до правки не получен. |
| Copyright UnicornisIT | LICENSE:3 | Файл лицензии | Не интерфейс приложения. |

**Полная информация о разработчике в текущем UI отсутствовала: данные существовали в коде, но текущий интерфейс их не выводил.** Это относится к полному footer. Утверждать полное отсутствие любого контакта было бы неверно: отдельный email уже имелся на административной странице /setup.

Контакты совпали с указанными в задании; конфликтующих значений не найдено. Технические ссылки UnicornisIT/manticore относятся к репозиторию и release pipeline. В README обнаружена устаревшая формулировка, будто версия UI задаётся только APP_VERSION в .env; она исправлена.

## После изменений

- Настройки → «О программе»: `/desktop_settings#about`, endpoint `desktop_settings`, template `templates/desktop_settings.html`. Общий раздел для Web/Desktop и всех ролей. Доступен также до завершения первичной настройки; требование авторизации сохранено. Ссылка «О программе» находится в заголовке настроек.
- На `/login` действительно виден компактный двухстрочный footer с именем приложения, версией, разработчиком и тремя ссылками. Он остаётся в обычном потоке flex layout, без fixed/absolute. На крайне низком окне используется вертикальная прокрутка.
- `app_metadata.py:APP_METADATA` — единственный источник контактных значений. `app.py:inject_template_globals` передаёт его Jinja. Общие ссылки создаёт `templates/_developer_links.html`. Существующий email /setup переведён на этот же источник.
- `VERSION` — существующий источник версии для Flask, sidebar, документации, About, footer, Desktop title/API и сборки. Номер вручную не продублирован. У Flask убран вводящий в заблуждение fallback 1.1.1: при отсутствующем VERSION используются APP_VERSION или unknown. Desktop/build pipeline не переработан.
- Контактная секция README генерируется из metadata через `python sync_app_metadata.py`; `--check` проверяет синхронизацию, и это включено в regression tests.
- Ссылки имеют `target="_blank" rel="noopener noreferrer"`; email использует mailto. Desktop явно включает штатную настройку pywebview `OPEN_EXTERNAL_LINKS_IN_BROWSER`. Проверен установленный WebView2 backend: NewWindowRequested помечается обработанным и передаётся webbrowser.open; в Windows это os.startfile. Новый JS bridge не требуется.

## Ctrl+K

Основной общий обработчик в `static/js/app.js` использует `(event.ctrlKey || event.metaKey) && event.code === 'KeyK'`. Ранее сравнивался event.key с латинской k, поэтому физическая клавиша K на RU возвращала л и не совпадала.

Открытие и закрытие идемпотентны, повторная инициализация search handler защищена, удержание клавиши не вызывает повторное открытие. Сохраняется исходный фокус и восстанавливается после Escape. Удалён отложенный лишний focus.

В `static/documentation.js` удалён конкурирующий Ctrl+K. В документации сочетание теперь открывает общий global search; локальный фильтр работает через клик/Tab. У локального фильтра удалена неверная подсказка Ctrl K в `templates/documentation.html`. При Escape из общего поиска локальный запрос документации сохраняется. Общая подсказка Ctrl K остаётся.

`static/js/modern-ui.js` проверен: отдельного обработчика поиска в исходной рабочей копии уже не было вследствие предыдущих незакоммиченных изменений. В рамках этой доработки дополнительных изменений в этот файл не внесено.

## Фактическая проверка UI

Использован отдельный Flask server `127.0.0.1:5081`, отдельная пустая БД `.ui-audit-data/developer-info/baze.db` и созданная для проверки учётная запись. Пользователь явно разрешил тестовый вход. Рабочая БД не использовалась.

Web:

- Открыты `/login`, `/setup`, `/desktop_settings#about`, `/documentation`.
- About виден в Light, Dark и System. Проверены название, версия, все контакты, href/target/rel, переход Настройки → О программе.
- Footer проверен в Light, Dark, System для 1920×1080, 1600×900, 1366×768, 1280×720, 1024×640. Проверены DOM-геометрия и снимки: footer виден, форма не перекрыта, горизонтального overflow нет. Из-за масштаба встроенного браузера отдельные прогоны Light/System имели округление размера на 1 CSS px; в Dark все пять размеров подтверждены точно.
- Дополнительно 361×400 в Dark: вертикальный scroll разрешает разместить форму и footer без перекрытия/горизонтального scrollbar.
- В реальном браузере Ctrl+K открыл один глобальный dialog; Escape закрыл его и восстановил фокус. Проверены click opener и обычная K без Ctrl. На документации Ctrl+K из локального поискового input открыл общий поиск; Escape сохранил текст «кампания» и вернул фокус в исходный input.
- Theme tokens/focus/hover CSS проверены для обеих цветовых схем. Отдельный нативный hover-снимок не выполнялся. Ошибок/warnings в console после итогового browser smoke не было.

Desktop/WebView2:

- Запущен текущий `desktop.windows_client.open_desktop_window` из исходников с отдельным профилем `.ui-audit-data/developer-info/desktop-profile`, подключённым к тому же тестовому серверу. Установленный пользовательский клиент/профиль не изменялся.
- Native accessibility tree окна python.exe / Manticore содержит login footer, UnicornisIT, GitHub, Telegram и Email.
- **Полная интерактивная Desktop-проверка не подтверждена.** Windows automation возвращала несоответствующий окну screenshot, ошибки UIA CacheRequest/изменения границ, а click/Tab не давали подтверждённого фокуса поля. Поэтому нативные EN/RU shortcuts, Desktop About и запуск системных браузера/почты кликом не объявляются проверенными.
- Физическое переключение раскладки ОС не выполнялось. EN/RU события KeyboardEvent покрыты исполняемыми regression tests общего JS. Browser Ctrl+K отдельно проверен реальным keyboard action.

## Изменённые файлы этой доработки

Backend/metadata: app.py, app_metadata.py.

UI: templates/base.html, templates/desktop_settings.html, templates/_developer_links.html, templates/login_rules_setup.html, templates/documentation.html, static/css/modern.css. templates/login.html наследует общий base и сам не менялся.

JS: static/js/app.js, static/documentation.js.

Desktop: desktop/windows_client.py.

Документация: README.md, sync_app_metadata.py, DEVELOPER_INFO_SHORTCUT_AUDIT.md.

Tests: tests/frontend/search_shortcut.test.cjs, tests/test_frontend_keyboard.py, tests/test_app_metadata.py, tests/test_windows_external_links.py; точечные правки tests/test_windows_client.py для двух настроек pywebview и изоляции Timer в существующих тестах.

## Тесты и ограничения

- `node --test tests/frontend/search_shortcut.test.cjs`: 17 сценариев, PASS. Ctrl/Meta, EN/RU/другая раскладка/uppercase, нет modifier, KeyL, body/table/filter/input, click, Escape/reopen/focus restoration, повторная инициализация/удержание, отсутствие двойного fetch/focus, оба порядка подключения документации и общего JS. Стенд исполняет реальные scripts, но использует небольшой DOM fixture.
- `tests/test_app_metadata.py`: 9 интеграционных тестов, PASS. Все роли и первичная настройка, auth, реальные Flask responses, динамическая подмена версии и metadata, общие контакты, безопасные ссылки, footer и синхронизация README.
- `tests/test_windows_external_links.py`: настройки external links и динамическая версия Desktop.
- Полный suite: `.\.venv-desktop\Scripts\python.exe -m unittest discover -s tests -q` — **119 tests, 41.049 s, OK, exit 0**. Фоновых исключений нет. JS regression suite вызывается из unittest при доступном Node. Два прежних теста SetupApi больше не запускают реальный фоновой Timer.
- `node --check static/js/app.js` и `node --check static/documentation.js`: PASS.
- `python sync_app_metadata.py --check`: PASS.
- `git diff --check`: PASS. Git сообщает только штатные уведомления LF → CRLF.

Не выполнены: полноценный ручной EN/RU smoke в Windows/WebView2, системный запуск всех внешних links/почтового клиента, отдельный installer build. Изменения не закоммичены и не опубликованы.
