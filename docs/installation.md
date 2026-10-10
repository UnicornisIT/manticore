# Установка и настройка

## Требования

- CPython 3.11 x64;
- Windows 10/11 либо современный Linux;
- интернет для первой установки зависимостей;
- для production: отдельный системный пользователь, systemd и Nginx.

`pyproject.toml` содержит extras `prod`, `desktop` и `dev`. Совместимые
`requirements*.txt` сохранены, а воспроизводимые окружения выпуска задают
`requirements-prod.lock` и `requirements-desktop.lock`.

## Windows: автоматический запуск

Запустите `start_one_click.bat`. Обёртка вызывает канонический
`scripts/start_one_click.bat`, который находит или устанавливает Python 3.11,
создаёт `.venv`, устанавливает `requirements.txt` и запускает приложение.
Первоначальный пароль администратора вводится через скрытое приглашение и не
печатается в консоль.

## Ручная установка

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe app.py
```

Linux:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python app.py
```

Для development можно использовать `python -m pip install -e ".[dev]"`.

## Обязательные настройки `.env`

- `SECRET_KEY` — случайная строка не короче 32 символов;
- `ADMIN_DEFAULT_PASSWORD` — сильный временный пароль не короче 12 символов;
- `UPLOAD_FOLDER` и `DB_FILENAME` — каталог и имя локальной SQLite-базы;
- `DEFAULT_CAMPAIGN_YEAR` и `LEGACY_CAMPAIGN_YEAR` — рабочие годы;
- `BACKUP_RETENTION_DAYS` — срок хранения автоматически создаваемых бэкапов;
- `APP_HOST` — оставьте `127.0.0.1`, если нет HTTPS reverse proxy или VPN.

Остальные параметры и безопасные значения приведены в `.env.example`.

## Первый вход

Пользователь `admin` создаётся только при наличии `ADMIN_DEFAULT_PASSWORD`.
После первого входа приложение принудительно требует заменить этот пароль, а
затем завершить настройку правил генерации логинов. Секреты не записываются в
журнал действий.

## Локальная сеть

Привязка к адресу, отличному от loopback, вызывает предупреждение. Обычный HTTP
передаёт учётные и персональные данные без транспортного шифрования. Для сети
обязателен HTTPS через reverse proxy либо доверенный VPN; затем включите
`SESSION_COOKIE_SECURE=true`, а HSTS — только после проверки HTTPS.
