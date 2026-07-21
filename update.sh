#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="${SERVICE_NAME:-manticore}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ $# -gt 0 ]]; then
  APP_DIR="$1"
elif [[ "$EUID" -eq 0 && -d /srv/manticore ]]; then
  APP_DIR="/srv/manticore"
else
  APP_DIR="$SCRIPT_DIR"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "Python was not found. Install Python 3.10+ and run this script again."
    exit 1
  fi
fi

if [[ ! -f "$APP_DIR/update_app.py" ]]; then
  echo "update_app.py was not found in $APP_DIR"
  exit 1
fi

REQUIREMENTS="${REQUIREMENTS:-}"
if [[ -z "$REQUIREMENTS" ]]; then
  REQUIREMENTS="requirements.txt"
  if [[ "$EUID" -eq 0 && "$(uname -s)" == "Linux" && -f "$APP_DIR/requirements-prod.txt" ]]; then
    REQUIREMENTS="requirements-prod.txt"
  fi
fi

EXTRA_ARGS=()
if [[ "$EUID" -eq 0 && "$(uname -s)" == "Linux" ]] && command -v systemctl >/dev/null 2>&1; then
  EXTRA_ARGS+=(--restart-systemd --service-name "$SERVICE_NAME")
  if command -v nginx >/dev/null 2>&1; then
    EXTRA_ARGS+=(--reload-nginx)
  fi
else
  echo "Local update mode: system service restart is skipped."
fi

"$PYTHON_BIN" "$APP_DIR/update_app.py" "$APP_DIR" --requirements "$REQUIREMENTS" "${EXTRA_ARGS[@]}"
