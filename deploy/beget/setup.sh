#!/usr/bin/env bash
# Развёртывание RNP-дашборда на Ubuntu VPS (Beget). Запускать от root.
# По IP:      bash setup.sh
# С доменом:  DOMAIN=твой-домен.ру bash setup.sh
set -euo pipefail

APP_DIR=/opt/rnp-dashboard
DATA_DIR=/opt/rnp-data
REPO=https://github.com/gagarinadss-hub/rnp-dashboard.git
DOMAIN="${DOMAIN:-}"
RAILWAY_URL="https://web-production-7fde6.up.railway.app"
DB_TOKEN="rnp-bot-2026"

echo "==> 1/7 Пакеты системы"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git nginx curl

echo "==> 2/7 Код проекта"
mkdir -p "$DATA_DIR"
if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull --ff-only; else git clone "$REPO" "$APP_DIR"; fi

echo "==> 3/7 Python-окружение"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip -q
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "==> 4/7 .env"
[ -f "$DATA_DIR/.env" ] || cp "$APP_DIR/deploy/beget/.env.example" "$DATA_DIR/.env"

echo "==> 5/7 База данных с Railway"
if [ ! -f "$DATA_DIR/launches.db" ]; then
  curl -fsSL "$RAILWAY_URL/api/admin/export-db?token=$DB_TOKEN" -o "$DATA_DIR/launches.db" \
    && echo "    launches.db скачана ($(du -h "$DATA_DIR/launches.db" | cut -f1))" \
    || echo "    ⚠ базу скачать не удалось — можно залить вручную позже"
fi

echo "==> 6/7 systemd-сервис"
cp "$APP_DIR/deploy/beget/rnp-dashboard.service" /etc/systemd/system/rnp-dashboard.service
systemctl daemon-reload
systemctl enable rnp-dashboard

echo "==> 7/7 nginx (по IP или домену)"
SRV="${DOMAIN:-_}"
sed "s/__DOMAIN__/$SRV/g" "$APP_DIR/deploy/beget/rnp-dashboard.nginx.conf" > /etc/nginx/sites-available/rnp-dashboard
ln -sf /etc/nginx/sites-available/rnp-dashboard /etc/nginx/sites-enabled/rnp-dashboard
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

systemctl restart rnp-dashboard
sleep 3
echo ""
echo "================= ГОТОВО ================="
curl -s http://127.0.0.1:8000/api/health || echo "(сервис ещё поднимается, подожди 5с)"
echo ""
echo "Дашборд открывается по адресу сервера: http://159.194.226.33/"
echo "Осталось (для импорта из Google): залить credentials.json в $APP_DIR/credentials.json"
echo "=========================================="
