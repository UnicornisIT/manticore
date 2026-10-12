# Аудит Windows release pipeline — 2026-09-07

## Причина и исправление

В `.github/workflows/windows-release.yml` draft создавался и получал assets, после чего workflow искал его через REST `/releases/tags/{tag}`. Этот поиск не подходит для pending tag draft-релиза. GitHub CLI использует дополнительный GraphQL-поиск draft и затем REST по ID: [реализация FetchRelease / fetchDraftRelease](https://github.com/cli/cli/blob/trunk/pkg/cmd/release/shared/fetch.go). Поэтому 404 на прежнем шаге не свидетельствовал об ошибке сборки или подписи.

Теперь поиск выполняется через `gh release view --json databaseId,assets,isDraft,tagName`. Проверяются точный tag, draft-флаг и положительный неизменный ID. Ошибки CLI завершают шаг. Только ответ `release not found` разрешает попытку создания; остальные ошибки первичного поиска останавливают процесс. Ошибка создания также останавливает процесс, без удаления релиза или обходного повторного создания.

Изменены только workflow, интеграционный тест `tests/test_release_publishing.py` и этот отчёт. Форматы metadata, VERSION, installer, приложение и updater не изменены.

## Последовательность и повторный запуск

1. Проверить локальные VERSION/tag, repository, schema/version/tag/asset metadata, SHA-256, размер и SHA256SUMS.
2. Найти draft текущего тега либо создать его с `--draft --verify-tag`.
3. Повторно проверить draft/ID; загрузить только три ожидаемых имени с `--clobber`.
4. Найти тот же draft через CLI, проверить каждый обязательный asset и его размер. Требуется ровно один EXE. Другие необязательные файлы не удаляются.
5. Скачать installer, metadata и sums в новый каталог с GUID в RUNNER_TEMP. Сверить размер и SHA-256 каждого файла с локальным. Поскольку локальные metadata/sums уже проверены, побайтовая проверка их хешей также подтверждает соответствие скачанных metadata текущему релизу.
6. Прочитать REST `/releases/{id}` и проверить digest, размер и официальный download URL installer для совместимости с updater. Для draft допустимы текущий tag либо временный сегмент `untagged-<hex>`; host, repository и имя installer должны совпадать точно. Это дополнительная проверка, а не замена вычислению хеша скачанных байтов. Digest в JSON-экспорте CLI не обязателен; отсутствие корректного digest в самом REST API блокирует публикацию, поскольку такой релиз отвергнет текущий клиент.
7. Повторно проверить draft/ID и неизменность идентификаторов, имён, размеров и digest assets. `downloadCount` не сравнивается: его меняет сама проверка.
8. Только затем выполнить публикацию. Для версии с prerelease-суффиксом: `--prerelease --latest=false`; для stable: `--prerelease=false --latest`.

Повторный запуск использует существующий draft. Ни один релиз не удаляется. Published release отклоняется до upload. Concurrency объединяет push и ручной запуск для одного тега. `--clobber` ограничен installer текущей версии, `desktop-update.json` и `SHA256SUMS.txt` этого тега.

## Как повторно использовать v0.0.3-alpha

После размещения исправленного workflow в основной ветке открыть Actions → Windows desktop release → Run workflow, выбрать ветку с исправлением и указать `release_tag: v0.0.3-alpha`. Workflow использует свою исправленную логику, но checkout берёт исходники именно из `refs/tags/v0.0.3-alpha`; VERSION проверяется относительно этого тега.

Новый тег и перенос существующего тега не нужны. Обычный Re-run старого запуска использует прежнюю ревизию workflow и не подхватит исправление: [GitHub о сохранении исходных SHA/ref при Re-run](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs). Кнопка Run workflow требует наличия workflow в default branch: [документация workflow_dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch). Если release уже опубликован, ручной запуск завершится отказом изменять его. Реальное состояние удалённого release в рамках этого аудита не проверялось и не изменялось.

## Проверка 20 пунктов

| Область | Результат |
| --- | --- |
| 1. YAML | Проверен PyYAML 6.0.2; структура trigger, checkout и permissions проверена отдельно. |
| 2. PowerShell | Все 9 run-блоков разобраны PowerShell Parser; release-блок реально исполняется тестами в PowerShell 7. |
| 3. Environment | RELEASE_TAG одинаков для валидации и публикации; GH_REPO закреплён; RUNNER_TEMP используется для уникального каталога. |
| 4. Tag | Checkout существующего полного refs/tags; ранняя SemVer/VERSION-проверка сохранена; перед публикацией точное сравнение. |
| 5. Repository | Все release-команды имеют явный --repo; REST использует закреплённый repo и проверенный ID. |
| 6. Token | GH_TOKEN получает github.token. PAT не добавлен; токены и сертификат не выводятся. |
| 7. Создание | Только draft с --verify-tag; код возврата обязателен. |
| 8. Повтор | Существующий draft переиспользуется; проверяется его ID. |
| 9. Upload | Только три точных имени, --clobber; ошибка блокирует дальнейшие шаги. |
| 10. Assets | Каждое обязательное имя ровно один раз, один EXE, совпадение размеров. |
| 11. SHA-256 | Локальные байты ↔ metadata ↔ sums; скачанные байты ↔ локальные; дополнительно API digest. |
| 12. Размер | Local ↔ metadata ↔ API ↔ download; предел updater 256 MiB, пустой installer запрещён. |
| 13. Metadata | Схема 1 и имена сохранены; проверяются repo/version/tag/asset/size/sha256; скачанная копия совпадает с локальной. |
| 14. Подпись | Порядок sign → metadata сохранён; release_tools, pin, WinVerifyTrust и прежняя политика цепочки доверия не изменены. |
| 15. Prerelease | Alpha/beta/rc публикуются с prerelease=true и latest=false. |
| 16. Stable | Prerelease=false, latest=true; существующий клиент читает только Latest и отклоняет prerelease. |
| 17. Публикация | После всех проверок; отказ edit не выдаёт Release publish: PASS. |
| 18. Ошибки | ErrorActionPreference=Stop, StrictMode; LASTEXITCODE проверяется у каждой gh-команды, включая команды внутри функций. |
| 19. Дубли | Поиск перед созданием, единая concurrency по тегу, отсутствие повторного создания после ошибки. |
| 20. Удаления | release delete отсутствует; заменяются только ожидаемые имена текущего draft. |

`contents: write` нужен для release; прежние `id-token: write` и `attestations: write` сохранены для существующего `actions/attest-build-provenance`. Новые права не добавлены.

## Тесты и границы проверки

- Все 163 ранее существовавших теста: PASS. Включены release metadata/versioning, updater, Windows client, подпись и существующие frontend-тесты.
- Новый модуль: 3 интеграционных теста с 30 сценариями — PASS, включая повторный прогон после уточнения проверки downloadCount. Он извлекает и исполняет настоящий release-блок workflow, подменяя только CLI сетевого сервиса. Проверяются создание/reuse, все каналы, отсутствие digest в экспорте CLI, изменение downloadCount, ошибки команд, published/wrong tag/changed ID, отсутствующие и повреждённые assets, размеры, локальные metadata/sums, API digest/URL, изменение draft/assets и ошибка публикации.
- PyYAML установлен только в игнорируемый каталог `.ui-audit-data/release-audit-deps`; зависимости проекта не менялись.
- YAML, PowerShell и git diff --check: PASS.

Реальную загрузку, скачивание и публикацию GitHub Actions здесь не выполняли; проверка использует фикстуры, не токен. Installer заново не собирался, реальные сертификаты не использовались. Окончательное подтверждение работы с GitHub — запуск исправленного workflow. При задержке появления API digest он безопасно оставит draft неопубликованным; после появления digest можно повторить запуск.

Concurrency и повторные проверки защищают от параллельных запусков этого workflow, но GitHub не предоставляет атомарную транзакцию «проверить assets и опубликовать». Ручное изменение релиза между последней проверкой и edit остаётся внешней гонкой; во время публикации не следует вручную менять этот draft. Временный каталог остаётся на одноразовом runner до его штатной очистки.

## Дополнение по реальному запуску (logs_92329085818.zip)

Запуск 2026-09-07 успешно нашёл draft 383731824, загрузил все assets и проверил скачанные байты. Installer: 46799624 байта; локальный, скачанный и API SHA-256 равны `3b696c1fa2a616cc24577f98dfdc80fc625ee5d335301bfa2208f87b1e53c569`.

Причина следующего отказа — чрезмерно строгая проверка URL в предыдущем исправлении. Прямое чтение API подтвердило, что draft имеет tag `v0.0.3-alpha`, но URL его assets содержит `untagged-60c7d2dd614974ca77b8`. До публикации этот временный сегмент допустим. Workflow теперь принимает его только для проверенного draft текущего тега, с точным официальным repo и именем installer. Ошибки размера, digest и URL разделены для диагностики. Проверки опубликованных URL в desktop updater не изменены.

В тесты добавлены реальный формат временного URL и отказы для другого тега, репозитория, имени файла и query string. Всего теперь 35 сценариев в 3 интеграционных тестах. Сведения выше об отсутствии обращения к реальному API относятся к первоначальному аудиту; при разборе новых логов выполнено только чтение draft API, без изменения релиза.
