#!/bin/bash
# deploy.sh — запускать на сервере после git pull
set -e

APP_DIR="/opt/rnp"

echo "==> Обновляю зависимости..."
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Перезапускаю сервис..."
sudo systemctl restart rnp

echo "==> Статус:"
sudo systemctl status rnp --no-pager -l

echo "✅ Деплой завершён"
