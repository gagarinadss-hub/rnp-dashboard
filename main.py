import time
import os
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import sys
sys.path.insert(0, str(BASE_DIR))

from sheets_client import SheetsClient
from data_processor import DataProcessor

CACHE_TTL = 300  # seconds
_cache: dict = {}
_last_updated: float = 0

DEMO_MODE = False
CACHE_FILE = BASE_DIR / "data_cache.json"

# ── Importer config ─────────────────────────────────────────────────────────
IMPORT_TTL = int(os.getenv("IMPORT_TTL", "600"))   # секунды между авто-импортами (по умолч. 10 мин)
_import_task: asyncio.Task | None = None


def _load_cache_file() -> dict:
    import json
    with open(CACHE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    from datetime import datetime
    data["overview"]["last_updated"] = datetime.now().isoformat()
    data["overview"]["_source"] = "cache"
    return data


def _fetch_data() -> dict:
    if DEMO_MODE:
        from demo_data import get_demo_data
        return get_demo_data()
    client = SheetsClient()
    registrations  = client.get_registrations()
    rnp_raw        = client.get_rnp_raw()
    historical_raw = client.get_historical_raw()
    processor = DataProcessor(registrations, rnp_raw, historical_raw)
    data = processor.get_dashboard_data()
    data["overview"]["_source"] = "live"
    return data


def get_data(force: bool = False) -> dict:
    global _cache, _last_updated
    now = time.time()
    if force or not _cache or (now - _last_updated > CACHE_TTL):
        _cache = _fetch_data()
        _last_updated = now
    return _cache


async def _import_loop():
    """Фоновый цикл: импортирует рег. из Google Таблицы каждые IMPORT_TTL секунд."""
    from sheets_importer import run_import
    from db import get_active_launch_id
    await asyncio.sleep(5)          # небольшая пауза после старта
    while True:
        try:
            launch_id = get_active_launch_id()
            if launch_id:
                result = await asyncio.to_thread(run_import, launch_id)
                log.info(f"[import_loop] {result}")
            else:
                log.info("[import_loop] нет активного запуска, пропускаю")
        except Exception as e:
            log.warning(f"[import_loop] ошибка: {e}")
        await asyncio.sleep(IMPORT_TTL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global DEMO_MODE, _import_task
    from db import init_db
    init_db()
    print("[startup] SQLite DB инициализирована")
    try:
        get_data()
        print("[startup] ✅ Подключился к Google Sheets (live)")
    except Exception as e:
        if CACHE_FILE.exists():
            print(f"[startup] ⚠ Google Sheets недоступен. Читаю data_cache.json")
            global _cache, _last_updated
            _cache = _load_cache_file()
            _last_updated = time.time()
        else:
            print(f"[startup] ⚠ Google Sheets недоступен ({e}). Включаю ДЕМО-режим.")
            DEMO_MODE = True
            get_data()

    # Запускаем фоновый импорт
    _import_task = asyncio.create_task(_import_loop())
    print(f"[startup] 🔄 Авто-импорт регистраций каждые {IMPORT_TTL}с запущен")

    yield

    # Останавливаем при завершении
    if _import_task:
        _import_task.cancel()
        try:
            await _import_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="РНП Запусков", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Live dashboard (Google Sheets) ──────────────────────────────────────────
@app.get("/api/dashboard")
def dashboard():
    try:
        return get_data()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/refresh")
def refresh():
    try:
        return get_data(force=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    from sheets_importer import get_status
    return {"status": "ok", "cached": bool(_cache), "importer": get_status()}


# ── Import endpoints ─────────────────────────────────────────────────────────
@app.post("/api/reimport")
def reimport_active():
    """Ручной запуск импорта для активного запуска."""
    from sheets_importer import run_import
    from db import get_active_launch_id
    launch_id = get_active_launch_id()
    if not launch_id:
        raise HTTPException(400, "Нет активного запуска")
    try:
        result = run_import(launch_id)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/launches/{launch_id}/reimport")
def reimport_launch(launch_id: int):
    """Ручной запуск импорта для конкретного запуска."""
    from sheets_importer import run_import
    try:
        result = run_import(launch_id)
        return result
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/importer/status")
def importer_status():
    from sheets_importer import get_status
    return get_status()


# ── Launches (SQLite) ───────────────────────────────────────────────────────
@app.get("/api/launches")
def list_launches():
    from db import get_all_launches
    return get_all_launches()


@app.get("/api/launches/{launch_id}/dashboard")
def launch_dashboard(launch_id: int):
    """DB-based dashboard for any launch (including active)."""
    from db import get_dashboard_from_db
    data = get_dashboard_from_db(launch_id)
    if not data:
        raise HTTPException(404, "Launch not found")
    return data


@app.get("/api/launches/{launch_id}")
def launch_detail(launch_id: int):
    from db import get_launch_detail
    data = get_launch_detail(launch_id)
    if not data:
        raise HTTPException(404, "Launch not found")
    return data


@app.post("/api/launches")
def create_launch_endpoint(body: dict):
    from db import create_launch
    launch_id = create_launch(
        name          = body["name"],
        reg_start     = body.get("reg_start"),
        reg_end       = body.get("reg_end"),
        event_date    = body.get("event_date"),
        event_end_date= body.get("event_end_date"),
        total_plan    = body.get("total_plan", 0),
        channels      = body.get("channels", []),
    )
    return {"id": launch_id, "status": "created"}


@app.post("/api/launches/{launch_id}/activate")
def activate_launch(launch_id: int):
    from db import set_active_launch
    set_active_launch(launch_id)
    return {"status": "ok"}


@app.get("/api/launches/{launch_id}/comments")
def get_comments(launch_id: int):
    from db import get_comments
    return get_comments(launch_id)


@app.put("/api/launches/{launch_id}/comments")
def upsert_comment_endpoint(launch_id: int, body: dict):
    from db import upsert_comment
    channel_name = body.get("channel_name", "").strip()
    day_num      = body.get("day_num")       # can be None for channel-level comment
    comment      = body.get("comment", "").strip()
    author       = body.get("author", "").strip()
    if not channel_name:
        raise HTTPException(400, "channel_name required")
    return upsert_comment(launch_id, channel_name, day_num, comment, author)


@app.put("/api/launches/{launch_id}/facts")
def update_fact(launch_id: int, body: dict):
    """Manual fact entry: set (overwrite) registrations for a channel/day."""
    from db import set_daily_fact
    channel_name = body.get("channel_name", "").strip()
    day_num      = int(body.get("day_num", 1))
    fact         = int(body.get("fact", 0))
    if not channel_name:
        raise HTTPException(400, "channel_name required")
    if day_num < 1:
        raise HTTPException(400, "day_num must be >= 1")
    set_daily_fact(launch_id, channel_name, day_num, fact)
    return {"status": "ok", "channel": channel_name, "day_num": day_num, "fact": fact}


# ── Label mappings ──────────────────────────────────────────────────────────
@app.get("/api/label-mappings")
def list_label_mappings():
    from db import get_label_mappings
    return get_label_mappings()


@app.put("/api/label-mappings")
def upsert_label_mapping(body: dict):
    from db import save_label_mapping
    src      = body.get("utm_source", "").strip()
    med      = body.get("utm_medium", "").strip()
    platform = body.get("platform", "").strip()
    ch       = body.get("channel_name", "").strip()
    if not ch:
        raise HTTPException(400, "channel_name required")
    save_label_mapping(src, med, platform, ch)
    return {"status": "ok", "utm_source": src, "utm_medium": med, "platform": platform, "channel_name": ch}


@app.delete("/api/label-mappings")
def remove_label_mapping(body: dict):
    from db import delete_label_mapping
    src      = body.get("utm_source", "").strip()
    med      = body.get("utm_medium", "").strip()
    platform = body.get("platform", "").strip()
    delete_label_mapping(src, med, platform)
    return {"status": "ok"}


@app.get("/api/launches/{launch_id}/unmatched")
def get_unmatched(launch_id: int):
    from db import get_unmatched_labels
    return get_unmatched_labels(launch_id)


@app.get("/api/launches/{launch_id}/utm-labels")
def get_utm_labels(launch_id: int):
    from db import get_utm_stats, get_label_mappings
    stats    = get_utm_stats(launch_id)
    mappings = {(m["utm_source"], m["utm_medium"]): m["channel_name"] for m in get_label_mappings()}
    # Annotate with user override if present
    for s in stats:
        override = mappings.get((s["utm_source"], s["utm_medium"]))
        s["user_mapping"] = override or ""
    return stats


# ── Bot webhook ─────────────────────────────────────────────────────────────
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "rnp-bot-2026")


@app.post("/api/webhook")
def webhook(body: dict):
    token = body.get("token") or ""
    if token != WEBHOOK_TOKEN:
        raise HTTPException(403, "Invalid token")
    from db import get_active_launch_id, get_db, upsert_channel, add_daily_registration
    from datetime import date
    launch_id    = body.get("launch_id") or get_active_launch_id()
    if not launch_id:
        raise HTTPException(400, "No active launch")
    channel_name = body.get("label") or body.get("channel") or "без метки"
    reg_date     = body.get("date") or str(date.today())
    count        = int(body.get("count", 1))
    with get_db() as conn:
        row = conn.execute("SELECT reg_start FROM launches WHERE id=?", (launch_id,)).fetchone()
        if row and row["reg_start"]:
            from datetime import datetime
            start   = datetime.fromisoformat(row["reg_start"]).date()
            d       = datetime.fromisoformat(reg_date).date()
            day_num = max(1, (d - start).days + 1)
        else:
            day_num = 1
        ch_id = upsert_channel(conn, channel_name)
        add_daily_registration(conn, launch_id, ch_id, day_num, count)
    return {"status": "ok", "day_num": day_num}


app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
