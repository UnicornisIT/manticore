<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/manticore-logo-dark.png">
    <img src="docs/images/manticore-logo.png" alt="Manticore" width="420">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/UnicornisIT/manticore/actions/workflows/ci.yml"><img src="https://github.com/UnicornisIT/manticore/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/UnicornisIT/manticore/actions/workflows/security.yml"><img src="https://github.com/UnicornisIT/manticore/actions/workflows/security.yml/badge.svg?branch=main" alt="Security"></a>
  <a href="https://github.com/UnicornisIT/manticore/releases"><img src="https://img.shields.io/github/v/release/UnicornisIT/manticore?include_prereleases&sort=semver" alt="Release"></a>
  <img src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white" alt="Python 3.11">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/UnicornisIT/manticore" alt="License: MIT"></a>
</p>

<p align="center">
  <b>Русский</b> · <a href="README.en.md">English</a>
</p>

# Manticore

Manticore — Flask/SQLite-приложение и Windows-клиент для приёмной комиссии:
импорт абитуриентов, сверка приказов, управление студентами и группами,
проверка качества данных и подготовка выгрузок Moodle.

> **Статус: alpha.** Проект уже умеет работать с персональными и боевыми
> данными, но пока не считается полностью стабилизированным. Перед внедрением
> организация должна проверить резервное копирование и восстановление,
> разграничение ролей, HTTPS/VPN, файловые права и план обновления. Не размещайте
> приложение напрямую в интернете и не используйте единственную копию базы.

## Возможности

- импорт и предварительная проверка Excel/CSV;
- приёмные кампании, дубли, конфликты логинов и архивирование;
- двухэтапный перенос абитуриентов в студентов через сверку с приказом;
- роли пользователей, журнал действий и резервные копии SQLite;
- Windows desktop-режим с локальной или серверной базой;
- безопасная заглушка `cron` в поле Moodle: реальные пароли студентов Manticore
  не принимает и не хранит.

## Быстрый старт

Требуется CPython 3.11 x64.

### Windows

1. Скачайте репозиторий или релиз.
2. Запустите `start_one_click.bat`.
3. Введите первоначальный пароль администратора в скрытом приглашении.
4. Откройте показанный локальный адрес и смените пароль при первом входе.

### Вручную

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

На Windows используйте `py -3.11 -m venv .venv` и `.venv\Scripts\python.exe`.

Скопируйте `.env.example` в `.env`, задайте сильные `SECRET_KEY` и
`ADMIN_DEFAULT_PASSWORD`, затем запустите:

```bash
.venv/bin/python app.py
```

Подробные команды для Windows, Linux и production описаны в
[инструкции по установке](docs/installation.md).

## Документация

- [Установка и настройка](docs/installation.md)
- [Роли и рабочий процесс](docs/roles.md)
- [Форматы импортируемых и экспортируемых файлов](docs/file-formats.md)
- [Linux-развёртывание и эксплуатация](docs/deployment.md)
- [Безопасность и остаточные риски](docs/security.md)
- [Windows desktop и обновления](docs/desktop-updates.md)
- [Обновление приложения](docs/updates.md)
- [Структура проекта](docs/project-structure.md)
- [История изменений](CHANGELOG.md) и [заметки к выпускам](docs/releases/)
- [Участие в разработке](CONTRIBUTING.md)
- [Политика сообщения об уязвимостях](SECURITY.md)

## Структура проекта

```text
app.py                     сборка Flask-приложения и совместимая бизнес-обвязка
config.py                  типизированная runtime-конфигурация
db/                        реестр версионных миграций SQLite
routes/                    Blueprints: auth, abiturients, students, files, admin,
                           migration и management
services/                  импорт файлов и чистая логика сверки
contingent.py              совместимый импорт services/reconciliation.py
contingent_web.py          веб-обвязка сверки контингента
db_safety.py               целостность, версии, backup/restore SQLite
email_validation.py        синтаксис, DNS и справочник почтовых доменов
desktop_releases.py        разрешённые релизы Windows-клиента
app_metadata.py            единый источник публичных контактов
sync_app_metadata.py       синхронизация контактов README
data/                      статический справочник email_providers.json, не runtime-БД
desktop/                   Windows-клиент, упаковка и release tooling
deploy/                    systemd, Nginx и sudoers-примеры
scripts/                   канонические скрипты запуска, обновления и проверок
static/, templates/        интерфейс и статические шаблоны файлов
tests/                     Python- и frontend-регрессии
VERSION                    единая версия приложения
RELEASE_CHECKLIST.md       контрольный список выпуска
```

Полная карта файлов и назначений приведена в
[docs/project-structure.md](docs/project-structure.md).

## Безопасная эксплуатация

- используйте HTTPS reverse proxy или доверенный VPN для любого сетевого доступа;
- храните `.env`, SQLite-базы, загрузки и резервные копии вне Git;
- устанавливайте Linux production-зависимости из `requirements-prod.lock`;
- запускайте `python -m unittest discover -s tests` перед обновлением;
- проверяйте `pip-audit` для prod- и desktop-lock-файлов;
- помните, что резервные копии ограничены файловыми правами, но пока не
  шифруются самим приложением.

## Лицензия

Проект распространяется по лицензии [MIT](LICENSE).

<!-- app-metadata:start (generated by sync_app_metadata.py) -->
### Разработчик

Разработчик: UnicornisIT

[GitHub](https://github.com/UnicornisIT) · [Telegram](https://t.me/unicornis_pulse) · [Email](mailto:dev-eva@outlook.com)
<!-- app-metadata:end -->
