#!/usr/bin/env bash
# Развёртывание RNP-дашборда на Ubuntu VPS (Beget). Запускать от root.
# Использование:   DOMAIN=твой-домен.ру bash setup.sh
set -euo pipefail

APP_DIR=/opt/rnp-dashboard
DATA_DIR=/opt/rnp-data
REPO=https://github.com/gagarinadss-hub/rnp-dashboard.git
DOMAIN="${DOMAIN:-}"

echo "==> 1/6 Пакеты системы"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git nginx curl

echo "==> 2/6 Код проекта"
mkdir -p "$DATA_DIR"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi

echo "==> 3/6 Python-окружение и зависимости"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> 4/6 Файл .env (если ещё нет)"
if [ ! -f "$DATA_DIR/.env" ]; then
  cp "$APP_DIR/deploy/beget/.env.example" "$DATA_DIR/.env"
  echo "    создан $DATA_DIR/.env — при необходимости поправь"
fi

echo "==> 5/6 systemd-сервис (автозапуск + перезапуск)"
cp "$APP_DIR/deploy/beget/rnp-dashboard.service" /etc/systemd/system/rnp-dashboard.service
systemctl daemon-reload
systemctl enable rnp-dashboard

echo "==> 6/6 nginx"
if [ -n "$DOMAIN" ]; then
  sed "s/__DOMAIN__/$DOMAIN/g" "$APP_DIR/deploy/beget/rnp-dashboard.nginx.conf" \
    > /etc/nginx/sites-available/rnp-dashboard
  ln -sf /etc/nginx/sites-available/rnp-dashboard /etc/nginx/sites-enabled/rnp-dashboard
  rm -f /etc/nginx/sites-enabled/default
  nginx -t && systemctl reload nginx
else
  echo "    DOMAIN не задан — nginx настрою позже (см. DEPLOY.md)"
fi

echo ""
echo "================ БАЗОВАЯ УСТАНОВКА ГОТОВА ================"
echo "Осталось:"
echo "  1) положить credentials.json -> $APP_DIR/credentials.json"
echo "  2) положить launches.db      -> $DATA_DIR/launches.db"
echo "  3) systemctl start rnp-dashboard"
echo "  4) SSL:  certbot --nginx -d ${DOMAIN:-твой-домен}"
echo "Проверка:  curl -s http://127.0.0.1:8000/api/health"
echo "========================================================"
