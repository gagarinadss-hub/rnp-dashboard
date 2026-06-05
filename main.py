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

# ── Railway / cloud: восстанавливаем credentials.json из env-переменной ──────
_creds_env = os.getenv("GOOGLE_CREDENTIALS")
if _creds_env and not (BASE_DIR / "credentials.json").exists():
    (BASE_DIR / "credentials.json").write_text(_creds_env, encoding="utf-8")
    log.info("[startup] credentials.json восстановлен из GOOGLE_CREDENTIALS")

import sys
sys.path.insert(0, str(BASE_DIR))

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
    from sheets_client import SheetsClient
    from data_processor import DataProcessor
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
    """Фоновый цикл: единый импорт-сервис каждые IMPORT_TTL секунд."""
    from import_service import run_import_service
    from db import get_active_launch_id
    await asyncio.sleep(5)          # небольшая пауза после старта
    while True:
        try:
            launch_id = get_active_launch_id()
            if launch_id:
                result = await asyncio.to_thread(run_import_service, launch_id, "auto")
                log.info(f"[import_loop] {result.get('status')}")
            else:
                log.info("[import_loop] нет активного запуска, пропускаю")
        except Exception as e:
            log.warning(f"[import_loop] ошибка: {e}")
        await asyncio.sleep(IMPORT_TTL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _import_task
    from db import init_db
    init_db()
    print("[startup] SQLite DB инициализирована")
    # Дашборд читает только БД (daily_plans + daily_registrations).
    # Старый live-путь Google Sheets отключён от дашборда; единственная
    # интеграция со Sheets — фоновый импорт факта ниже.

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
def _empty_dashboard() -> dict:
    """Чистый empty-state, когда нет активного запуска. Не 500, форма как у
    реального payload, чтобы фронт не падал."""
    from datetime import datetime
    return {
        "overview": {
            "launch_id": None, "launch_name": "Нет активного запуска",
            "start_date": None, "end_date": None, "event_date": None,
            "total_plan": 0, "total_actual": 0, "completion_pct": 0,
            "days_elapsed": 0, "days_total": 0, "days_remaining": 0,
            "not_started": True, "pace_needed": 0,
            "last_updated": datetime.now().isoformat(), "_source": "empty",
        },
        "daily": {"dates": [], "daily_actual": [], "daily_plan": [],
                  "cumulative_actual": [], "cumulative_plan": []},
        "channels": [], "forecast": {}, "alerts": [],
        "best_channels": [], "lag_channels": [],
    }


def _active_dashboard() -> dict:
    """Единый источник правды дашборда: активный запуск из БД.
    Факт — из raw_registrations (дедуп); план — сохранённый/ручной."""
    from db import get_active_launch_id, get_dashboard_from_db, build_raw_override
    launch_id = get_active_launch_id()
    if not launch_id:
        return _empty_dashboard()
    data = get_dashboard_from_db(launch_id, live_override=build_raw_override(launch_id))
    return data or _empty_dashboard()


@app.get("/api/dashboard")
def dashboard():
    try:
        return _active_dashboard()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/refresh")
def refresh():
    # «Обновить» больше не переключает на старый live-режим: тот же DB-дашборд.
    try:
        return _active_dashboard()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    from sheets_importer import get_status
    return {"status": "ok", "cached": bool(_cache), "importer": get_status()}


# ── Import endpoints ─────────────────────────────────────────────────────────
# Ручной и авто-импорт идут через ОДИН сервис (import_service.run_import_service)
# с защитой от параллельных запусков. Возвращаем агрегатный результат для
# обратной совместимости (UI/smoke ожидают total_registrations).
def _reimport(launch_id: int):
    from import_service import run_import_service
    res = run_import_service(launch_id, "manual")
    if res.get("status") == "already_running":
        return {"status": "already_running", "message": "Импорт уже выполняется"}
    agg = res.get("aggregate") or {}
    agg["_raw_import"] = res.get("raw")
    return agg


@app.post("/api/reimport")
def reimport_active():
    """Ручной запуск импорта для активного запуска."""
    from db import get_active_launch_id
    launch_id = get_active_launch_id()
    if not launch_id:
        raise HTTPException(400, "Нет активного запуска")
    try:
        return _reimport(launch_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/launches/{launch_id}/reimport")
def reimport_launch(launch_id: int):
    """Ручной запуск импорта для конкретного запуска."""
    try:
        return _reimport(launch_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/importer/status")
def importer_status():
    from sheets_importer import get_status
    return get_status()


# ── Raw import (Этап 3) — построчно в raw_registrations ──────────────────────
@app.post("/api/import/google-sheets")
def import_google_sheets(body: dict | None = None):
    """Единый импорт из Google Sheets (raw + агрегат) через общий сервис с локом."""
    from import_service import run_import_service
    from db import get_active_launch_id
    body = body or {}
    launch_id = body.get("launch_id") or get_active_launch_id()
    try:
        return run_import_service(launch_id, source=body.get("source", "manual"))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/import-runs")
def import_runs(limit: int = 20):
    from db import get_import_runs
    return get_import_runs(limit)


# ── Unknown UTM (Этап 4) ─────────────────────────────────────────────────────
@app.post("/api/launches/{launch_id}/reresolve-raw")
def reresolve_raw(launch_id: int):
    """Переразобрать каналы у уже импортированных raw_registrations
    (после расширения правил резолва)."""
    from raw_import import reresolve_raw_channels
    try:
        return reresolve_raw_channels(launch_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/launches/{launch_id}/raw-fact")
def raw_fact(launch_id: int):
    """Сверка (Задача 5.1): факт из raw_registrations с дедупом
    (User ID + телефон). Для сравнения со старым дашбордом до переключения."""
    from db import aggregate_fact_from_raw
    return aggregate_fact_from_raw(launch_id)


@app.get("/api/launches/{launch_id}/unknown-utm")
def unknown_utm(launch_id: int):
    """Неизвестные UTM запуска (raw_registrations с channel_id IS NULL),
    частые -> свежие."""
    from db import get_unknown_utm
    return get_unknown_utm(launch_id)


@app.post("/api/utm-mappings")
def post_utm_mapping(body: dict):
    """Назначить UTM каналу: создать/обновить правило + перераспределить
    уже импортированные raw_registrations с этой меткой."""
    from db import assign_utm_to_channel
    cid = body.get("channel_id", body.get("channelId"))
    cname = body.get("channel_name") or body.get("channelName")
    if cid is None and not cname:
        raise HTTPException(400, "channel_id или channel_name обязателен")
    res = assign_utm_to_channel(
        body.get("utm_source", body.get("utmSource")),
        body.get("utm_medium", body.get("utmMedium")),
        body.get("platform"),
        channel_id=cid, channel_name=cname,
    )
    return {"status": "ok", **res}


# ── Launches (SQLite) ───────────────────────────────────────────────────────
@app.get("/api/launches")
def list_launches():
    from db import get_all_launches
    return get_all_launches()


@app.get("/api/channels/{channel_name}/history")
def channel_history(channel_name: str):
    """Drill-down: как канал отрабатывал во всех запусках."""
    from db import get_channel_history
    data = get_channel_history(channel_name)
    if not data:
        raise HTTPException(404, "Channel not found")
    return data


def _ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """Пересекаются ли два диапазона ISO-дат. Любой битый вход → False."""
    from datetime import date
    try:
        as_, ae = date.fromisoformat(a_start), date.fromisoformat(a_end)
        bs, be = date.fromisoformat(b_start), date.fromisoformat(b_end)
    except Exception:
        return False
    return as_ <= be and bs <= ae


def _live_override_for(launch_id: int) -> dict | None:
    """Для активного запуска тянет доверенные числа каналов из живого
    Справочника. None — если запуск не активен, демо-режим, Sheets недоступен
    ИЛИ живой лист относится к другому событию (окна дат не пересекаются) —
    тогда дашборд считается по БД, как раньше."""
    if DEMO_MODE:
        return None
    from db import get_active_launch_id, get_launch_detail
    if get_active_launch_id() != launch_id:
        return None
    try:
        data = get_data()
        ov = data.get("overview", {})
        if ov.get("_source") not in ("live", "cache"):
            return None

        # ── ЗАЩИТА: живой Справочник должен относиться к ЭТОМУ запуску ──────
        # Сверяем окно дат живого листа с окном активного запуска. Если они
        # не пересекаются — лист ещё на другом (прошлом) событии, и брать из
        # него числа нельзя (иначе на активный запуск попадут чужие данные).
        det = get_launch_detail(launch_id) or {}
        ovd = det.get("overview", det)
        l_start = ovd.get("reg_start") or ovd.get("start_date")
        l_end   = ovd.get("reg_end")   or ovd.get("end_date")
        if not (l_start and l_end and ov.get("start_date") and ov.get("end_date")
                and _ranges_overlap(l_start, l_end, ov["start_date"], ov["end_date"])):
            log.warning(
                f"[live_override] окно живого листа {ov.get('start_date')}..{ov.get('end_date')} "
                f"не совпадает с запуском {l_start}..{l_end} — беру данные из БД")
            return None

        channels = data.get("channels", [])
        daily    = data.get("daily", {})
        ch_actuals = {c["name"]: c["actual"] for c in channels if c.get("name")}
        daily_map  = dict(zip(daily.get("dates", []), daily.get("daily_actual", [])))
        if not ch_actuals:
            return None
        return {"channel_actuals": ch_actuals, "daily_actuals": daily_map}
    except Exception as e:
        log.warning(f"[live_override] не удалось получить живые данные: {e}")
        return None


@app.get("/api/launches/{launch_id}/dashboard")
def launch_dashboard(launch_id: int):
    """DB-based dashboard. Факт берётся из raw_registrations (дедуп, override),
    план — сохранённый/ручной из БД. Если raw-строк нет — fallback на агрегат."""
    from db import get_dashboard_from_db, build_raw_override
    data = get_dashboard_from_db(launch_id, live_override=build_raw_override(launch_id))
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


@app.put("/api/launches/{launch_id}")
def update_launch_endpoint(launch_id: int, body: dict):
    """Обновить метаданные запуска (даты регистрации/мероприятия, название, план).
    Принимает любое подмножество: name, reg_start, reg_end, event_date,
    event_end_date, total_plan."""
    from db import update_launch, upsert_launch_channels
    allowed = {"name", "reg_start", "reg_end", "event_date", "event_end_date", "total_plan"}
    fields = {k: v for k, v in body.items() if k in allowed}
    channels = body.get("channels")
    if not fields and not channels:
        raise HTTPException(400, "No updatable fields provided")
    result = {"id": launch_id, "updated": []}
    if fields:
        result = update_launch(launch_id, **fields)
        if result is None:
            raise HTTPException(404, "Launch not found")
    if channels:
        ch_res = upsert_launch_channels(launch_id, channels)
        if ch_res is None:
            raise HTTPException(404, "Launch not found")
        result["channels"] = ch_res["channels"]
    return {"status": "ok", **result}


@app.post("/api/launches/{launch_id}/snapshot-live")
def snapshot_live(launch_id: int):
    """Перезаписать факты запуска точными числами из живого Справочника.
    Берём только если окно живого листа пересекается с окном запуска —
    иначе можно занести чужое событие."""
    from db import get_launch_detail, snapshot_live_channels
    if DEMO_MODE:
        raise HTTPException(400, "Демо-режим: живой Справочник недоступен")
    det = get_launch_detail(launch_id)
    if not det:
        raise HTTPException(404, "Launch not found")
    try:
        data = get_data()
    except Exception as e:
        raise HTTPException(502, f"Живой Справочник недоступен: {e}")
    ov = data.get("overview", {})
    if ov.get("_source") not in ("live", "cache"):
        raise HTTPException(502, "Живой Справочник недоступен (демо/нет данных)")
    o = det["overview"]
    if not _ranges_overlap(o.get("reg_start"), o.get("reg_end"),
                           ov.get("start_date"), ov.get("end_date")):
        raise HTTPException(
            409,
            f"Окно живого листа {ov.get('start_date')}..{ov.get('end_date')} не "
            f"совпадает с запуском {o.get('reg_start')}..{o.get('reg_end')} — отказ")
    channels = data.get("channels", [])
    result = snapshot_live_channels(launch_id, channels)
    return {"status": "ok", "source_window": f"{ov.get('start_date')}..{ov.get('end_date')}", **result}


@app.delete("/api/launches/{launch_id}")
def delete_launch_endpoint(launch_id: int):
    """Удалить запуск со всеми связанными данными."""
    from db import delete_launch, get_active_launch_id
    if get_active_launch_id() == launch_id:
        raise HTTPException(400, "Нельзя удалить активный запуск")
    result = delete_launch(launch_id)
    if result is None:
        raise HTTPException(404, "Launch not found")
    return {"status": "ok", **result}


@app.post("/api/launches/{launch_id}/activate")
def activate_launch(launch_id: int):
    from db import set_active_launch
    set_active_launch(launch_id)
    return {"status": "ok"}


@app.post("/api/launches/{launch_id}/regenerate-plan")
def regenerate_plan_endpoint(launch_id: int, body: dict | None = None):
    """Сгенерировать дневной план из истории и сохранить новую active-версию."""
    from db import regenerate_plan
    body = body or {}
    res = regenerate_plan(
        launch_id,
        mode=body.get("mode", "window_index"),
        history_limit=int(body.get("history_limit", 10)),
    )
    if res is None:
        raise HTTPException(404, "Launch not found")
    return {"status": "ok", **res}


@app.get("/api/launches/{launch_id}/daily-plan")
def get_daily_plan_endpoint(launch_id: int):
    """Активная версия сохранённого дневного плана + method_snapshot
    (на какой истории построен план)."""
    from db import get_active_daily_plan
    return get_active_daily_plan(launch_id)


@app.put("/api/launches/{launch_id}/plan-curve")
def set_plan_curve(launch_id: int, body: dict):
    """Shape the plan-by-day curve for a launch.

    Body options (priority: manual > reference > history):
      • {"manual": [700, 700, 100]} — explicit per-day weights (normalised);
        pass null/[] to clear the manual curve.
      • {"ref_launch_id": 12}       — copy the daily shape of another launch.
      • {"ref_launch_id": null}     — fall back to the 5-launch history curve.
    """
    from db import set_plan_curve_ref, set_plan_curve_manual
    if "manual" in body:
        set_plan_curve_manual(launch_id, body.get("manual"))
        return {"status": "ok", "launch_id": launch_id, "manual": body.get("manual")}
    ref = body.get("ref_launch_id")
    set_plan_curve_ref(launch_id, ref if ref else None)
    return {"status": "ok", "launch_id": launch_id, "plan_curve_ref": ref}


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


@app.get("/api/launches/{launch_id}/channels/{channel_name}/tasks")
def list_channel_tasks(launch_id: int, channel_name: str):
    from db import get_channel_tasks
    return get_channel_tasks(launch_id, channel_name.strip())


@app.post("/api/launches/{launch_id}/channels/{channel_name}/tasks")
def create_channel_task(launch_id: int, channel_name: str, body: dict):
    from db import add_channel_task
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    author = (body.get("author") or "").strip()
    return add_channel_task(launch_id, channel_name.strip(), text, author)


@app.patch("/api/tasks/{task_id}")
def patch_channel_task(task_id: int, body: dict):
    from db import update_channel_task
    text = body.get("text")
    if text is not None:
        text = str(text).strip()
    done = body.get("done")
    result = update_channel_task(task_id, text=text, done=done)
    if result is None:
        raise HTTPException(404, "Task not found")
    return result


@app.delete("/api/tasks/{task_id}")
def remove_channel_task(task_id: int):
    from db import delete_channel_task
    if not delete_channel_task(task_id):
        raise HTTPException(404, "Task not found")
    return {"status": "deleted", "id": task_id}


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


@app.get("/api/launches/{launch_id}/compare/{ref_launch_id}")
def compare_launches(launch_id: int, ref_launch_id: int):
    """Day-by-day comparison between two launches."""
    from db import get_comparison_data
    data = get_comparison_data(launch_id, ref_launch_id)
    if not data:
        raise HTTPException(404, "One or both launches not found")
    return data


@app.get("/api/launches/{launch_id}/pace")
def launch_pace(launch_id: int):
    """Темп запуска vs среднеисторический (доля плана по дням)."""
    from db import get_pace_benchmark
    data = get_pace_benchmark(launch_id)
    if not data:
        raise HTTPException(404, "No benchmark data available")
    return data


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


@app.post("/api/admin/historical-import")
def admin_historical_import(body: dict):
    """Прогоняет исторический импорт дашбордов прямо на сервере.
    Тело: {token, sheets:[spreadsheet_id, ...]}. Активный запуск не трогается."""
    token = body.get("token") or ""
    if token != WEBHOOK_TOKEN:
        raise HTTPException(403, "Invalid token")
    sheets = body.get("sheets") or []
    if not isinstance(sheets, list) or not sheets:
        raise HTTPException(400, "Provide non-empty 'sheets' list")
    from historical_importer import import_spreadsheet
    from db import get_active_launch_id
    results = []
    for sid in sheets:
        try:
            results.append(import_spreadsheet(sid))
        except Exception as e:
            results.append({"spreadsheet": str(sid)[:14], "error": f"{type(e).__name__}: {e}"})
    return {"results": results, "active_launch": get_active_launch_id()}


app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
