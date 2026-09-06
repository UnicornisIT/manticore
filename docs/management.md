# Объединение Admin и Settings

## Аудит до изменений

- `/admin_panel`, `templates/admin_panel.html`, `static/css/pages/admin_panel.css`: создание, одобрение, редактирование, удаление пользователей и роли; кампании; группы; миграция; правила логинов; резервные копии; аудит; очистка кампании; обновление сервера и разрешение Windows-релизов.
- `/desktop_settings`, `templates/desktop_settings.html`, `static/js/desktop-settings.js`: локальная тема, сведения о Windows-клиенте, подключение, журнал, проверка и установка обновлений. Здесь же находился блок About из общих метаданных.
- Sidebar имел отдельные Admin и Settings. Topbar содержал поиск и тему, но пользователь был статическим элементом без меню.
- Отдельных `/admin`, `/settings`, `/about`, `/desktop-settings` не было. Прямых вызовов старых UI routes в desktop/windows_client.py не найдено.
- Admin и Settings дублировали точки входа в управление. Разрешение релиза администратором и установка разрешённого релиза на устройство — разные операции, обе сохранены.
- Тема и About доступны всем авторизованным пользователям, включая период первоначальной настройки. Browser-only карточка объясняет доступность клиентских параметров. Desktop-only: режим, источник, подключение, журнал и установка обновления.
- Миграция ранее доступна также assistant через раздел операций; это право сохранено. Группы и правила логинов используют существующий role_required; пользователи, кампании, резервные копии, аудит и управление релизами — admin_required. Объединение не расширяет эти права.

## Результат

Страница **Управление** (`/management`) содержит три partial: `_management_admin.html`, `_management_settings.html`, `_management_about.html`. Название охватывает и администрирование системы, и личные параметры устройства.

Административная секция содержит восемь карточек перехода. Сложный список пользователей, изменение ролей, очистка кампании и серверные/Windows-релизы оставлены на `/management/administration` (существующий admin_panel.html, переименованный визуально в «Пользователи и обслуживание»). Дублирующая панель навигационных кнопок на этой подстранице сокращена до создания пользователя и очистки кампании.

Настройки используют прежний desktop-settings.js и мост pywebview. У темы добавлены radio-семантика, стрелки клавиатуры и синхронизация с topbar. Клиентские карточки скрыты до появления API, браузерная подсказка в Desktop скрывается.

About содержит Manticore, текущую APP_VERSION, UnicornisIT, GitHub, Telegram и Email из app_metadata.py через существующий макрос безопасных внешних ссылок. Отдельной верхней кнопки About нет.

Sidebar содержит один «Управление», активный также на соответствующих административных подстраницах. Меню пользователя открывается через native details/summary. Ссылки ведут на #administration, #settings и #about; первый пункт отсутствует без прав. Escape закрывает меню с возвратом фокуса. Секции имеют tabindex=-1 и scroll-margin-top для sticky header.

## Совместимость и безопасность

- `/admin_panel` сохранён с admin_required, перенаправляет на `/management#administration`.
- `/desktop_settings` сохранён с login_required, перенаправляет на `/management` без фрагмента: браузер наследует старый #about. Проверено реальной навигацией.
- Старый desktop_settings.html удалён после переноса содержимого в partial. admin_panel.html и его CSS сохранены для сложной подстраницы.
- Backend action routes, POST-формы и CSRF сохранены. Проверка admin_required вынесена без изменения условий в current_user_is_admin: пользователь существует в БД, role=admin, approved=1. Эта же проверка определяет видимость административной секции и пункта меню, независимо от устаревшей роли сессии.
- Первоначальная настройка по-прежнему блокирует административные операции. Management доступен до её завершения, как прежние настройки и контакты.

## Файлы этой задачи

Изменены: app.py; README.md; static/css/modern.css; static/js/desktop-settings.js; templates/base.html; templates/admin_panel.html; templates/documentation.html; templates/add_group.html; templates/add_user.html; templates/audit_logs.html; templates/backups.html; templates/campaigns.html; templates/edit_user.html; templates/login_rules_setup.html; tests/test_app.py; tests/test_app_metadata.py; tests/test_frontend_keyboard.py.

Добавлены: templates/management.html; templates/_management_admin.html; templates/_management_settings.html; templates/_management_about.html; static/js/user-menu.js; tests/frontend/management.test.cjs; docs/management.md.

Удалён: templates/desktop_settings.html. Другие исходные незакоммиченные изменения пользователя сохранены.

## Проверки

- Полный suite: `.venv/Scripts/python.exe -m unittest discover -s tests` — 130 тестов, OK (включая запуск двух Node-проверок нового management.test.cjs).
- Новые серверные проверки: секции и ссылки для admin/viewer/неодобренного admin; устаревшая административная роль в сессии; отказ admin endpoints; авторизация; редиректы; первоначальная настройка.
- Существующие тесты серверных обновлений переведены на новую подстраницу. Тесты About проверяют старую ссылку с follow_redirects.
- Новые Node-проверки: скрытие Windows-controls в браузере; позднее подключение pywebview API; чтение сведений о клиенте; выбор темы стрелками; синхронизация темы с topbar. Это имитация API, не реальный WebView2.
- Браузер: три темы LIGHT/DARK/SYSTEM на пяти размерах; горизонтального overflow нет, на 1024 — одна колонка. Из-за масштабирования browser tool измеренные CSS viewport: 1921×1080, 1600×900, 1366×768, 1280×721, 1024×640 (погрешность 1 px для двух размеров). SYSTEM проверен при тёмной системной теме среды.
- Проверены пользовательское меню, Escape, переход с фокусом на #settings, отступ от sticky header, viewer без admin UI, ровно одна active-ссылка sidebar, сохранение старой закладки #about.
- `git diff --check`: код 0, ошибок пробелов нет. Git сообщает только существующие предупреждения LF/CRLF.
- Реальный Desktop/WebView2 не запускался. Проверка установки обновлений и открытия системного журнала в реальном клиенте остаётся ручной проверкой; её результат не заявляется как успешный.
