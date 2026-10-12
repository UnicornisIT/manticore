# Инвентаризация репозитория

Дата аудита: 2026-10-10. Ветка: `chore/repo-audit-cleanup`.

## Резюме

- Отслеживается файлов: **204**.
- Распределение: корень — 38, `.github/` — 5, `data/` — 1, `deploy/` — 5, `desktop/` — 18, `docs/` — 6, `static/` — 59, `templates/` — 46, `tests/` — 25, `uploads/` — 1.
- Среди отслеживаемых файлов нет runtime-БД, SQLite-файлов, `.env`, ключей/сертификатов, логов, кэшей, виртуальных окружений, `dist/`, `build/`, EXE или ZIP.
- Отслеживаемые бинарные/табличные артефакты имеют явное назначение: `static/template.xlsx` и `static/students_template.csv` — скачиваемые шаблоны импорта; два DOCX в `tests/fixtures/contingent/` — синтетические тестовые фикстуры.
- Локальные `.env`, `__pycache__/`, `.pytest_cache/`, `.venv/`, `.venv-desktop/`, `build/`, `dist/` и `.ui-audit-data/` присутствуют в рабочем дереве, но уже игнорируются Git и не входят в инвентаризацию содержимого.

## Каталог `data/`

В каталоге находится только `data/email_providers.json`: это версионируемый справочник доменов, а не пользовательские данные. Он читается `email_validation.py` и включается в Desktop-сборку через `desktop/Manticore.spec`. Каталог не описан в README; это нужно исправить на этапе документации. Исключать весь `data/` из индекса нельзя: безопаснее игнорировать runtime-содержимое точечно, сохранив этот справочник.

## Полный перечень

| Путь | Размер, байт | Назначение | Используется ли |
|---|---:|---|---|
| `.env.example` | 1 042 | Безопасный пример окружения | Да — настройка разработчика/деплоя |
| `.gitattributes` | 68 | Конфигурация Git | Да — Git |
| `.github/ISSUE_TEMPLATE/bug_report.md` | 550 | Шаблон GitHub issue | Да — интерфейс GitHub |
| `.github/ISSUE_TEMPLATE/feature_request.md` | 464 | Шаблон GitHub issue | Да — интерфейс GitHub |
| `.github/pull_request_template.md` | 823 | Шаблон pull request | Да — интерфейс GitHub |
| `.github/workflows/pip-audit.yml` | 520 | Workflow CI/релиза | Да — GitHub Actions |
| `.github/workflows/windows-release.yml` | 20 700 | Workflow CI/релиза | Да — GitHub Actions |
| `.gitignore` | 869 | Конфигурация Git | Да — Git |
| `BULK_SELECTION_AUDIT.md` | 12 423 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `CONTINGENT_RECONCILIATION_AUDIT.md` | 21 827 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `CONTRIBUTING.md` | 1 571 | Документация репозитория | Да — разработка/релиз |
| `DESKTOP_UPDATES_AUDIT.md` | 16 615 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `DEVELOPER_INFO_SHORTCUT_AUDIT.md` | 13 822 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `LICENSE` | 1 089 | Лицензия проекта | Да — репозиторий/дистрибуция |
| `LIVE_SEARCH_AUDIT.md` | 11 504 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `README.md` | 105 037 | Документация репозитория | Да — разработка/релиз |
| `RELEASE_CHECKLIST.md` | 2 938 | Документация репозитория | Да — разработка/релиз |
| `SECURITY.md` | 1 320 | Документация репозитория | Да — разработка/релиз |
| `TOPBAR_UPLOAD_LAYOUT_AUDIT.md` | 12 020 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `UI_MODERNIZATION_REPORT.md` | 16 981 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `UI_REGRESSION_AUDIT.md` | 15 489 | Архивный отчёт аудита | Ручная справка; кандидат на перенос в `docs/archive/` |
| `VERSION` | 6 | Версия приложения | Да — метаданные, сборка и релиз |
| `app.py` | 548 955 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `app_metadata.py` | 357 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `build_windows_desktop.ps1` | 8 747 | PowerShell-скрипт сборки/обслуживания | Да — сборка/релиз |
| `contingent.py` | 12 634 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `contingent_web.py` | 10 180 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `data/email_providers.json` | 7 093 | Реестр доменов почтовых провайдеров | Да — `email_validation.py`, упаковка Desktop |
| `db_safety.py` | 14 722 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `deploy.sh` | 5 665 | Скрипт серверного развёртывания | Да — операторский запуск |
| `deploy/manticore-update.service` | 448 | Конфигурация серверного развёртывания | Да — deploy/systemd/nginx |
| `deploy/manticore-update.sudoers` | 154 | Конфигурация серверного развёртывания | Да — deploy/systemd/nginx |
| `deploy/manticore.service` | 432 | Конфигурация серверного развёртывания | Да — deploy/systemd/nginx |
| `deploy/nginx-https.example.conf` | 1 012 | Конфигурация серверного развёртывания | Да — deploy/systemd/nginx |
| `deploy/nginx.conf` | 663 | Конфигурация серверного развёртывания | Да — deploy/systemd/nginx |
| `desktop/Manticore.iss` | 2 232 | Конфигурация сборки Windows | Да — PyInstaller/Inno Setup |
| `desktop/Manticore.spec` | 1 624 | Конфигурация сборки Windows | Да — PyInstaller/Inno Setup |
| `desktop/__init__.py` | 38 | Код/скрипт Windows Desktop | Да — Desktop и его сборка |
| `desktop/manticore.ico` | 130 777 | Ресурс Windows Desktop | Да — упаковка/интерфейс |
| `desktop/package_smoke_test.py` | 7 827 | Код/скрипт Windows Desktop | Да — Desktop и его сборка |
| `desktop/release_tools.py` | 4 757 | Код/скрипт Windows Desktop | Да — Desktop и его сборка |
| `desktop/sign_windows_binary.ps1` | 1 553 | Код/скрипт Windows Desktop | Да — Desktop и его сборка |
| `desktop/ui/connection_error.html` | 1 238 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/connection_error.js` | 1 440 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/setup.css` | 4 342 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/setup.html` | 5 225 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/setup.js` | 3 431 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/source_dialog.css` | 835 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/source_dialog.html` | 2 483 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/source_dialog.js` | 1 601 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/startup.html` | 456 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/ui/system.css` | 2 356 | Ресурс интерфейса Windows Desktop | Да — `desktop/windows_client.py` |
| `desktop/windows_client.py` | 109 147 | Код/скрипт Windows Desktop | Да — Desktop и его сборка |
| `desktop_releases.py` | 16 998 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `docs/desktop-updates.md` | 25 163 | Документация | Да — ручная справка |
| `docs/images/manticore-logo-dark.png` | 40 815 | Иллюстрация документации | Да — ручная справка |
| `docs/images/manticore-logo.png` | 266 392 | Иллюстрация документации | Да — ручная справка |
| `docs/management.md` | 9 406 | Документация | Да — ручная справка |
| `docs/student-transfer.md` | 10 288 | Документация | Да — ручная справка |
| `docs/windows-release-pipeline-audit.md` | 13 864 | Документация | Да — ручная справка |
| `email_validation.py` | 16 072 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `install_and_run_windows.ps1` | 8 935 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `requirements-desktop.lock` | 654 | Список/lock зависимостей | Да — установка/CI/сборка |
| `requirements-desktop.txt` | 108 | Список/lock зависимостей | Да — установка/CI/сборка |
| `requirements-prod.txt` | 37 | Список/lock зависимостей | Да — установка/CI/сборка |
| `requirements.txt` | 129 | Список/lock зависимостей | Да — установка/CI/сборка |
| `start_one_click.bat` | 185 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `static/css/legacy.css` | 44 517 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/modern.css` | 40 604 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/abiturients.css` | 6 976 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/abiturients_to_students.css` | 10 736 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/add_group.css` | 4 568 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/add_user.css` | 2 111 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/admin_panel.css` | 4 644 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/approve_users.css` | 1 202 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/audit_logs.css` | 243 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/backups.css` | 309 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/campaigns.css` | 3 378 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/data_checks.css` | 4 223 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/duplicates.css` | 410 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/duplicates_abiturients.css` | 336 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/edit_abiturient.css` | 3 436 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/edit_conflict.css` | 1 255 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/edit_student.css` | 4 418 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/edit_user.css` | 2 111 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/enrollment_order_student_roster.css` | 9 606 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/enrollment_order_upload.css` | 1 193 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/file_work.css` | 11 082 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/index.css` | 4 113 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/login.css` | 2 926 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/login_conflicts.css` | 569 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/login_rules_setup.css` | 3 396 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/manual_create.css` | 6 665 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/migration_wizard.css` | 1 982 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/person_card.css` | 3 676 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/register.css` | 851 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/search.css` | 479 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/student_transfer_timeline.css` | 7 606 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/students.css` | 2 782 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/pages/students_list.css` | 7 029 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/css/tokens.css` | 4 557 | CSS веб-интерфейса | Да — шаблоны Flask |
| `static/documentation.css` | 35 691 | Ресурс встроенной документации | Да — страница документации |
| `static/documentation.js` | 6 159 | Ресурс встроенной документации | Да — страница документации |
| `static/documentation/dashboard-test-stand.png` | 42 167 | Скриншот встроенной документации | Да — страница документации |
| `static/documentation/file-work-test-stand.png` | 67 925 | Скриншот встроенной документации | Да — страница документации |
| `static/documentation/migration-test-stand.png` | 54 212 | Скриншот встроенной документации | Да — страница документации |
| `static/favicon.ico` | 130 777 | Графический ресурс веб-интерфейса | Да — шаблоны/статические маршруты |
| `static/images/manticore-head.png` | 1 633 151 | Графический ресурс веб-интерфейса | Да — шаблоны/статические маршруты |
| `static/images/manticore-logo-dark.png` | 40 815 | Графический ресурс веб-интерфейса | Да — шаблоны/статические маршруты |
| `static/images/manticore-logo.png` | 266 392 | Графический ресурс веб-интерфейса | Да — шаблоны/статические маршруты |
| `static/js/app.js` | 17 913 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/contingent.js` | 2 328 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/desktop-settings.js` | 2 591 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/desktop-source.js` | 4 945 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/desktop-updater.js` | 20 480 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/enrollment-order-roster.js` | 10 206 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/live-search.js` | 11 504 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/modern-ui.js` | 6 958 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/row-actions.js` | 4 912 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/student-transfer.js` | 9 289 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/students-migration-datepicker.js` | 16 899 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/table-selection.js` | 5 701 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/theme.js` | 592 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/js/user-menu.js` | 836 | JavaScript веб-интерфейса | Да — шаблоны Flask |
| `static/students_template.csv` | 109 | Пользовательский шаблон импорта | Да — маршрут/ссылка скачивания |
| `static/template.xlsx` | 9 616 | Пользовательский шаблон импорта | Да — маршрут/ссылка скачивания |
| `sync_app_metadata.py` | 1 402 | Python-модуль приложения | Да — runtime, инструменты или импорт |
| `templates/_campaign_switcher.html` | 601 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_contingent.html` | 10 522 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_csrf_field.html` | 68 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_developer_links.html` | 536 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_management_about.html` | 933 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_management_admin.html` | 1 483 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_management_settings.html` | 4 925 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_student_transfer_timeline.html` | 6 462 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/_ui_macros.html` | 758 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/abiturients.html` | 18 718 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/abiturients_to_students.html` | 25 525 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/add_group.html` | 10 286 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/add_user.html` | 2 170 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/admin_panel.html` | 19 082 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/approve_users.html` | 1 267 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/audit_logs.html` | 1 440 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/backups.html` | 2 252 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/base.html` | 18 176 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/campaigns.html` | 5 856 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/data_checks.html` | 4 501 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/documentation.html` | 45 587 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/duplicates.html` | 590 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/duplicates_abiturients.html` | 2 526 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/edit_abiturient.html` | 4 804 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/edit_conflict.html` | 1 768 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/edit_student.html` | 8 666 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/edit_user.html` | 2 392 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/enrollment_order_student_roster.html` | 18 544 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/enrollment_order_upload.html` | 3 716 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/fallback_changes.html` | 1 208 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/file_work.html` | 40 811 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/index.html` | 4 470 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/login.html` | 2 183 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/login_conflicts.html` | 2 460 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/login_rules_setup.html` | 8 678 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/management.html` | 634 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/manual_create.html` | 10 383 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/migration_wizard.html` | 3 456 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/person_card.html` | 2 490 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/register.html` | 996 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/search.html` | 1 561 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/students.html` | 8 708 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/students_duplicates.html` | 1 278 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/students_expelled.html` | 4 974 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/students_list.html` | 10 640 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `templates/students_upload.html` | 1 218 | Jinja-шаблон Flask | Да — серверный рендеринг |
| `tests/fixtures/contingent/department-2026-2027.docx` | 39 501 | Синтетическая тестовая фикстура | Да — `tests/test_contingent.py` |
| `tests/fixtures/contingent/single.docx` | 36 843 | Синтетическая тестовая фикстура | Да — `tests/test_contingent.py` |
| `tests/frontend/desktop_updater.test.cjs` | 9 885 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/live_search.test.cjs` | 4 418 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/management.test.cjs` | 2 453 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/row_actions.test.cjs` | 5 381 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/search_shortcut.test.cjs` | 14 396 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/serve_layout_audit.py` | 2 146 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/student_transfer.test.cjs` | 8 653 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/students_migration_datepicker.test.cjs` | 2 940 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/frontend/upload_layout.browser.js` | 4 431 | Frontend-тест/стенд | Да — тестовый набор |
| `tests/test_app.py` | 275 667 | Python-тест | Да — `unittest discover` |
| `tests/test_app_metadata.py` | 10 263 | Python-тест | Да — `unittest discover` |
| `tests/test_contingent.py` | 17 369 | Python-тест | Да — `unittest discover` |
| `tests/test_db_safety.py` | 7 485 | Python-тест | Да — `unittest discover` |
| `tests/test_desktop_releases.py` | 4 534 | Python-тест | Да — `unittest discover` |
| `tests/test_desktop_updater.py` | 18 968 | Python-тест | Да — `unittest discover` |
| `tests/test_email_validation.py` | 6 223 | Python-тест | Да — `unittest discover` |
| `tests/test_frontend_keyboard.py` | 3 131 | Python-тест | Да — `unittest discover` |
| `tests/test_release_publishing.py` | 11 188 | Python-тест | Да — `unittest discover` |
| `tests/test_release_signatures.py` | 2 456 | Python-тест | Да — `unittest discover` |
| `tests/test_student_transfer_frontend.py` | 681 | Python-тест | Да — `unittest discover` |
| `tests/test_update_app.py` | 4 262 | Python-тест | Да — `unittest discover` |
| `tests/test_windows_client.py` | 41 242 | Python-тест | Да — `unittest discover` |
| `tests/test_windows_external_links.py` | 2 111 | Python-тест | Да — `unittest discover` |
| `update.sh` | 1 698 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `update_and_run_windows.bat` | 194 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `update_app.py` | 18 897 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `update_windows.bat` | 176 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `update_windows.ps1` | 1 739 | Скрипт запуска/обновления | Да — пользовательский/операторский запуск |
| `uploads/.gitkeep` | 111 | Маркер runtime-каталога загрузок | Да — сохраняет структуру; данные не отслеживаются |

## Найденные отслеживаемые артефакты

| Путь | Тип | Решение этапа 0 |
|---|---|---|
| `static/students_template.csv` | CSV-шаблон | Оставить: скачивается со страницы импорта |
| `static/template.xlsx` | XLSX-шаблон | Оставить: отдаётся маршрутом Flask |
| `tests/fixtures/contingent/department-2026-2027.docx` | Тестовая фикстура | Оставить после проверки обезличивания на этапе 1 |
| `tests/fixtures/contingent/single.docx` | Тестовая фикстура | Оставить после проверки обезличивания на этапе 1 |

Других совпадений с заданным перечнем артефактов в `git ls-files` не найдено.
