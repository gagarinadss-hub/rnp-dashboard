# Развёртывание RNP-дашборда на Beget VPS

VPS: **Energetic Marian**, Ubuntu, IP **159.194.226.33**.
Идём по шагам. Команды — копировать в SSH-консоль сервера (можно веб-консоль в панели Бегета).

---

## Шаг 1. Домен → сервер (DNS)
В панели Бегета: **DNS** → у своего домена добавь/измени запись:
```
тип A   |   имя @ (или поддомен, напр. dash)   |   значение 159.194.226.33
```
Подожди 10–60 минут, пока запись «разойдётся».

## Шаг 2. Зайти на сервер
Панель Бегета → **VPS → Energetic Marian** → кнопка консоли (или по SSH):
```
ssh root@159.194.226.33
```
Пароль root — в панели VPS (можно сбросить там же).

## Шаг 3. Установка одной командой
На сервере выполни (подставь свой домен вместо dashboard.example.ru):
```
DOMAIN=dashboard.example.ru bash <(curl -sL https://raw.githubusercontent.com/gagarinadss-hub/rnp-dashboard/main/deploy/beget/setup.sh)
```
Скрипт сам поставит Python, nginx, код, зависимости, systemd-сервис и nginx-конфиг.

## Шаг 4. Секреты и данные
Нужно положить два файла на сервер:

**credentials.json** (Google service account) → `/opt/rnp-dashboard/credentials.json`
**launches.db** (текущая база с Railway) → `/opt/rnp-data/launches.db`

Самый простой способ — прямо на сервере скачать базу с Railway:
```
curl -sL "https://web-production-7fde6.up.railway.app/api/admin/export-db?token=rnp-bot-2026" -o /opt/rnp-data/launches.db
```
credentials.json залей через файловый менеджер Бегета или командой `scp` со своего Mac:
```
scp "/Users/daragagarina/РНП Запуски/credentials.json" root@159.194.226.33:/opt/rnp-dashboard/credentials.json
```

## Шаг 5. Запуск
```
systemctl start rnp-dashboard
systemctl status rnp-dashboard      # должно быть active (running)
curl -s http://127.0.0.1:8000/api/health
```

## Шаг 6. HTTPS (бесплатный сертификат)
```
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d dashboard.example.ru
```
Готово — дашборд доступен по `https://dashboard.example.ru`.

---

## Обновление кода в будущем
```
cd /opt/rnp-dashboard && git pull
/opt/rnp-dashboard/.venv/bin/pip install -r requirements.txt
systemctl restart rnp-dashboard
```

## Полезное
- Логи: `journalctl -u rnp-dashboard -f`
- Перезапуск: `systemctl restart rnp-dashboard`
- БД и .env лежат в `/opt/rnp-data` (переживают обновления кода).
- Авто-импорт работает внутри процесса (как на Railway), отдельный cron не нужен.

## Безопасность
- После переезда **удали** служебный эндпоинт экспорта БД (или смени `WEBHOOK_TOKEN`).
- `credentials.json` — только на сервере, не в git.
