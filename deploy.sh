#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${1:-/srv/logins_moodle_v3.5}
DOMAIN=${2:-example.com}
SERVICE_NAME=logins_moodle
VENV_DIR="$APP_DIR/.venv"
NGINX_AVAILABLE="/etc/nginx/sites-available/$SERVICE_NAME"
NGINX_ENABLED="/etc/nginx/sites-enabled/$SERVICE_NAME"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_USER=${DEPLOY_USER:-www-data}

if [[ "$EUID" -ne 0 ]]; then
  echo "Запустите скрипт от root или через sudo"
  exit 1
fi

if id -u "$DEPLOY_USER" >/dev/null 2>&1; then
  RUN_USER="$DEPLOY_USER"
else
  RUN_USER=${SUDO_USER:-$(id -un)}
fi

mkdir -p "$APP_DIR"

if [[ "$CURRENT_DIR" != "$APP_DIR" ]]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='.git' --exclude='.venv' --exclude='uploads/baze.db' --exclude='uploads/*.xlsx' "$CURRENT_DIR/" "$APP_DIR/"
  else
    cp -a "$CURRENT_DIR/." "$APP_DIR/"
    rm -rf "$APP_DIR/.git"
    rm -rf "$APP_DIR/.venv"
    rm -f "$APP_DIR/uploads/baze.db"
  fi
fi

cd "$APP_DIR"

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python3 -m pip install --upgrade pip
pip install -r requirements.txt

mkdir -p "$APP_DIR/uploads"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
chmod -R 750 "$APP_DIR/uploads"

cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=Flask app logins_moodle
After=network.target

[Service]
User=%RUN_USER%
Group=%RUN_USER%
WorkingDirectory=%APP_DIR%
Environment="PATH=%VENV_DIR%/bin"
ExecStart=%VENV_DIR%/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sed -i "s|%RUN_USER%|$RUN_USER|g" "$SERVICE_FILE"
sed -i "s|%APP_DIR%|$APP_DIR|g" "$SERVICE_FILE"
sed -i "s|%VENV_DIR%|$VENV_DIR|g" "$SERVICE_FILE"

if [[ ! -f "$NGINX_AVAILABLE" ]]; then
  cat > "$NGINX_AVAILABLE" <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias $APP_DIR/static/;
    }
}
EOF
fi

ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

if nginx -t; then
  systemctl restart nginx
fi

echo "Deploy завершен. Откройте http://$DOMAIN или http://<IP>:80"
