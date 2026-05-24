# РНП Запусков — Dashboard

## Быстрый старт

### 1. Установи зависимости

```bash
cd "РНП Запуски"
pip install -r requirements.txt
```

### 2. Настрой Google Sheets API (один раз)

**Шаг 1.** Открой [console.cloud.google.com](https://console.cloud.google.com)

**Шаг 2.** Создай новый проект (или выбери существующий) → нажми «Select project»

**Шаг 3.** В поиске введи «Google Sheets API» → включи (Enable)

**Шаг 4.** Перейди в «APIs & Services» → «Credentials» → «Create Credentials» → «Service account»
- Название: `rnp-dashboard`
- Нажми «Create and Continue» → «Done»

**Шаг 5.** Кликни на созданный сервисный аккаунт → вкладка «Keys» → «Add Key» → «JSON»
- Скачается файл `*.json` — переименуй его в `credentials.json`
- Положи его в папку `РНП Запуски/`

**Шаг 6.** Скопируй email сервисного аккаунта (вида `rnp-dashboard@...iam.gserviceaccount.com`)

**Шаг 7.** Открой свою Google таблицу → кнопка «Поделиться» → вставь этот email → роль «Читатель»

### 3. Создай `.env`

Скопируй `.env.example` в `.env`:
```bash
cp .env.example .env
```
В файле `.env` уже прописан ID твоей таблицы — менять не нужно.

### 4. Запусти

```bash
python main.py
```

Открой браузер: **http://localhost:8000**

---

## Деплой на Railway

1. Загрузи проект на GitHub
2. Зайди на [railway.app](https://railway.app) → «New Project» → «Deploy from GitHub repo»
3. В настройках сервиса добавь переменные окружения:
   - `SPREADSHEET_ID` = `1bDbcketkM9SC5FY0rMHvwy8bUaf4Op3hPxFQJHBKH9E`
   - `GOOGLE_CREDENTIALS_JSON` = содержимое `credentials.json` (всё одной строкой)
4. Деплой запустится автоматически

---

## Структура листов Google Sheets

| Лист | Назначение |
|------|-----------|
| `База` | Сырые регистрации из бота |
| `РНП ✓` | Планы и факт по каналам |
| `Для расчёта процентов запуска - дни` | Исторические % по дням |

## Обновление данных

Данные обновляются автоматически каждые **5 минут**.  
Принудительное обновление — кнопка **«Обновить»** в правом верхнем углу.
