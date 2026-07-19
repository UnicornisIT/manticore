#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${1:-/srv/manticore}
DOMAIN=${2:-example.com}
SERVICE_NAME=manticore
VENV_DIR="$APP_DIR/.venv"
NGINX_AVAILABLE="/etc/nginx/sites-available/$SERVICE_NAME"
NGINX_ENABLED="/etc/nginx/sites-enabled/$SERVICE_NAME"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_USER=${DEPLOY_USER:-www-data}

show_admin_password_notice() {
  local password="$1"
  cat <<EOF

============================================================
ВАЖНО / IMPORTANT
Локальный администратор: admin
Admin password: $password
RU: Сохраните этот пароль. При первом входе обязательно смените пароль администратора.
EN: Save this password. You must change the admin password after the first login.
============================================================

EOF
}

generate_secret_key() {
  python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

generate_admin_password() {
  python3 -c 'import secrets, string; chars = string.ascii_letters + string.digits; print("".join(secrets.choice(chars) for _ in range(20)))'
}

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

if [[ ! -f "$APP_DIR/.env" ]]; then
  SECRET_KEY="$(generate_secret_key)"
  ADMIN_PASSWORD="$(generate_admin_password)"
  cat > "$APP_DIR/.env" <<EOF
SECRET_KEY=$SECRET_KEY
ADMIN_DEFAULT_PASSWORD=$ADMIN_PASSWORD
UPLOAD_FOLDER=uploads
DB_FILENAME=baze.db
DEFAULT_CAMPAIGN_YEAR=2026
LEGACY_CAMPAIGN_YEAR=2025
FLASK_ENV=production
APP_HOST=127.0.0.1
APP_PORT=8000
APP_DEBUG=false
EOF
  chmod 640 "$APP_DIR/.env"
  echo "Generated local admin password and saved it in $APP_DIR/.env."
  show_admin_password_notice "$ADMIN_PASSWORD"
fi

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python3 -m pip install --upgrade pip
pip install -r requirements-prod.txt

mkdir -p "$APP_DIR/uploads"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
chmod -R 750 "$APP_DIR/uploads"

cat > "$SERVICE_FILE" <<'EOF'
[Unit]
Description=manticore Flask app
After=network.target

[Service]
User=%RUN_USER%
Group=%RUN_USER%
WorkingDirectory=%APP_DIR%
EnvironmentFile=-%APP_DIR%/.env
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
    client_max_body_size ${MAX_UPLOAD_SIZE_MB:-16}m;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300;
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
