"""
historical_importer.py
Импортирует исторические запуски из их Google-таблиц-дашбордов.

Для каждой таблицы:
  • «Справочник»   → маппинг (utm_source, utm_medium) → канал
  • «РНП ✅»        → планы, ответственные, итоговый план, название
  • «База»          → сырые регистрации (дата + utm) → канал × дата
  • «Реферальная»   → (опц.) рефералки по датам → «Рефка»

Регистрации раскладываются по РЕАЛЬНЫМ датам (полный диапазон),
day_num = (дата - reg_start).days + 1.
"""
import re
import sqlite3
import gspread
from collections import defaultdict, Counter
from datetime import datetime, date, timedelta

from sheets_client import _build_creds
from db import DB_PATH

# ── UTM → канал резолвер (переиспользуем логику живого импортёра) ─────────────
from sheets_importer import _resolve, _EXTRA  # noqa: F401


# ── Справочник ───────────────────────────────────────────────────────────────
def _build_mapping(ss) -> dict:
    """(src.lower(), med.lower()) -> channel из листа «Справочник» таблицы."""
    try:
        ws = ss.worksheet("Справочник")
    except Exception:
        return {}
    rows = ws.get_all_values()
    mapping = {}
    for r in rows[2:]:
        channel = r[0].strip() if len(r) > 0 else ""
        src     = r[3].strip().lower() if len(r) > 3 else ""
        med     = r[4].strip().lower() if len(r) > 4 else ""
        if channel and src:
            key = (src, med)
            if key not in mapping:
                mapping[key] = channel
    return mapping


# ── РНП-лист: планы / факт / ответственные ──────────────────────────────────
def _find_rnp_ws(ss):
    """Выбирает основной лист РНП (с ✅), пропуская старые/копии/backup."""
    cand = None
    for ws in ss.worksheets():
        t = ws.title
        tl = t.lower()
        if not tl.strip().startswith("рнп"):
            continue
        if any(x in tl for x in ("стар", "копи", "backup", "old")):
            continue
        if "✅" in t:
            return ws
        cand = cand or ws
    return cand


def _num(val) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("%", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _parse_rnp(ss) -> dict:
    """Возвращает {channel_name: {plan, responsible}} из РНП-листа.
    Колонки определяются динамически по строке-заголовку «Канал … План … Факт … Отв»."""
    ws = _find_rnp_ws(ss)
    if not ws:
        return {}
    vals = ws.get_all_values()

    # Найти строку-заголовок
    hdr_idx = None
    col = {}
    for i, row in enumerate(vals[:30]):
        cells = [str(c).strip().lower() for c in row]
        if "канал" in cells:
            # map columns
            for ci, c in enumerate(cells):
                if c == "канал" and "name" not in col:
                    col["name"] = ci
                elif c.startswith("план") and "plan" not in col:
                    col["plan"] = ci
                elif c == "факт" and "fact" not in col:
                    col["fact"] = ci
                elif c in ("отв", "ответственный") and "resp" not in col:
                    col["resp"] = ci
            if "name" in col and "plan" in col:
                hdr_idx = i
                break

    if hdr_idx is None:
        return {}

    name_c = col["name"]
    plan_c = col.get("plan")
    resp_c = col.get("resp")

    channels = {}
    for row in vals[hdr_idx + 1:]:
        if name_c >= len(row):
            continue
        name = str(row[name_c]).strip()
        if not name or name.lower() in ("итого", "общие", "общий", "всего"):
            continue
        # Stop at clearly-empty tail (no name)
        plan = _num(row[plan_c]) if plan_c is not None and plan_c < len(row) else 0
        resp = str(row[resp_c]).strip() if resp_c is not None and resp_c < len(row) else ""
        key = name.lower()
        if key not in channels:
            channels[name] = {"plan": int(plan), "responsible": resp}
    return channels


# ── База: регистрации по каналу × дате ───────────────────────────────────────
def _parse_base(ss, mapping: dict):
    """Возвращает (channel_date_counts, date_counter, totals).
    channel_date_counts: {channel: {date: count}}
    """
    try:
        ws = ss.worksheet("База")
    except Exception:
        return {}, Counter(), 0

    rows = ws.get_all_values()
    if not rows:
        return {}, Counter(), 0

    # Определяем колонки по заголовку
    hdr = [str(c).strip().lower() for c in rows[0]]
    def col_of(*names, default=None):
        for n in names:
            if n in hdr:
                return hdr.index(n)
        return default

    c_id   = col_of("sb_id", "tg id", "телефон", default=0)
    c_date = col_of("дата входа", "дата", default=1)
    c_trig = col_of("trigger", default=7)
    c_src  = col_of("utm_source", default=8)
    c_med  = col_of("utm_medium", default=9)
    c_plat = col_of("платформа", "platform", default=None)

    ch_date = defaultdict(lambda: defaultdict(int))
    date_cnt = Counter()
    total = 0
    seen = set()        # дедуп по SB_ID — команда считает УНИКАЛЬНЫЕ регистрации
    dropped_dups = 0

    for r in rows[1:]:
        if not any(str(c).strip() for c in r):
            continue
        # Дедуп: один человек (один SB_ID) = одна регистрация.
        # База отсортирована по дате → первое вхождение = самая ранняя дата.
        uid = r[c_id].strip() if c_id is not None and c_id < len(r) else ""
        if uid:
            if uid in seen:
                dropped_dups += 1
                continue
            seen.add(uid)
        date_str = r[c_date].strip() if c_date < len(r) else ""
        d = _parse_date(date_str)
        if not d:
            continue
        src  = r[c_src].strip()  if c_src  is not None and c_src  < len(r) else ""
        med  = r[c_med].strip()  if c_med  is not None and c_med  < len(r) else ""
        trig = r[c_trig].strip() if c_trig is not None and c_trig < len(r) else ""
        plat = r[c_plat].strip() if c_plat is not None and c_plat < len(r) else ""
        ch = _resolve(src, med, trig, plat, mapping, {})
        ch_date[ch][d] += 1
        date_cnt[d] += 1
        total += 1

    return ch_date, date_cnt, total


def _parse_referral(ss):
    """Опционально читает «Реферальная»: дата → +1 к Рефке.
    Возвращает {date: count} или пустой dict."""
    try:
        ws = ss.worksheet("Реферальная")
    except Exception:
        return {}
    rows = ws.get_all_values()
    if not rows:
        return {}
    # Найти колонку, где значения парсятся как даты
    sample = rows[1:50]
    best_col, best_hits = None, 0
    for ci in range(min(8, max(len(r) for r in sample) if sample else 0)):
        hits = sum(1 for r in sample if ci < len(r) and _parse_date(r[ci].strip()))
        if hits > best_hits:
            best_hits, best_col = hits, ci
    if best_col is None or best_hits < 3:
        return {}
    out = Counter()
    for r in rows[1:]:
        if best_col < len(r):
            d = _parse_date(r[best_col].strip())
            if d:
                out[d] += 1
    return dict(out)


def _parse_date(s: str):
    s = (s or "").strip()[:10]
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            if 2024 <= d.year <= 2028:
                return d
            return None
        except ValueError:
            continue
    return None


# ── Окно дат: полный диапазон с лёгкой защитой от одиночных выбросов ─────────
def _window(date_cnt: Counter):
    if not date_cnt:
        return None, None
    peak = max(date_cnt, key=lambda d: date_cnt[d])
    # Полный диапазон, но отбрасываем даты дальше 30 дней от пика (явные выбросы)
    valid = [d for d in date_cnt if abs((d - peak).days) <= 30]
    if not valid:
        valid = list(date_cnt)
    return min(valid), max(valid)


# ── Названия / даты мероприятия из заголовка ─────────────────────────────────
def _event_dates_from_title(title: str, year_hint: int):
    """Пытается извлечь даты мероприятия вида '24-26.10' / '12-14 МАЯ' и т.п."""
    m = re.search(r"(\d{1,2})\s*[-–]\s*(\d{1,2})[.\s]*(\d{1,2})", title)
    if m:
        d1, d2, mon = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(year_hint, mon, d1), date(year_hint, mon, d2)
        except ValueError:
            pass
    return None, None


# ── Запись в БД (без активации) ──────────────────────────────────────────────
def _upsert_launch(con, name, reg_start, reg_end, event_date, event_end, total_plan):
    """Создаёт или обновляет запуск по имени, НЕ трогая is_active."""
    row = con.execute("SELECT id FROM launches WHERE name=?", (name,)).fetchone()
    if row:
        lid = row[0]
        con.execute(
            """UPDATE launches SET reg_start=?, reg_end=?, event_date=?,
               event_end_date=?, total_plan=? WHERE id=?""",
            (reg_start, reg_end, event_date, event_end, total_plan, lid),
        )
        # Чистим старые данные этого запуска
        con.execute("DELETE FROM daily_registrations WHERE launch_id=?", (lid,))
        con.execute("DELETE FROM launch_channels WHERE launch_id=?", (lid,))
    else:
        con.execute(
            """INSERT INTO launches(name, reg_start, reg_end, event_date,
               event_end_date, total_plan, is_active)
               VALUES(?,?,?,?,?,?,0)""",
            (name, reg_start, reg_end, event_date, event_end, total_plan),
        )
        lid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    return lid


def _channel_id(con, name):
    con.execute("INSERT OR IGNORE INTO channels(name) VALUES(?)", (name,))
    return con.execute("SELECT id FROM channels WHERE name=?", (name,)).fetchone()[0]


# ── Главная функция ──────────────────────────────────────────────────────────
def import_spreadsheet(spreadsheet_id: str) -> dict:
    gc = gspread.authorize(_build_creds())
    ss = gc.open_by_key(spreadsheet_id)
    title = ss.title.strip()

    mapping  = _build_mapping(ss)
    rnp      = _parse_rnp(ss)
    ch_date, date_cnt, base_total = _parse_base(ss, mapping)

    # ВАЖНО: «Реферальную» НЕ приплюсовываем — рефералы уже сидят в «Базе»
    # (под своими utm-метками). Отдельный лист был бы двойным счётом:
    # дедуп «Базы» по SB_ID уже даёт официальное число РНП «ОБЩИЕ».

    reg_start, reg_end = _window(date_cnt)
    if not reg_start:
        return {"spreadsheet": title, "error": "нет валидных дат в Базе"}

    total_days = (reg_end - reg_start).days + 1
    year_hint = reg_start.year
    ev_start, ev_end = _event_dates_from_title(title, year_hint)

    # Объединяем каналы из РНП и из Базы
    base_channels = set(ch_date.keys())
    rnp_names = {n: rnp[n] for n in rnp}
    # сопоставление по имени (case-insensitive) — РНП имя имеет приоритет для отображения
    rnp_lower = {n.lower(): n for n in rnp_names}

    all_channels = {}  # display_name -> {plan, responsible}
    for n, info in rnp_names.items():
        all_channels[n] = {"plan": info["plan"], "responsible": info["responsible"]}
    for bn in base_channels:
        if bn.lower() in rnp_lower:
            continue  # уже учтён под именем из РНП
        all_channels.setdefault(bn, {"plan": 0, "responsible": ""})

    total_plan = sum(c["plan"] for c in all_channels.values())

    # Перекладываем факт Базы под отображаемые имена РНП
    fact_by_display = defaultdict(lambda: defaultdict(int))  # name -> {day_num: count}
    for bn, days in ch_date.items():
        disp = rnp_lower.get(bn.lower(), bn)
        for d, cnt in days.items():
            day_num = (d - reg_start).days + 1
            if day_num < 1:
                continue
            fact_by_display[disp][day_num] += cnt

    # ── Пишем в БД ───────────────────────────────────────────────────────────
    con = sqlite3.connect(DB_PATH)
    try:
        lid = _upsert_launch(
            con, title,
            str(reg_start), str(reg_end),
            str(ev_start) if ev_start else None,
            str(ev_end) if ev_end else None,
            total_plan,
        )
        # каналы
        for name, info in all_channels.items():
            cid = _channel_id(con, name)
            con.execute(
                """INSERT OR REPLACE INTO launch_channels(launch_id, channel_id, plan, responsible)
                   VALUES(?,?,?,?)""",
                (lid, cid, info["plan"], info["responsible"]),
            )
        # факт по дням
        records = 0
        for name, days in fact_by_display.items():
            cid = _channel_id(con, name)
            for day_num, cnt in days.items():
                con.execute(
                    """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                       VALUES(?,?,?,?)
                       ON CONFLICT(launch_id, channel_id, day_num)
                       DO UPDATE SET count=excluded.count""",
                    (lid, cid, day_num, cnt),
                )
                records += 1
        con.commit()
    finally:
        con.close()

    total_fact = sum(sum(d.values()) for d in fact_by_display.values())
    return {
        "spreadsheet":  title,
        "launch_id":    lid,
        "reg_start":    str(reg_start),
        "reg_end":      str(reg_end),
        "total_days":   total_days,
        "channels":     len(all_channels),
        "total_plan":   total_plan,
        "total_fact":   total_fact,
        "db_records":   records,
    }


if __name__ == "__main__":
    import sys
    print(import_spreadsheet(sys.argv[1]))
