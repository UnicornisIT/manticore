# Аудит Desktop updater — 6–7 сентября 2026

## Результат и границы проверки

Существующий updater доработан для прямого обновления из официального стабильного GitHub Latest. Проверка, скачивание с прогрессом, подтверждение и установка разделены. Реальный локальный переход между упакованными версиями **1.0.0 → 1.5.0** прошёл: старое окно/процессы завершились, Inno Setup заменил программу, помощник запустил новую версию, её журнал подтвердил успешный перезапуск 1.5.0. Тестовые данные сохранились. Изолированная тестовая установка затем удалена; данные остались.

Это **не полный production GitHub E2E**: в локальном интеграционном драйвере ответ GitHub и транспорт скачивания заменены фикстурой/потоком настоящего локального установщика. Сам downloader, SHA-256, прогресс, установщик, помощник ожидания/перезапуска и упакованные приложения использовались настоящие. Клик через UI старого установленного приложения и скачивание B с опубликованного GitHub Release в этом сценарии не выполнялись.

Публичный API проверен без PAT: Latest — `v0.0.1-alpha`, `draft=false`, `prerelease=false`, `immutable=false`; asset `Manticore-Setup-0.0.1-alpha.exe`, 47 622 952 байта, digest SHA-256 присутствует. Такой тег новый stable-клиент отклоняет независимо от флага prerelease. Тестовые релизы не публиковались, GitHub Actions не запускался, `VERSION` основной рабочей копии сохранён: `0.0.1-alpha`.

Для реального использования нужна публикация стабильной версии обычным release workflow. Возможность прямого обновления реализована и проверена локально; окончательную приёмку скачивания из GitHub на установленном production-клиенте следует выполнить после такого выпуска. Старые установленные бинарники не получают исправления updater до установки версии, содержащей новый код.

## Почему прежний flow был неполным

Клиент зависел от серверного административного разрешения и обязательной подписи/Immutable Release. До запуска окна выполнялась синхронная проверка. Загрузка и установка были объединены, прогресс не передавался в интерфейс, результат установщика/перезапуск не контролировались достаточно явно. Текущий публичный alpha Latest не удовлетворяет политике stable.

Стек: Python 3.11 → Flask/Jinja + pywebview/WebView2 → PyInstaller one-file → Inno Setup x64. Electron/Tauri, preload, electron-updater отсутствуют. Сохранён существующий Python updater и его загрузчик. Версия из `VERSION`; точная проверка stable-тега в CI, PE version resource и installer metadata генерируются из этого файла.

## Новый flow

Запуск окна → одна отложенная проверка → GitHub stable Latest → available/current/error → явное скачивание → прогресс и SHA-256/при необходимости Authenticode → downloaded → отдельный клик и нативное подтверждение → ожидание завершения клиента и PyInstaller bootloader → Inno Setup в тот же каталог/scope → ожидание результата → запуск новой версии.

Нет автоматического скачивания, регулярного GitHub polling, downgrade или установки предварительных версий. JS API не принимает URL, пути и команды. Dev-сборка не предлагает установку. Недоступность сервера Manticore не блокирует фоновую проверку GitHub: уведомление доступно и на встроенной странице ошибки соединения. Старый remote сервер получает встроенный updater UI от Desktop. Настройки, БД и профиль хранятся отдельно от программы.

## Проверки

| Проверка | Результат | Основание / граница |
|---|---|---|
| Lint | NOT RUN | Отдельного настроенного lint в проекте нет; не подменён syntax check |
| Typecheck | NOT RUN | Отдельного typecheck нет |
| Python/JS syntax | PASS | compileall/py_compile, node --check |
| Зависимости | PASS | pip check, зафиксированный Windows Python 3.11 lockfile |
| Tests | PASS | 159 unittest, включая запускаемые ими Node frontend regressions; после последней правки также повторно прошли все 17 targeted updater tests |
| Web build | N/A | Flask/Jinja + static, отдельного сборщика нет; шаблоны проверены тестами и вошли в bundle |
| Desktop build | PASS | Настоящий PyInstaller EXE с VERSION и PE ProductVersion |
| Production installer | PASS | Inno Setup EXE, без сертификата, с metadata и SHA256SUMS |
| Проверка GitHub | PASS, ограниченно | Публичный API доступен; обнаружен некорректный для stable alpha Latest |
| Update available | PASS | Контрактные тесты и локальный интеграционный драйвер |
| No update / downgrade | PASS | Равная/более старая Latest, SemVer и build metadata |
| Draft/alpha/beta/rc | PASS | Отклонение даже при ошибочном флаге stable у alpha |
| Download | PASS, локальный транспорт | Настоящий файл Inno проходит потоковый downloader; сеть заменена локальным потоком |
| Progress | PASS | Реальный downloader до 100%, frontend state tests |
| Install | PASS | Настоящий Inno Setup заменил изолированную установленную A на B |
| Restart | PASS | Настоящая упакованная B запущена помощником; версия 1.5.0 подтверждена журналом |
| User data | PASS, проверенный набор | Конфигурация, секрет сессии, путь БД, запись SQLite, файл предпочтений; реальная смена темы в UI отдельно не проверялась |
| Jump через версии | PASS, локально | 1.0.0 → 1.5.0 без промежуточных установщиков |
| Закрытие / single instance | PASS, локально | Завершение A/B; Inno отказывается работать при занятом mutex |
| Uninstall | PASS, изолированно | Программа удалена; конфигурация и SQLite остались |
| Авторизация, upload/download, настройки | PASS, автоматические тесты | Существующие backend/bridge regressions; физические файловые диалоги заново не проверялись |
| Внешние ссылки | PASS, автоматические тесты | Сохранены настройки открытия системным браузером |
| WebSocket / tray | N/A | Отдельной такой функциональности в проверенном Desktop нет |
| HTTP 403/404/429/500, timeout, metadata | PASS | Ошибки и восстановление проверены тестами |
| Checksum, размер, disk space, tampering | PASS | Ошибка без установки, удаление частичного файла |
| Отмена / блокировка установки | PASS, автоматические тесты | Отмена нативного подтверждения; ошибка запуска без закрытия клиента |
| Реальная UAC cancellation / SmartScreen | NOT RUN | Требует отдельной интерактивной Windows-проверки |
| Подпись | PASS тестов; реальная подпись NOT RUN | Пин сертификата и bad digest проверены; сертификат для этой сборки не предоставлен |
| GitHub Actions публикация | NOT RUN | Workflow изменён, но тег не отправлялся и релиз не публиковался |
| Полный production GitHub A → B | NOT RUN | Сейчас отсутствует подходящий публичный stable Latest; использована изолированная схема |
| git diff --check | PASS | Без ошибок whitespace |

При нескольких локальных компиляциях Inno временно отказывал в обновлении ресурса EXE (EndUpdateResource, 110). Повторная компиляция в отдельной staging-папке проходила; staging добавлен в build-скрипт, чтобы не заменять итоговый installer до успешного завершения. Это не гарантия отсутствия внешних блокировок Windows. Антивирус и защита Windows не отключались. Первая попытка удаления тестовой B была слишком ранней: процесс ещё завершался. После его штатного завершения uninstall успешно повторён; принудительное завершение для успешного сценария обновления A → B не потребовалось.

## GitHub Actions и assets

Flow: stable tag/version check → Python/Node → locked pip + pip check → syntax/tests → PyInstaller → Inno → optional code signing → metadata → provenance → build artifact → draft → загрузка и сверка assets → stable Latest. Опубликованные релизы не перезаписываются при повторном запуске; повторяемое завершение разрешено для draft.

Assets: `Manticore-Setup-X.Y.Z.exe`, `desktop-update.json`, `SHA256SUMS.txt`. Клиент использует GitHub API asset digest; JSON — дополнительный манифест для проверки выпуска. Electron `latest.yml`/blockmap этому стеку не нужны. Code signing подключается через `WINDOWS_CERTIFICATE_BASE64` и `WINDOWS_CERTIFICATE_PASSWORD`, секреты не входят в клиент или исходники.

## Файлы, изменённые в этой задаче

В рабочей копии уже были изменения других задач. Ниже перечислены именно файлы, затронутые работой над updater; остальные не откатывались.

| Файл | Изменение |
|---|---|
| `desktop/windows_client.py` | Прямой stable check, состояния/IPC, фоновые операции, progress, проверки, native confirmation, ожидание процессов, результат установки и перезапуск |
| `desktop_releases.py` | Корректный SemVer, отдельный stable-client режим существующего валидатора/API, точная ссылка asset, понятные сетевые ошибки; legacy approval сохранён |
| `static/js/desktop-updater.js` | UI состояний, прогресс, отдельные действия, локальный IPC polling, уведомление и совместимость старого remote UI |
| `static/js/desktop-settings.js` | Удалён прежний объединённый update handler; остальные настройки сохранены |
| `templates/_management_settings.html` | Текст stable-источника, отдельная download-кнопка и progress |
| `templates/base.html` | Подключение общего updater UI |
| `templates/admin_panel.html` | Новый Desktop переходит в настройки обновления; старый API flow сохранён только для старых клиентов |
| `desktop/ui/setup.html` | Убран ввод неиспользуемого сервера обновлений, указано получение из GitHub |
| `desktop/Manticore.iss` | Silent installer не запускает второй экземпляр через [Run] |
| `desktop/Manticore.spec` | PE version resource из VERSION |
| `desktop/release_tools.py` | Проверка stable tag, генерация PE версии, JSON metadata и SHA256SUMS |
| `build_windows_desktop.ps1` | Optional signing policy, locked dependencies, проверка exit codes, безопасная staging-сборка installer, генерация metadata |
| `requirements-desktop.lock` | Фиксированные версии Windows Python-зависимостей |
| `.github/workflows/windows-release.yml` | Проверки перед сборкой, необязательная подпись, draft/upload/verify/publish Latest |
| `.gitignore` | Разрешён lockfile; исключены локальные тестовые данные/логи |
| `tests/test_windows_client.py` | Обновлены ожидания прямого API и установки только готового файла; проверка helper |
| `tests/test_windows_external_links.py` | Заглушка события pywebview, сохранены существующие проверки |
| `tests/test_desktop_updater.py` | SemVer, stable validation, сбои, целостность, state machine, подпись, подтверждение, фоновый запуск |
| `tests/frontend/desktop_updater.test.cjs` | JS availability/progress/install/dev/error |
| `tests/test_frontend_keyboard.py` | Включение updater JS regressions в общий unittest |
| `README.md` | Обновлены инструкции сборки и выпуска |
| `docs/desktop-updates.md` | Архитектура, выпуск, assets, доверие, тестирование и логи |
| `DESKTOP_UPDATES_AUDIT.md` | Этот отчёт |

Локальные доказательства (не коммитятся): `.ui-audit-data/desktop-updater-tests.log`, `desktop-updater-build.log`, `desktop-updater-final-bundle.log`, `desktop-updater-final-installer.log`, `desktop-updater-integration.json`, `updater-profile/ManticoreUpdaterValidation/logs/client.log` и `installer.log`. Фикстуры двух сборок находятся в `build/updater-validation`; их AppId, имя папки данных и mutex отличаются от production. Данные содержат только созданные для теста записи.
