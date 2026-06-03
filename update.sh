#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${1:-/srv/logins_moodle_v3.5}
SERVICE_NAME=logins_moodle
VENV_DIR="$APP_DIR/.venv"

if [[ "$EUID" -ne 0 ]]; then
  echo "Запустите скрипт от root или через sudo"
  exit 1
fi

cd "$APP_DIR"

if [[ -d .git ]]; then
  git pull --ff-only || true
fi

if [[ -d "$VENV_DIR" ]]; then
  source "$VENV_DIR/bin/activate"
  python3 -m pip install --upgrade pip
  pip install -r requirements.txt
else
  echo "Виртуальное окружение не найдено: $VENV_DIR"
  exit 1
fi

systemctl restart "$SERVICE_NAME"

if nginx -t; then
  systemctl reload nginx
fi

echo "Обновление выполнено. Сервис $SERVICE_NAME перезапущен."
