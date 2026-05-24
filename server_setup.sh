#!/bin/bash
# server_setup.sh — ОДИН РАЗ на чистом Ubuntu 22.04
# Запускать от root: bash server_setup.sh
set -e

APP_DIR="/opt/rnp"
REPO_URL="https://github.com/YOUR_USERNAME/rnp-dashboard.git"   # ← заменить

echo "==> Обновляю систему..."
apt-get update -q && apt-get upgrade -yq

echo "==> Устанавливаю пакеты..."
apt-get install -yq python3.11 python3.11-venv python3-pip nginx git

echo "==> Создаю пользователя rnp..."
useradd -r -s /bin/false rnp 2>/dev/null || true

echo "==> Клонирую репозиторий..."
git clone "$REPO_URL" "$APP_DIR" 2>/dev/null || (cd "$APP_DIR" && git pull)

echo "==> Создаю виртуальное окружение..."
python3.11 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Настраиваю .env (заполни вручную!)..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "   ⚠ Заполни /opt/rnp/.env перед запуском"
fi

echo "==> Загрузи credentials.json вручную:"
echo "   scp credentials.json root@SERVER:/opt/rnp/credentials.json"

echo "==> Устанавливаю systemd сервис..."
cp "$APP_DIR/rnp.service" /etc/systemd/system/rnp.service
systemctl daemon-reload
systemctl enable rnp

echo "==> Настраиваю nginx..."
cp "$APP_DIR/nginx.conf" /etc/nginx/sites-available/rnp
ln -sf /etc/nginx/sites-available/rnp /etc/nginx/sites-enabled/rnp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "==> Права на директорию..."
chown -R rnp:rnp "$APP_DIR"
chmod 600 "$APP_DIR/credentials.json" 2>/dev/null || true

echo ""
echo "✅ Сервер настроен!"
echo ""
echo "Следующие шаги:"
echo "  1. Загрузи credentials.json:  scp credentials.json root@IP:/opt/rnp/"
echo "  2. Заполни .env:              nano /opt/rnp/.env"
echo "  3. Запусти сервис:            systemctl start rnp"
echo "  4. Проверь:                   systemctl status rnp"
echo "  5. SSL (опционально):         certbot --nginx -d YOUR_DOMAIN"
