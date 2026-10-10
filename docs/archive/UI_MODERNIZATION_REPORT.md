# Отчёт о модернизации Manticore Desktop

Дата проверки: 2 сентября 2026 года.

## Итог

Manticore сохранён на исходном стеке Python + Flask + Jinja + SQLite + pywebview/WebView2. Бизнес-логика, роли, server-side фильтрация, локальный и серверный режимы, single-instance защита и защищённый механизм обновлений не заменялись.

Интерфейс переработан как единое desktop-first приложение: sidebar + topbar + рабочая область, общая дизайн-система, светлая/тёмная/системная темы, command palette, унифицированные таблицы, формы, кнопки, состояния, сообщения и диалоги. Tkinter полностью исключён из пользовательского UX и из сборки Windows-клиента.

## Что было до модернизации

- Основная оболочка визуально воспринималась как веб-страница внутри окна.
- `templates/base.html` содержал 1 917 строк, включая крупные inline CSS и JavaScript.
- Стили страниц были изолированы друг от друга и использовали разные цвета, отступы, кнопки и формы.
- Первичная конфигурация, создание пароля администратора и часть системных сообщений использовали Tk/ttk.
- Глобальный поиск не выглядел и не работал как desktop command palette.
- Тёмная тема, единый responsive shell и отдельный раздел настроек desktop-клиента отсутствовали.

## Что реализовано

### Общая оболочка и навигация

- Фиксированный sidebar с локальными SVG-иконками, ролевой видимостью пунктов, активным разделом и вложенными группами.
- Компактный topbar с заголовком страницы, поиском, переключателем темы и текущим пользователем.
- Sidebar полностью сворачивается, после чего ограничение ширины рабочей области снимается и большие таблицы используют всё окно; responsive fallback для узкого окна сохранён.
- Версия приложения, документация, настройки и выход вынесены в нижнюю часть sidebar.
- Существующие routes и проверки ролей сохранены.

### Design system

- `static/css/tokens.css` — палитра, типографика, размеры, радиусы, тени, motion и семантические цвета.
- `static/css/legacy.css` — вынесенный слой совместимости старой разметки.
- `static/css/modern.css` — shell и общие компоненты.
- `static/css/pages/*.css` — стили конкретных страниц.
- `static/js/theme.js` — раннее применение темы без вспышки неправильного режима.
- `static/js/app.js` — глобальный поиск и базовое поведение приложения.
- `static/js/modern-ui.js` — sidebar, уведомления, modal confirm, drag-and-drop и progressive enhancement.
- `templates/_ui_macros.html` — повторно используемые badge, disclosure и empty state.

После рефакторинга `templates/base.html` занимает 202 строки. Inline `<style>` удалён из всех шаблонов: найдено 0 шаблонов с такими блоками. Тяжёлый frontend framework, CDN, удалённые шрифты и DataGrid-библиотека не добавлялись.

### Dashboard и рабочие сценарии

- Компактные KPI-карточки для абитуриентов, кандидатов, приказов, студентов и групп.
- Центр задач с semantic severity: error, warning, informational, success.
- Заполняемость групп с progress bar и предупреждением о переполнении.
- Empty states с понятным следующим действием.
- Страница проверки данных, мастер миграции, административная панель, загрузка файлов, группы и карточки человека приведены к общей системе.

### Таблицы, формы и действия

- Компактные строки, sticky header, hover/selected states и локальный горизонтальный scroll контейнера.
- Существующие server-side фильтрация, сортировка и пагинация сохранены.
- Унифицированы input, select, textarea, checkbox, file picker, focus, disabled и invalid states.
- File upload поддерживает drag-and-drop и показывает имя выбранного файла.
- Технические наборы квадратных icon-only кнопок в таблицах абитуриентов и студентов заменены одной понятной кнопкой «Действия» и текстовым popover-меню.
- Опасные действия используют единый доступный modal confirm; нативные `confirm()` удалены.
- Диалог поддерживает клавишу Escape и возвращает фокус инициатору.

### Поиск

- `Ctrl+K` открывает command/search palette.
- Enter выполняет поиск, Escape закрывает palette, фокус возвращается на кнопку поиска.
- Debounce и отмена устаревшего запроса сохранены.
- Результаты строятся безопасными DOM-узлами через `textContent`; пользовательские значения не передаются в `innerHTML`.

### Темы и доступность

- Light, Dark и System темы работают без внешних ресурсов.
- Тема сохраняется локально в браузере/WebView.
- Добавлены видимые `:focus-visible` состояния, корректные labels, status/alert semantics и keyboard UX.
- Semantic warning/success/info и update cards имеют отдельные контрастные варианты для тёмной темы.

## Windows desktop client

### Первый запуск и конфигурация

- Tk/ttk wizard заменён локальным pywebview/WebView2 onboarding.
- Есть отдельные карточки «Общий сервер» и «Локальная база».
- Remote mode проверяет адрес и сохраняет запрет удалённого HTTP; loopback остаётся допустимым.
- Local mode позволяет выбрать или создать SQLite-базу через узкий Python bridge.
- Создание первого пароля администратора перенесено в тот же WebView UX; пароль хешируется и не записывается в конфигурацию.
- Конфигурационный wizard запускается отдельным дочерним процессом, поэтому основной WebView стартует в чистом GUI lifecycle.

### Запуск, ошибки и настройки

- До готовности сервера показывается локальный splash `Запуск Manticore…`, а не пустое белое окно.
- Для remote mode добавлен отдельный экран ошибки соединения с кнопками «Повторить» и «Изменить сервер».
- Техническая ошибка остаётся в локальном log.
- Добавлена страница `Настройки приложения`: тема, режим, источник данных, версия, проверка обновлений, открытие log и повторная конфигурация.
- В обычном браузере desktop-only действия корректно скрыты или объяснены.

### Системные сообщения и обновления

- Tkinter полностью удалён из `desktop/windows_client.py`.
- Критичные сообщения и подтверждения updater используют системный Win32 MessageBox.
- SHA-256, pinned certificate, Authenticode/WinVerifyTrust, GitHub release approval и запуск installer сохранены.
- `desktop/ui` включён в PyInstaller one-file bundle.

## Производительность

- Не добавлен отдельный frontend runtime или SPA bundle.
- CSS/JS обслуживаются как обычные кешируемые static assets.
- Поиск отменяет устаревшие запросы.
- Таблицы продолжают использовать серверную обработку данных.
- Splash не содержит искусственной задержки: переход выполняется сразу после готовности сервера/соединения.

## Безопасность

- CSRF, authentication, session cookies и role decorators сохранены.
- Jinja autoescape сохранён.
- Dynamic search/cohort values выводятся через `textContent`.
- Удалённый HTTP по-прежнему запрещён, кроме loopback.
- Пароль первоначального администратора не хранится в `desktop-config.json`.
- Updater продолжает проверять SHA-256 и Authenticode до запуска файла.
- Внешние CDN и remote origins не добавлены.

## Проверка

### Автоматические тесты

Команда:

```powershell
.\.venv-desktop\Scripts\python.exe -m unittest discover -s tests -q
```

Результат: **104 tests, OK; 0 failed, 0 skipped**.

Дополнительно покрыты:

- сохранение remote/local конфигурации через `SetupApi`;
- отклонение небезопасного remote HTTP;
- создание первого admin password через bridge;
- понятная обработка ошибки удалённого подключения;
- startup page и переход desktop-окна к приложению;
- присутствие новых static assets и command palette в HTML.

`git diff --check` проходит; остаются только информационные предупреждения Git о будущем LF → CRLF преобразовании рабочей копии.

### Визуальный и функциональный smoke test

В локальном изолированном профиле проверены:

- login и вход по Enter;
- dashboard;
- applicants table с sticky header;
- custom confirm и безопасная отмена удаления без изменения данных;
- global search по `Ctrl+K`, Enter и Escape;
- migration wizard;
- data checks;
- file upload/drop zone;
- admin panel и update cards;
- desktop settings;
- свёрнутый sidebar;
- light и dark темы;
- размеры окна 1024×640, 1366×768 и 1920×1080;
- отсутствие глобального горизонтального overflow; широкий applicants table скроллится только внутри собственного контейнера;
- отсутствие console errors и битых изображений на проверенных страницах.

### Windows build

Выполнена полная development-сборка:

```powershell
.\build_windows_desktop.ps1 -SkipDependencies -UnsignedDevelopmentBuild
```

Результат:

- `dist/Manticore.exe` — 45 218 560 байт;
- `dist/installer/Manticore-Setup-1.1.3.exe` — 46 779 286 байт;
- PyInstaller завершился успешно;
- Inno Setup 6.7.3 завершился успешно;
- в PyInstaller warning log нет `tkinter`/`_tkinter`;
- архив EXE содержит `desktop/ui/setup.html`, `startup.html`, `connection_error.html` и `system.css`;
- собранный EXE запущен с изолированным `%LOCALAPPDATA%`: процесс и WebView2 поднялись и не завершились аварийно;
- временный профиль и тестовая база после проверки удалены.

SHA-256:

- `Manticore.exe`: `4E24B22C6D7E0A218C5DECFFEAE8C1C80EB503E193D0D7ECFAADED75FE5397D5`;
- `Manticore-Setup-1.1.3.exe`: `6AAA740360E1139E45A48B6BC01AEB1DD86313FB2433418430A0F5AAFAC9B3C0`.

Оба development-артефакта ожидаемо имеют статус `NotSigned`, потому что сборка запущена с `-UnsignedDevelopmentBuild`.

## Изменённые файлы

- `README.md`
- `app.py`
- `desktop/Manticore.spec`
- `desktop/windows_client.py`
- `templates/_student_transfer_timeline.html`
- `templates/_ui_macros.html`
- `templates/abiturients.html`
- `templates/abiturients_to_students.html`
- `templates/add_group.html`
- `templates/add_user.html`
- `templates/admin_panel.html`
- `templates/approve_users.html`
- `templates/audit_logs.html`
- `templates/backups.html`
- `templates/base.html`
- `templates/campaigns.html`
- `templates/data_checks.html`
- `templates/duplicates.html`
- `templates/duplicates_abiturients.html`
- `templates/edit_abiturient.html`
- `templates/edit_conflict.html`
- `templates/edit_student.html`
- `templates/edit_user.html`
- `templates/enrollment_order_student_roster.html`
- `templates/enrollment_order_upload.html`
- `templates/file_work.html`
- `templates/index.html`
- `templates/login.html`
- `templates/login_conflicts.html`
- `templates/login_rules_setup.html`
- `templates/manual_create.html`
- `templates/migration_wizard.html`
- `templates/person_card.html`
- `templates/register.html`
- `templates/search.html`
- `templates/students.html`
- `templates/students_list.html`
- `tests/test_app.py`
- `tests/test_windows_client.py`

## Новые файлы

- `UI_MODERNIZATION_REPORT.md`
- `desktop/ui/connection_error.html`
- `desktop/ui/connection_error.js`
- `desktop/ui/setup.css`
- `desktop/ui/setup.html`
- `desktop/ui/setup.js`
- `desktop/ui/startup.html`
- `desktop/ui/system.css`
- `static/css/tokens.css`
- `static/css/legacy.css`
- `static/css/modern.css`
- все 31 файла `static/css/pages/*.css`, извлечённые из соответствующих шаблонов;
- `static/images/manticore-logo-dark.png`
- `static/js/app.js`
- `static/js/desktop-settings.js`
- `static/js/modern-ui.js`
- `static/js/theme.js`
- `templates/desktop_settings.html`

## Удалённые файлы

Нет.

## Известные ограничения и production-checklist

- Development EXE и installer не подписаны. Для production необходимо повторить сборку с реальным сертификатом через `-CertificateThumbprint`, затем проверить Authenticode на чистой Windows 10 и Windows 11.
- Реальный upgrade/uninstall и скачивание одобренного GitHub Release не выполнялись: для этого нужен опубликованный подписанный release. Кодовые и unit security checks механизма обновления проходят.
- Визуальная проверка выполнена на текущем Windows/WebView2 и в локальном browser harness. Отдельная матрица 150%/200% DPI и несколько физических Windows-машин остаются release QA, а не блокером development-сборки.
