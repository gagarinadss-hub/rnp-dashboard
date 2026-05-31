import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

# DATA_DIR: на Railway монтируем volume в /data, локально — рядом с кодом
import os as _os
_data_dir = Path(_os.getenv("DATA_DIR", str(Path(__file__).parent)))
_data_dir.mkdir(parents=True, exist_ok=True)
DB_PATH = _data_dir / "launches.db"


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS launches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                reg_start TEXT,
                reg_end TEXT,
                event_date TEXT,
                event_end_date TEXT,
                total_plan INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS launch_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                launch_id INTEGER REFERENCES launches(id),
                channel_id INTEGER REFERENCES channels(id),
                plan INTEGER DEFAULT 0,
                responsible TEXT DEFAULT '',
                UNIQUE(launch_id, channel_id)
            );
            CREATE TABLE IF NOT EXISTS daily_registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                launch_id INTEGER REFERENCES launches(id),
                channel_id INTEGER,
                day_num INTEGER NOT NULL,
                count INTEGER DEFAULT 0,
                UNIQUE(launch_id, channel_id, day_num)
            );
        """)
        # Migrations for columns added after initial schema
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                launch_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                day_num INTEGER,
                comment TEXT NOT NULL DEFAULT '',
                author TEXT DEFAULT '',
                updated_at TEXT,
                UNIQUE(launch_id, channel_id, day_num)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                launch_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0,
                author TEXT DEFAULT '',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        for sql in [
            "ALTER TABLE launches ADD COLUMN event_end_date TEXT",
            "ALTER TABLE launches ADD COLUMN plan_curve_ref INTEGER",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass  # Column already exists

        # utm_label_stats — always recreated on import, so we can migrate freely
        _migrate_utm_tables(conn)


def _migrate_utm_tables(conn):
    """Create/migrate utm_label_stats, unmatched_labels, label_mappings with platform support."""
    # utm_label_stats and unmatched_labels: rebuilt on every import — drop & recreate freely
    conn.executescript("""
        DROP TABLE IF EXISTS utm_label_stats;
        CREATE TABLE utm_label_stats (
            launch_id        INTEGER NOT NULL,
            utm_source       TEXT NOT NULL DEFAULT '',
            utm_medium       TEXT NOT NULL DEFAULT '',
            platform         TEXT NOT NULL DEFAULT '',
            count            INTEGER NOT NULL DEFAULT 0,
            resolved_channel TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (launch_id, utm_source, utm_medium, platform)
        );
        DROP TABLE IF EXISTS unmatched_labels;
        CREATE TABLE unmatched_labels (
            launch_id  INTEGER NOT NULL,
            utm_source TEXT NOT NULL DEFAULT '',
            utm_medium TEXT NOT NULL DEFAULT '',
            platform   TEXT NOT NULL DEFAULT '',
            count      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (launch_id, utm_source, utm_medium, platform)
        );
    """)

    # label_mappings: has user data — migrate carefully
    cols = [row[1] for row in conn.execute("PRAGMA table_info(label_mappings)").fetchall()]
    if not cols:
        # Fresh create
        conn.execute("""
            CREATE TABLE label_mappings (
                utm_source   TEXT NOT NULL DEFAULT '',
                utm_medium   TEXT NOT NULL DEFAULT '',
                platform     TEXT NOT NULL DEFAULT '',
                channel_name TEXT NOT NULL,
                PRIMARY KEY (utm_source, utm_medium, platform)
            )
        """)
    elif 'platform' not in cols:
        # Migrate: copy old data, add platform='' (means "any platform")
        conn.executescript("""
            ALTER TABLE label_mappings RENAME TO label_mappings_old;
            CREATE TABLE label_mappings (
                utm_source   TEXT NOT NULL DEFAULT '',
                utm_medium   TEXT NOT NULL DEFAULT '',
                platform     TEXT NOT NULL DEFAULT '',
                channel_name TEXT NOT NULL,
                PRIMARY KEY (utm_source, utm_medium, platform)
            );
            INSERT OR IGNORE INTO label_mappings(utm_source, utm_medium, platform, channel_name)
            SELECT utm_source, utm_medium, '', channel_name FROM label_mappings_old;
            DROP TABLE label_mappings_old;
        """)


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_channel(conn, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO channels(name) VALUES(?)", (name,))
    row = conn.execute("SELECT id FROM channels WHERE name=?", (name,)).fetchone()
    return row["id"]


def add_daily_registration(conn, launch_id: int, channel_id, day_num: int, count: int):
    """Accumulate registrations (used by bot webhook)."""
    conn.execute(
        """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
           VALUES(?,?,?,?)
           ON CONFLICT(launch_id, channel_id, day_num)
           DO UPDATE SET count = count + excluded.count""",
        (launch_id, channel_id, day_num, count),
    )


def get_comments(launch_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT cc.id, cc.channel_id, c.name AS channel_name,
                      cc.day_num, cc.comment, cc.author, cc.updated_at
               FROM channel_comments cc
               JOIN channels c ON c.id = cc.channel_id
               WHERE cc.launch_id=? ORDER BY cc.channel_id, cc.day_num""",
            (launch_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_comment(launch_id: int, channel_name: str, day_num, comment: str, author: str = "") -> dict:
    from datetime import datetime
    with get_db() as conn:
        ch_id = upsert_channel(conn, channel_name)
        conn.execute(
            """INSERT INTO channel_comments(launch_id, channel_id, day_num, comment, author, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(launch_id, channel_id, day_num)
               DO UPDATE SET comment=excluded.comment, author=excluded.author, updated_at=excluded.updated_at""",
            (launch_id, ch_id, day_num, comment, author, datetime.now().isoformat())
        )
        row = conn.execute(
            "SELECT id FROM channel_comments WHERE launch_id=? AND channel_id=? AND day_num=?",
            (launch_id, ch_id, day_num)
        ).fetchone()
        return {"id": row["id"], "channel": channel_name, "day_num": day_num, "comment": comment}


# ── Channel tasks (список задач/комментариев на канал в рамках запуска) ──────
def get_channel_tasks(launch_id: int, channel_name: str) -> list:
    with get_db() as conn:
        ch = conn.execute("SELECT id FROM channels WHERE name=?", (channel_name,)).fetchone()
        if not ch:
            return []
        rows = conn.execute(
            """SELECT id, text, done, author, created_at, updated_at
               FROM channel_tasks WHERE launch_id=? AND channel_id=?
               ORDER BY done ASC, created_at ASC""",
            (launch_id, ch["id"])
        ).fetchall()
        return [dict(r) for r in rows]


def add_channel_task(launch_id: int, channel_name: str, text: str, author: str = "") -> dict:
    from datetime import datetime
    now = datetime.now().isoformat()
    with get_db() as conn:
        ch_id = upsert_channel(conn, channel_name)
        cur = conn.execute(
            """INSERT INTO channel_tasks(launch_id, channel_id, text, done, author, created_at, updated_at)
               VALUES(?,?,?,0,?,?,?)""",
            (launch_id, ch_id, text, author, now, now)
        )
        return {"id": cur.lastrowid, "text": text, "done": 0, "author": author,
                "created_at": now, "updated_at": now}


def update_channel_task(task_id: int, text=None, done=None) -> dict | None:
    from datetime import datetime
    with get_db() as conn:
        row = conn.execute("SELECT id FROM channel_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        sets, params = [], []
        if text is not None:
            sets.append("text=?"); params.append(text)
        if done is not None:
            sets.append("done=?"); params.append(1 if done else 0)
        sets.append("updated_at=?"); params.append(datetime.now().isoformat())
        params.append(task_id)
        conn.execute(f"UPDATE channel_tasks SET {', '.join(sets)} WHERE id=?", params)
        r = conn.execute(
            "SELECT id, text, done, author, created_at, updated_at FROM channel_tasks WHERE id=?",
            (task_id,)
        ).fetchone()
        return dict(r)


def delete_channel_task(task_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM channel_tasks WHERE id=?", (task_id,))
        return cur.rowcount > 0


def set_daily_fact(launch_id: int, channel_name: str, day_num: int, fact: int):
    """Set (overwrite) a manual fact for a channel/day. Used by manual entry UI."""
    with get_db() as conn:
        ch_id = upsert_channel(conn, channel_name)
        conn.execute(
            """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
               VALUES(?,?,?,?)
               ON CONFLICT(launch_id, channel_id, day_num)
               DO UPDATE SET count = excluded.count""",
            (launch_id, ch_id, day_num, fact),
        )
        return ch_id


def snapshot_live_channels(launch_id: int, live_channels: list) -> dict:
    """Перезаписать факты запуска точными числами из живого Справочника.

    Удаляет все daily_registrations запуска и записывает дневную разбивку
    каждого канала из live (по позиции дня). План/комментарии/задачи не
    трогаются. Каналы, которых нет в launch_channels, привязываются с планом 0.
    live_channels: list of {"name", "actual", "daily_actual": [...]}.
    """
    written, channels_touched = 0, 0
    with get_db() as conn:
        conn.execute("DELETE FROM daily_registrations WHERE launch_id=?", (launch_id,))
        for ch in live_channels:
            name = (ch.get("name") or "").strip()
            if not name:
                continue
            ch_id = upsert_channel(conn, name)
            conn.execute(
                "INSERT OR IGNORE INTO launch_channels(launch_id, channel_id, plan) VALUES(?,?,0)",
                (launch_id, ch_id)
            )
            channels_touched += 1
            daily = ch.get("daily_actual") or []
            # Авторитетный итог канала — ch["actual"] из Справочника. Дневную
            # форму берём из daily_actual, но масштабируем к точному итогу
            # (DataProcessor округляет дни вниз и теряет ~8% — здесь чиним).
            target = int(round(ch.get("actual", 0) or 0))
            dsum = sum(daily)
            if daily and dsum > 0 and target > 0:
                scaled = [int(round(x * target / dsum)) for x in daily]
                drift = target - sum(scaled)
                if scaled:
                    mi = max(range(len(scaled)), key=lambda k: scaled[k])
                    scaled[mi] += drift
                for i, c in enumerate(scaled):
                    if c:
                        conn.execute(
                            """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                               VALUES(?,?,?,?)
                               ON CONFLICT(launch_id, channel_id, day_num)
                               DO UPDATE SET count=excluded.count""",
                            (launch_id, ch_id, i + 1, int(c))
                        )
                        written += 1
            elif target:
                conn.execute(
                    """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                       VALUES(?,?,1,?)
                       ON CONFLICT(launch_id, channel_id, day_num)
                       DO UPDATE SET count=excluded.count""",
                    (launch_id, ch_id, target)
                )
                written += 1
    return {"launch_id": launch_id, "channels": channels_touched, "rows_written": written}


def delete_launch(launch_id: int) -> dict | None:
    """Полностью удалить запуск и все связанные данные. None — если не найден."""
    with get_db() as conn:
        row = conn.execute("SELECT id, name FROM launches WHERE id=?", (launch_id,)).fetchone()
        if not row:
            return None
        name = row["name"]
        for tbl in ("daily_registrations", "launch_channels", "channel_comments",
                    "channel_tasks", "utm_label_stats", "unmatched_labels"):
            conn.execute(f"DELETE FROM {tbl} WHERE launch_id=?", (launch_id,))
        conn.execute("DELETE FROM launches WHERE id=?", (launch_id,))
        return {"deleted_id": launch_id, "name": name}


def get_active_launch_id():
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM launches WHERE is_active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None


def set_active_launch(launch_id: int):
    with get_db() as conn:
        conn.execute("UPDATE launches SET is_active=0")
        conn.execute("UPDATE launches SET is_active=1 WHERE id=?", (launch_id,))


def set_plan_curve_ref(launch_id: int, ref_launch_id):
    """Set the reference launch whose daily fact shape defines the plan curve.
    Pass ref_launch_id=None to reset to even distribution."""
    with get_db() as conn:
        conn.execute(
            "UPDATE launches SET plan_curve_ref=? WHERE id=?",
            (ref_launch_id, launch_id)
        )


def _cumulative(values):
    result, running = [], 0
    for v in values:
        running += v
        result.append(running)
    return result


# Глобальная историческая кривая долей регистраций по дням запуска
# (лист «Для расчёта процентов запуска - дни»). Та же, что DEFAULT_DAY_PCTS
# в data_processor.py — единая методология прогноза для живого и БД-путей.
GLOBAL_DAY_PCTS = [0.066, 0.167, 0.197, 0.190, 0.212, 0.167, 0.001]


def _forecast_fractions(total_days: int) -> list[float]:
    """Доли ожидаемых регистраций по дням, нормированные к сумме 1.0,
    подогнанные под длину запуска total_days."""
    base = GLOBAL_DAY_PCTS
    if total_days <= 0:
        return []
    if total_days <= len(base):
        fr = base[:total_days]
    else:
        fr = base + [base[-1]] * (total_days - len(base))
    s = sum(fr)
    return [x / s for x in fr] if s > 0 else [1.0 / total_days] * total_days


def _plan_curve_fractions(conn, ref_launch_id, total_days: int) -> list[float] | None:
    """Return per-day fractions (summing to 1.0) shaped like the daily fact
    distribution of a reference launch. None if reference has no usable data."""
    if not ref_launch_id or total_days <= 0:
        return None
    rows = conn.execute(
        """SELECT day_num, SUM(count) AS total
           FROM daily_registrations WHERE launch_id=?
           GROUP BY day_num ORDER BY day_num""",
        (ref_launch_id,)
    ).fetchall()
    if not rows:
        return None
    ref_max = max(r["day_num"] for r in rows)
    ref_daily = [0] * ref_max
    for r in rows:
        ref_daily[r["day_num"] - 1] = r["total"] or 0
    if sum(ref_daily) <= 0:
        return None

    # Fit reference shape to total_days
    if len(ref_daily) >= total_days:
        shape = ref_daily[:total_days]
    else:
        avg = sum(ref_daily) / len(ref_daily)
        shape = ref_daily + [avg] * (total_days - len(ref_daily))

    s = sum(shape)
    if s <= 0:
        return None
    return [x / s for x in shape]


def _history_day_fractions(conn, total_days: int, exclude_launch_id, n: int = 5) -> list[float] | None:
    """Доли регистраций по дням, усреднённые по последним n запускам с реальными
    дневными данными. Логика плана по дням (по просьбе): берём первые total_days
    дней каждого из последних n запусков, для каждого считаем его ДОЛЮ по дням
    (нормируем к 1, чтобы крупный запуск не доминировал), усредняем доли по
    запускам. Так план текущего запуска по дням повторяет типичную динамику
    прошлых запусков. None — если истории нет.

    Источник дневных итогов: строки без канала (импортный агрегат) — это реальный
    факт по дням; если их нет, суммируем по каналам.
    """
    if total_days <= 0:
        return None
    rows = conn.execute(
        "SELECT id FROM launches WHERE id != ? ORDER BY date(reg_start) DESC, id DESC",
        (exclude_launch_id,)
    ).fetchall()

    agg  = [0.0] * total_days
    used = 0
    for r in rows:
        lid = r["id"]
        drows = conn.execute(
            "SELECT day_num, SUM(count) AS t FROM daily_registrations "
            "WHERE launch_id=? AND channel_id IS NULL GROUP BY day_num", (lid,)
        ).fetchall()
        if not drows:
            drows = conn.execute(
                "SELECT day_num, SUM(count) AS t FROM daily_registrations "
                "WHERE launch_id=? GROUP BY day_num", (lid,)
            ).fetchall()
        if not drows:
            continue
        dmax = max(x["day_num"] for x in drows)
        # сравнимы только запуски, у которых есть данные минимум на total_days дней
        if dmax < total_days:
            continue
        daily = [0] * dmax
        for x in drows:
            daily[x["day_num"] - 1] = x["t"] or 0
        first = daily[:total_days]
        s = sum(first)
        if s <= 0:
            continue
        for i in range(total_days):
            agg[i] += first[i] / s
        used += 1
        if used >= n:
            break

    total = sum(agg)
    if used == 0 or total <= 0:
        return None
    return [x / total for x in agg]


def _distribute_plan(ch_plan: int, total_days: int, fractions: list[float] | None) -> list[int]:
    """Distribute a channel plan across days. By a reference curve if provided,
    otherwise evenly. Always sums exactly to ch_plan."""
    if ch_plan <= 0 or total_days <= 0:
        return [0] * max(total_days, 0)
    if fractions and len(fractions) == total_days:
        daily = [int(round(ch_plan * f)) for f in fractions]
    else:
        base = ch_plan // total_days
        daily = [base] * total_days
    # Fix rounding drift onto the peak day
    drift = ch_plan - sum(daily)
    if drift != 0:
        peak = max(range(total_days), key=lambda i: daily[i]) if any(daily) else total_days - 1
        daily[peak] += drift
    return daily


def compute_alerts(overview: dict, channels: list, forecast: dict) -> list[dict]:
    """Self-reading insights. Returns list of {severity, icon, title, text, channel}.
    severity: 'red' | 'yellow' | 'green'."""
    alerts = []
    total_plan     = overview.get("total_plan", 0) or 0
    days_elapsed   = overview.get("days_elapsed", 0) or 0
    days_remaining = overview.get("days_remaining", 0) or 0
    yesterday      = overview.get("yesterday_actual", 0) or 0
    yest_delta     = overview.get("yesterday_delta", 0) or 0
    pace_needed    = overview.get("pace_needed", 0) or 0

    # 1. Forecast vs plan (realistic scenario)
    real = forecast.get("realistic", 0) or 0
    if total_plan > 0:
        gap_pct = round((real - total_plan) / total_plan * 100)
        if real >= total_plan:
            alerts.append({
                "severity": "green", "icon": "🎯",
                "title": "Прогноз выше плана",
                "text": f"При текущем темпе финал ≈ {real:,} ({gap_pct:+d}% к плану).".replace(",", " "),
                "channel": "",
            })
        elif real >= total_plan * 0.9:
            alerts.append({
                "severity": "yellow", "icon": "⚠️",
                "title": "Прогноз чуть ниже плана",
                "text": f"Финал ≈ {real:,} ({gap_pct:+d}% к плану). Нужно поднажать.".replace(",", " "),
                "channel": "",
            })
        else:
            alerts.append({
                "severity": "red", "icon": "🚨",
                "title": "Прогноз не дотягивает до плана",
                "text": f"Финал ≈ {real:,} ({gap_pct:+d}% к плану). Риск недобора.".replace(",", " "),
                "channel": "",
            })

    # 2. Pace needed vs yesterday's pace
    if days_remaining > 0 and pace_needed > 0 and yesterday > 0:
        if pace_needed > yesterday * 1.3:
            alerts.append({
                "severity": "red", "icon": "📈",
                "title": "Нужно ускоряться",
                "text": f"Чтобы дойти до плана нужно ~{pace_needed:,}/день, вчера было {yesterday:,}.".replace(",", " "),
                "channel": "",
            })

    # 3. Yesterday drop
    if yest_delta < 0 and yesterday > 0:
        prev = yesterday - yest_delta
        if prev > 0 and abs(yest_delta) >= prev * 0.2:
            drop_pct = round(yest_delta / prev * 100)
            alerts.append({
                "severity": "yellow", "icon": "📉",
                "title": "Вчера просели",
                "text": f"Вчера {yesterday:,} рег. ({drop_pct}% ко дню до).".replace(",", " "),
                "channel": "",
            })

    # 4. Per-channel pace problems
    for c in channels:
        if c.get("plan", 0) <= 50:
            continue
        ratio = c.get("pace_ratio", 0)
        if c.get("actual", 0) == 0 and days_elapsed >= 1:
            alerts.append({
                "severity": "red", "icon": "🔴",
                "title": f"Канал «{c['name']}» молчит",
                "text": "Ни одной регистрации при плане " + f"{c['plan']:,}.".replace(",", " "),
                "channel": c["name"],
            })
        elif 0 < ratio < 0.6:
            alerts.append({
                "severity": "red", "icon": "🔴",
                "title": f"Канал «{c['name']}» тонет",
                "text": f"Темп {ratio:.0%} от нужного ({c.get('actual',0):,}/{c['plan']:,}).".replace(",", " "),
                "channel": c["name"],
            })

    # 5. Top performer (only if we have a clear leader)
    leaders = sorted(
        [c for c in channels if c.get("plan", 0) > 50 and c.get("pace_ratio", 0) >= 1.2],
        key=lambda x: x.get("pace_ratio", 0), reverse=True
    )
    if leaders:
        c = leaders[0]
        alerts.append({
            "severity": "green", "icon": "🚀",
            "title": f"Канал «{c['name']}» в лидерах",
            "text": f"Темп {c['pace_ratio']:.0%} от плана — перевыполняет.",
            "channel": c["name"],
        })

    # Order: red → yellow → green
    order = {"red": 0, "yellow": 1, "green": 2}
    alerts.sort(key=lambda a: order.get(a["severity"], 3))
    return alerts


def get_dashboard_from_db(launch_id: int, live_override: dict | None = None) -> dict:
    """Compute a full dashboard payload from SQLite data.

    Если передан ``live_override`` (для активного запуска), фактические числа
    каналов и дневная кривая берутся из живого Справочника (тем, которым
    доверяет команда), а план/ответственные/комментарии/задачи/кривая плана
    остаются из БД. Сопоставление каналов — по имени.

    live_override = {
        "channel_actuals": {channel_name: actual_int},
        "daily_actuals":   {date_str: count_int},
    }
    """
    live_ch_actuals  = (live_override or {}).get("channel_actuals") or {}
    live_daily_map   = (live_override or {}).get("daily_actuals") or {}
    with get_db() as conn:
        l = conn.execute(
            "SELECT id, name, reg_start, reg_end, event_date, event_end_date, total_plan, is_active, plan_curve_ref FROM launches WHERE id=?",
            (launch_id,)
        ).fetchone()
        if not l:
            return None

        today = date.today()
        reg_start = date.fromisoformat(l["reg_start"]) if l["reg_start"] else today - timedelta(days=6)
        reg_end   = date.fromisoformat(l["reg_end"])   if l["reg_end"]   else today

        # Окно учёта регистраций: от reg_start до ПОСЛЕДНЕГО дня мероприятия
        # (event_end_date, иначе event_date), с откатом на reg_end и потолком
        # MAX_REG_SPAN. То же правило, что и в бенчмарке темпа.
        span = reg_window_span(l)
        total_days     = span if span else max(1, (reg_end - reg_start).days + 1)
        window_end     = reg_start + timedelta(days=total_days - 1)
        # Сколько дней запуска прошло. До старта — 0 (запуск ещё не начался),
        # после конца — все total_days. Оставшиеся дни считаем В ПРЕДЕЛАХ окна
        # запуска, а не «календарно до конца», иначе для будущего запуска
        # pace_needed = план / (дни до конца) расходится с дневным планом графика.
        raw_elapsed    = (today - reg_start).days + 1
        days_elapsed   = max(0, min(raw_elapsed, total_days))
        days_remaining = max(0, total_days - days_elapsed)

        day_dates = [str(reg_start + timedelta(days=i)) for i in range(total_days)]

        # Channels
        ch_rows = conn.execute(
            """SELECT lc.plan, lc.responsible, c.name AS ch_name, c.id AS ch_id
               FROM launch_channels lc
               JOIN channels c ON c.id = lc.channel_id
               WHERE lc.launch_id=?
               ORDER BY lc.plan DESC""",
            (launch_id,)
        ).fetchall()

        # Сверка плана: если сумма планов по каналам ≠ план запуска (total_plan),
        # пропорционально раскидываем разницу по каналам, чтобы сумма по каналам
        # всегда совпадала с планом запуска. Хвост округления — на крупнейший
        # канал. Исходные данные в БД не меняем, правка только в расчёте.
        raw_plans      = [(ch["ch_id"], ch["plan"] or 0) for ch in ch_rows]
        raw_plan_sum   = sum(p for _, p in raw_plans)
        launch_total   = l["total_plan"] or 0
        if launch_total > 0 and raw_plan_sum > 0 and launch_total != raw_plan_sum:
            scaled_plan = {cid: int(round(p * launch_total / raw_plan_sum)) for cid, p in raw_plans}
            drift = launch_total - sum(scaled_plan.values())
            if drift != 0 and scaled_plan:
                big = max(scaled_plan, key=lambda k: scaled_plan[k])
                scaled_plan[big] += drift
        else:
            scaled_plan = {cid: p for cid, p in raw_plans}

        # yesterday / day-before indices (0-based)
        yesterday_idx   = days_elapsed - 2   # day before today
        day_before_idx  = days_elapsed - 3   # two days ago

        # Кривая плана по дням:
        #  • если выбрана явная «База плана» (plan_curve_ref) — форма того запуска;
        #  • иначе по умолчанию — усреднённая динамика последних 5 запусков
        #    (план каждого канала и общий план раскидываются по этой кривой,
        #     поэтому сумма по каналам = план на день).
        if l["plan_curve_ref"]:
            plan_fractions = _plan_curve_fractions(conn, l["plan_curve_ref"], total_days)
        else:
            plan_fractions = _history_day_fractions(conn, total_days, launch_id, n=5)
        plan_curve_used = plan_fractions is not None

        channels = []
        for ch in ch_rows:
            dregs = conn.execute(
                "SELECT day_num, count FROM daily_registrations WHERE launch_id=? AND channel_id=? ORDER BY day_num",
                (launch_id, ch["ch_id"])
            ).fetchall()
            day_map = {r["day_num"]: r["count"] for r in dregs}

            daily_actual = [day_map.get(i + 1, 0) for i in range(total_days)]
            total_actual_ch = sum(daily_actual)
            ch_plan = scaled_plan.get(ch["ch_id"], ch["plan"] or 0)

            # ── Живой Справочник: подменяем факт канала на доверенное число ──
            if ch["ch_name"] in live_ch_actuals:
                live_a = int(live_ch_actuals[ch["ch_name"]])
                db_sum = sum(daily_actual)
                if db_sum > 0:
                    # сохраняем форму кривой канала из БД, масштабируем к live
                    daily_actual = [round(x * live_a / db_sum) for x in daily_actual]
                    drift = live_a - sum(daily_actual)
                    if daily_actual:
                        daily_actual[-1] += drift
                else:
                    # нет дневной разбивки в БД — раскладываем по кривой плана
                    daily_actual = _distribute_plan(live_a, total_days, plan_fractions)
                total_actual_ch = live_a

            # Distribute plan across days (by reference curve, else evenly)
            daily_plan = _distribute_plan(ch_plan, total_days, plan_fractions)

            pct = round(total_actual_ch / ch_plan * 100, 1) if ch_plan > 0 else 0

            # Yesterday / delta per channel
            ch_yesterday  = daily_actual[yesterday_idx]  if 0 <= yesterday_idx  < total_days else 0
            ch_day_before = daily_actual[day_before_idx] if 0 <= day_before_idx < total_days else 0
            ch_delta      = ch_yesterday - ch_day_before

            # Pace: actual per elapsed day vs plan per day
            actual_pace = total_actual_ch / days_elapsed if days_elapsed > 0 else 0
            target_pace = ch_plan / total_days if total_days > 0 and ch_plan > 0 else 0
            pace_ratio  = round(actual_pace / target_pace, 2) if target_pace > 0 else 0

            # Per-channel forecast: current pace projected to end
            ch_forecast = int(actual_pace * total_days) if actual_pace > 0 else total_actual_ch

            channels.append({
                "channel_id":    ch["ch_id"],
                "name":          ch["ch_name"],
                "plan":          ch_plan,
                "actual":        total_actual_ch,
                "pct":           pct,
                "responsible":   ch["responsible"] or "",
                "daily_plan":    daily_plan,
                "daily_actual":  daily_actual,
                "yesterday":     ch_yesterday,
                "yesterday_delta": ch_delta,
                "pace_ratio":    pace_ratio,
                "forecast":      ch_forecast,
            })

        total_plan   = l["total_plan"] or sum(c["plan"] for c in channels)
        total_actual = sum(c["actual"] for c in channels)

        # Also check null-channel daily totals (historical data)
        null_totals = conn.execute(
            "SELECT day_num, count FROM daily_registrations WHERE launch_id=? AND channel_id IS NULL ORDER BY day_num",
            (launch_id,)
        ).fetchall()
        null_map = {r["day_num"]: r["count"] for r in null_totals}

        # Daily totals: prefer null-channel rows (historical), else sum channels
        daily_total_actual = []
        for i in range(total_days):
            day_num = i + 1
            if day_num in null_map:
                daily_total_actual.append(null_map[day_num])
            else:
                daily_total_actual.append(sum(c["daily_actual"][i] for c in channels))

        if null_map:
            total_actual = sum(daily_total_actual)

        # ── Живой Справочник: итог и дневная кривая из доверенного источника ──
        if live_ch_actuals:
            # Доверенный итог = сумма факта каналов Справочника (как на /api/dashboard,
            # те самые 100.8%, которым верит команда).
            total_actual = sum(c["actual"] for c in channels)
            if live_daily_map:
                # Форму дневной кривой берём из живой дневной карты, но масштабируем
                # к доверенному итогу — чтобы конец кумулятивной кривой = заголовку.
                curve = [live_daily_map.get(day_dates[i], 0) for i in range(total_days)]
                curve_sum = sum(curve)
                if curve_sum > 0 and total_actual > 0:
                    scaled = [round(x * total_actual / curve_sum) for x in curve]
                    drift = total_actual - sum(scaled)
                    for j in range(len(scaled) - 1, -1, -1):
                        if scaled[j] > 0:
                            scaled[j] += drift
                            break
                    daily_total_actual = scaled
                else:
                    daily_total_actual = curve

        completion_pct = round(total_actual / total_plan * 100, 1) if total_plan > 0 else 0
        daily_plan_list = [sum(c["daily_plan"][i] for c in channels) for i in range(total_days)]

        # Today stats
        today_idx    = (today - reg_start).days
        today_actual = daily_total_actual[today_idx] if 0 <= today_idx < total_days else 0
        today_plan   = daily_plan_list[today_idx]    if 0 <= today_idx < len(daily_plan_list) else 0
        today_pct    = round(today_actual / today_plan * 100, 1) if today_plan > 0 else 0

        # Yesterday totals (for whole launch)
        yesterday_actual   = daily_total_actual[yesterday_idx]  if 0 <= yesterday_idx  < total_days else 0
        day_before_actual  = daily_total_actual[day_before_idx] if 0 <= day_before_idx < total_days else 0
        yesterday_delta    = yesterday_actual - day_before_actual

        # Pace needed to hit plan
        total_remaining = max(0, total_plan - total_actual)
        pace_needed     = round(total_remaining / days_remaining) if days_remaining > 0 else 0

        # ── Прогноз по методологии: проекция через накопленную долю кривой ──
        # Регистрации распределены не ровно, а по исторической кривой долей дня
        # (та же DEFAULT_DAY_PCTS, что в живом пути DataProcessor). Проекция =
        # накопл.факт / накопл.доля к текущему дню. «Вялые» дни (<15% ожидания)
        # отбрасываем, чтобы мягкий старт не занижал прогноз.
        fcurve = _forecast_fractions(total_days)

        good_idxs = []
        for i in range(days_elapsed):
            expected = total_plan * fcurve[i] if i < len(fcurve) else 0
            if expected > 0 and daily_total_actual[i] >= expected * 0.15:
                good_idxs.append(i)

        if good_idxs:
            cum_actual = sum(daily_total_actual[i] for i in good_idxs)
            cum_frac   = sum(fcurve[i] for i in good_idxs)
            projected  = int(cum_actual / cum_frac) if cum_frac > 0 else total_actual
        else:
            cum_frac  = sum(fcurve[:days_elapsed])
            projected = int(total_actual / cum_frac) if cum_frac > 0 else total_actual

        # Сценарии: реалистичный — накопл.метод. Опт/пес — диапазон неопределённости
        # ТОЛЬКО по оставшимся дням: накопленный факт уже зафиксирован, поэтому
        # band = факт + лучший/худший дневной темп × доля оставшихся дней.
        # Темп берём по «весомым» дням (доля >= 5%), иначе деление на крошечную
        # долю (последний день ~0.001 / добитые дни) даёт абсурдные значения.
        # Для завершённого запуска оставшихся дней нет → опт = пес = реал = факт.
        proj_realistic = projected
        remaining_frac = sum(fcurve[i] for i in range(days_elapsed, total_days))
        observed       = sum(daily_total_actual[:days_elapsed])
        rates = [
            daily_total_actual[i] / fcurve[i]
            for i in good_idxs
            if fcurve[i] >= 0.05
        ]
        if rates and remaining_frac > 0:
            proj_optimistic  = int(observed + max(rates) * remaining_frac)
            proj_pessimistic = int(observed + min(rates) * remaining_frac)
            # удерживаем в разумной полосе вокруг реалистичного
            proj_optimistic  = min(proj_optimistic,  int(projected * 1.5))
            proj_pessimistic = max(proj_pessimistic, int(projected * 0.5))
            proj_optimistic  = max(proj_optimistic,  projected)
            proj_pessimistic = min(proj_pessimistic, projected)
        else:
            proj_optimistic  = projected
            proj_pessimistic = projected

        projected_total = proj_realistic
        projected_pct   = round(projected_total / total_plan * 100, 1) if total_plan > 0 else 0

        # Кумулятивная линия прогноза: факт за прошедшие дни + проекция по кривой
        daily_forecast = [int(projected * f) for f in fcurve]
        forecast_cum   = _cumulative(daily_total_actual[:days_elapsed])
        for i in range(days_elapsed, total_days):
            inc = daily_forecast[i] if i < len(daily_forecast) else 0
            forecast_cum.append((forecast_cum[-1] if forecast_cum else 0) + inc)

        n_good = len(good_idxs)
        confidence = "низкая" if n_good <= 1 else ("средняя" if n_good <= 2 else "высокая")

        # Best / lag channels (sorted by pace_ratio)
        active_chs    = [c for c in channels if c["plan"] > 0 and c["actual"] > 0]
        best_channels = sorted(active_chs, key=lambda x: x["pct"], reverse=True)[:3]
        lag_channels  = sorted(
            [c for c in channels if c["plan"] > 50 and c["actual"] < c["plan"]],
            key=lambda x: x["pct"]
        )[:3]

        overview = {
            "launch_id":        launch_id,
            "launch_name":      l["name"],
            "start_date":       str(reg_start),
            "end_date":         str(window_end),
            "event_date":       l["event_date"],
            "event_end_date":   l["event_end_date"],
            "total_plan":       total_plan,
            "total_actual":     total_actual,
            "completion_pct":   completion_pct,
            "days_elapsed":     days_elapsed,
            "days_total":       total_days,
            "days_remaining":   days_remaining,
            "not_started":      today < reg_start,
            "today_actual":     today_actual,
            "today_plan":       today_plan,
            "today_pct":        today_pct,
            "yesterday_actual": yesterday_actual,
            "yesterday_delta":  yesterday_delta,
            "pace_needed":      pace_needed,
            "plan_curve_used":  plan_curve_used,
            "plan_curve_ref":   l["plan_curve_ref"],
            "last_updated":     datetime.now().isoformat(),
            "_source":          "db",
        }

        forecast = {
            "projected_total":    projected_total,
            "projected_pct":      projected_pct,
            "confidence":         confidence,
            "pessimistic":        proj_pessimistic,
            "realistic":          proj_realistic,
            "optimistic":         proj_optimistic,
            "pessimistic_pct":    round(proj_pessimistic / total_plan * 100, 1) if total_plan > 0 else 0,
            "optimistic_pct":     round(proj_optimistic  / total_plan * 100, 1) if total_plan > 0 else 0,
            "cumulative_forecast": forecast_cum,
            "cumulative_plan":    _cumulative(daily_plan_list),
        }

        return {
            "overview": overview,
            "daily": {
                "dates":             day_dates,
                "daily_actual":      daily_total_actual,
                "daily_plan":        daily_plan_list,
                "cumulative_actual": _cumulative(daily_total_actual),
                "cumulative_plan":   _cumulative(daily_plan_list),
            },
            "channels": channels,
            "forecast": forecast,
            "alerts":   compute_alerts(overview, channels, forecast),
            "best_channels": [{"name": c["name"], "pct": c["pct"], "actual": c["actual"]} for c in best_channels],
            "lag_channels":  [{"name": c["name"], "pct": c["pct"], "plan": c["plan"], "actual": c["actual"]} for c in lag_channels],
        }


def get_comparison_data(launch_id: int, ref_launch_id: int) -> dict:
    """Compare two launches aligned by day number."""
    with get_db() as conn:
        def _info(lid):
            return conn.execute(
                "SELECT id, name, total_plan FROM launches WHERE id=?", (lid,)
            ).fetchone()

        def _daily(lid):
            rows = conn.execute(
                """SELECT day_num, SUM(count) as total
                   FROM daily_registrations WHERE launch_id=?
                   GROUP BY day_num ORDER BY day_num""", (lid,)
            ).fetchall()
            return {r["day_num"]: r["total"] for r in rows}

        main_info = _info(launch_id)
        ref_info  = _info(ref_launch_id)
        if not main_info or not ref_info:
            return None

        main_daily = _daily(launch_id)
        ref_daily  = _daily(ref_launch_id)

        max_day = max(
            max(main_daily.keys(), default=0),
            max(ref_daily.keys(),  default=0)
        )
        days = list(range(1, max_day + 1))

        main_data = [main_daily.get(d, 0) for d in days]
        ref_data  = [ref_daily.get(d,  0) for d in days]

        return {
            "launch":    {"id": launch_id,     "name": main_info["name"], "plan": main_info["total_plan"]},
            "reference": {"id": ref_launch_id, "name": ref_info["name"],  "plan": ref_info["total_plan"]},
            "days": days,
            "main_daily":      main_data,
            "ref_daily":       ref_data,
            "main_cumulative": _cumulative(main_data),
            "ref_cumulative":  _cumulative(ref_data),
        }


def _pdate(s):
    """Безопасный разбор ISO-даты. None при пустом/битом значении."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


# Потолок длины окна регистрации (дни). Защищает от битых дат мероприятия,
# из-за которых ось бенчмарка растягивалась на 100+ дней.
MAX_REG_SPAN = 45


def reg_window_span(row):
    """Длина окна учёта регистраций в днях.

    Регистрации считаются от reg_start до ПОСЛЕДНЕГО дня мероприятия
    (event_end_date, иначе event_date). Пример: рег. стартовали 22.05,
    мероприятие 27–28.05 → последний день учёта 28.05 → окно 7 дней.

    Даты мероприятия в части запусков битые (месяцы мимо, конец раньше
    начала), поэтому: берём только валидные даты (>= reg_start), при их
    отсутствии откатываемся на reg_end, и в любом случае ограничиваем
    окно сверху MAX_REG_SPAN. Возвращает span (int) или None, если нет
    даже reg_start.
    """
    rs = _pdate(row["reg_start"])
    if not rs:
        return None
    # последний день мероприятия среди валидных дат
    ends = [d for d in (_pdate(row["event_end_date"]), _pdate(row["event_date"]))
            if d and d >= rs]
    end = max(ends) if ends else None
    if end:
        span = (end - rs).days + 1
        if 1 <= span <= MAX_REG_SPAN:
            return span
    # запасной вариант — заявленный конец окна регистрации
    re_ = _pdate(row["reg_end"])
    if re_ and re_ >= rs:
        span = (re_ - rs).days + 1
        if 1 <= span <= MAX_REG_SPAN:
            return span
    return MAX_REG_SPAN  # жёсткий потолок, если все даты непригодны


def get_pace_benchmark(launch_id: int) -> dict:
    """Темп текущего запуска vs среднеисторический.
    На каждый день N: какую долю плана (%) набрал запуск к этому дню.
    Бенчмарк = среднее по всем завершённым запускам с планом и данными.
    Плюс огибающая лучший/худший. Вердикт на текущий день."""
    with get_db() as conn:
        target = conn.execute(
            "SELECT id, name, total_plan, is_active, reg_start, reg_end, event_date, event_end_date "
            "FROM launches WHERE id=?", (launch_id,)
        ).fetchone()
        if not target or not target["total_plan"]:
            return None

        def _cum_pct_curve(lid, plan, span):
            """Накопленный % плана по дням. Регистрации за пределами окна
            (day_num > span) — единичные поздние «хвосты», их отбрасываем,
            иначе ось дней растягивается на 100+ дней из-за пары запоздавших."""
            rows = conn.execute(
                """SELECT day_num, SUM(count) AS total
                   FROM daily_registrations WHERE launch_id=?
                   GROUP BY day_num ORDER BY day_num""", (lid,)
            ).fetchall()
            if not rows or plan <= 0:
                return []
            daily = {r["day_num"]: r["total"] for r in rows}
            data_max = max(r["day_num"] for r in rows)
            # верхняя граница = окно регистрации (если известно), иначе все данные
            max_day = min(data_max, span) if span else data_max
            running = 0
            curve = []
            for d in range(1, max_day + 1):
                running += daily.get(d, 0)
                curve.append(round(running / plan * 100, 1))
            return curve

        target_span = reg_window_span(target)
        target_curve = _cum_pct_curve(launch_id, target["total_plan"], target_span)

        others = conn.execute(
            "SELECT id, total_plan, reg_start, reg_end, event_date, event_end_date "
            "FROM launches WHERE id != ? AND total_plan > 0",
            (launch_id,)
        ).fetchall()
        ref_curves = []
        for o in others:
            c = _cum_pct_curve(o["id"], o["total_plan"], reg_window_span(o))
            if len(c) >= 3:                       # только осмысленные кривые
                ref_curves.append(c)

        if not ref_curves:
            return None

        # обрезаем «хвосты»: день идёт в бенчмарк, только если данные есть
        # хотя бы у половины запусков (иначе средняя кривая искажается единичными
        # запусками с поздними регистрациями). Но не короче текущего дня запуска.
        max_len = max(len(c) for c in ref_curves)
        min_cov = max(2, len(ref_curves) // 2)
        bench_days = 0
        for d in range(max_len):
            cov = sum(1 for c in ref_curves if d < len(c))
            if cov >= min_cov:
                bench_days = d + 1
        bench_days = max(bench_days, len(target_curve))

        avg_curve, best_curve, worst_curve = [], [], []
        for d in range(bench_days):
            vals = [c[d] for c in ref_curves if d < len(c)]
            if not vals:
                avg_curve.append(avg_curve[-1] if avg_curve else 0)
                best_curve.append(best_curve[-1] if best_curve else 0)
                worst_curve.append(worst_curve[-1] if worst_curve else 0)
                continue
            avg_curve.append(round(sum(vals) / len(vals), 1))
            best_curve.append(max(vals))
            worst_curve.append(min(vals))

        # вердикт на текущий день запуска
        verdict = None
        cur_day = len(target_curve)
        if cur_day > 0 and cur_day <= len(avg_curve):
            t = target_curve[-1]
            b = avg_curve[cur_day - 1]
            delta = round(t - b, 1)
            verdict = {
                "day":          cur_day,
                "target_pct":   t,
                "bench_pct":    b,
                "delta":        delta,
                "status":       "ahead" if delta > 2 else ("behind" if delta < -2 else "ontrack"),
            }

        return {
            "launch":       {"id": launch_id, "name": target["name"], "plan": target["total_plan"], "is_active": target["is_active"]},
            "days":         list(range(1, bench_days + 1)),
            "target_curve": target_curve,
            "avg_curve":    avg_curve,
            "best_curve":   best_curve,
            "worst_curve":  worst_curve,
            "ref_count":    len(ref_curves),
            "verdict":      verdict,
        }


def get_all_launches() -> list:
    with get_db() as conn:
        launches = conn.execute(
            "SELECT id, name, reg_start, reg_end, event_date, total_plan, is_active FROM launches ORDER BY id DESC"
        ).fetchall()
        result = []
        for l in launches:
            lid = l["id"]
            null_sum = conn.execute(
                "SELECT COALESCE(SUM(count),0) AS s FROM daily_registrations WHERE launch_id=? AND channel_id IS NULL",
                (lid,)
            ).fetchone()["s"]
            if null_sum > 0:
                total_actual = null_sum
            else:
                total_actual = conn.execute(
                    "SELECT COALESCE(SUM(count),0) AS s FROM daily_registrations WHERE launch_id=?",
                    (lid,)
                ).fetchone()["s"]
            completion_pct = round(total_actual / l["total_plan"] * 100, 1) if l["total_plan"] > 0 else 0
            result.append({
                "id":             lid,
                "name":           l["name"],
                "reg_start":      l["reg_start"],
                "reg_end":        l["reg_end"],
                "event_date":     l["event_date"],
                "total_plan":     l["total_plan"],
                "total_actual":   total_actual,
                "completion_pct": completion_pct,
                "is_active":      l["is_active"],
            })
        return result


def get_launch_detail(launch_id: int) -> dict:
    with get_db() as conn:
        l = conn.execute(
            "SELECT id, name, reg_start, reg_end, event_date, event_end_date, total_plan, is_active FROM launches WHERE id=?",
            (launch_id,)
        ).fetchone()
        if not l:
            return None

        daily_null = conn.execute(
            "SELECT day_num, count FROM daily_registrations WHERE launch_id=? AND channel_id IS NULL ORDER BY day_num",
            (launch_id,)
        ).fetchall()

        ch_rows = conn.execute(
            """SELECT lc.plan, lc.responsible, c.name AS ch_name, c.id AS ch_id
               FROM launch_channels lc
               JOIN channels c ON c.id = lc.channel_id
               WHERE lc.launch_id=?
               ORDER BY lc.plan DESC""",
            (launch_id,)
        ).fetchall()

        channels_out = []
        max_day = 0
        for ch in ch_rows:
            dregs = conn.execute(
                "SELECT day_num, count FROM daily_registrations WHERE launch_id=? AND channel_id=? ORDER BY day_num",
                (launch_id, ch["ch_id"])
            ).fetchall()
            day_map = {r["day_num"]: r["count"] for r in dregs}
            if day_map:
                max_day = max(max_day, max(day_map.keys()))
            ch_total = sum(day_map.values())
            channels_out.append({
                "name":        ch["ch_name"],
                "plan":        ch["plan"],
                "responsible": ch["responsible"],
                "daily":       day_map,
                "total_actual": ch_total,
            })

        if daily_null:
            max_day = max(max_day, max(r["day_num"] for r in daily_null))
            day_total_map = {r["day_num"]: r["count"] for r in daily_null}
        else:
            day_total_map = {}
            for ch in channels_out:
                for d, cnt in ch["daily"].items():
                    day_total_map[d] = day_total_map.get(d, 0) + cnt

        n_days = max(max_day, 1)
        daily_total_list = [day_total_map.get(i, 0) for i in range(1, n_days + 1)]
        total_actual = sum(daily_total_list)

        for ch in channels_out:
            ch["daily"] = [ch["daily"].get(i, 0) for i in range(1, n_days + 1)]

        completion_pct = round(total_actual / l["total_plan"] * 100, 1) if l["total_plan"] > 0 else 0

        return {
            "overview": {
                "id":             l["id"],
                "name":           l["name"],
                "reg_start":      l["reg_start"],
                "reg_end":        l["reg_end"],
                "event_date":     l["event_date"],
                "event_end_date": l["event_end_date"],
                "total_plan":     l["total_plan"],
                "total_actual":   total_actual,
                "completion_pct": completion_pct,
                "is_active":      l["is_active"],
            },
            "channels":    channels_out,
            "daily_total": daily_total_list,
        }


def get_channel_history(channel_name: str) -> dict:
    """История одного канала по всем запускам: план/факт/% хронологически,
    плюс агрегаты (средний %, лучший/худший запуск, тренд)."""
    with get_db() as conn:
        ch = conn.execute(
            "SELECT id, name FROM channels WHERE name=?", (channel_name,)
        ).fetchone()
        if not ch:
            return None
        ch_id = ch["id"]

        rows = conn.execute(
            """SELECT l.id, l.name, l.reg_start, l.event_date, l.is_active,
                      lc.plan AS plan, lc.responsible AS responsible
               FROM launch_channels lc
               JOIN launches l ON l.id = lc.launch_id
               WHERE lc.channel_id = ?
               ORDER BY COALESCE(l.reg_start, l.event_date, '')""",
            (ch_id,)
        ).fetchall()

        history = []
        for r in rows:
            actual = conn.execute(
                "SELECT COALESCE(SUM(count),0) AS s FROM daily_registrations WHERE launch_id=? AND channel_id=?",
                (r["id"], ch_id)
            ).fetchone()["s"]
            plan = r["plan"] or 0
            pct = round(actual / plan * 100, 1) if plan > 0 else None
            history.append({
                "launch_id":   r["id"],
                "launch_name": r["name"],
                "reg_start":   r["reg_start"],
                "event_date":  r["event_date"],
                "is_active":   r["is_active"],
                "plan":        plan,
                "actual":      actual,
                "pct":         pct,
                "responsible": r["responsible"],
            })

        # агрегаты по завершённым запускам (где есть план и факт)
        pcts = [h["pct"] for h in history if h["pct"] is not None and not h["is_active"]]
        actuals = [h for h in history if not h["is_active"]]
        avg_pct = round(sum(pcts) / len(pcts), 1) if pcts else None
        best = max((h for h in actuals if h["pct"] is not None), key=lambda x: x["pct"], default=None)
        worst = min((h for h in actuals if h["pct"] is not None), key=lambda x: x["pct"], default=None)
        max_actual = max(actuals, key=lambda x: x["actual"], default=None)

        # тренд: сравниваем средний % первой половины и второй половины
        trend = None
        if len(pcts) >= 4:
            mid = len(pcts) // 2
            first = sum(pcts[:mid]) / mid
            second = sum(pcts[mid:]) / (len(pcts) - mid)
            diff = round(second - first, 1)
            trend = {"direction": "up" if diff > 3 else ("down" if diff < -3 else "flat"), "diff": diff}

        return {
            "channel":     ch["name"],
            "history":     history,
            "avg_pct":     avg_pct,
            "best":        {"launch_name": best["launch_name"], "pct": best["pct"]} if best else None,
            "worst":       {"launch_name": worst["launch_name"], "pct": worst["pct"]} if worst else None,
            "max_actual":  {"launch_name": max_actual["launch_name"], "actual": max_actual["actual"]} if max_actual else None,
            "total_launches": len(history),
            "trend":       trend,
        }


def save_utm_stats(launch_id: int, stats: list[dict]):
    """stats = [{utm_source, utm_medium, platform, count, resolved_channel}]"""
    with get_db() as conn:
        conn.execute("DELETE FROM utm_label_stats WHERE launch_id=?", (launch_id,))
        for item in stats:
            conn.execute(
                """INSERT INTO utm_label_stats(launch_id, utm_source, utm_medium, platform, count, resolved_channel)
                   VALUES(?,?,?,?,?,?)""",
                (launch_id,
                 item.get("utm_source", ""),
                 item.get("utm_medium", ""),
                 item.get("platform", ""),
                 item.get("count", 0),
                 item.get("resolved_channel", ""))
            )


def get_utm_stats(launch_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT utm_source, utm_medium, platform, count, resolved_channel
               FROM utm_label_stats WHERE launch_id=?
               ORDER BY count DESC""",
            (launch_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def save_unmatched_labels(launch_id: int, labels: list[dict]):
    """labels = [{utm_source, utm_medium, platform, count}]"""
    with get_db() as conn:
        conn.execute("DELETE FROM unmatched_labels WHERE launch_id=?", (launch_id,))
        for item in labels:
            conn.execute(
                """INSERT INTO unmatched_labels(launch_id, utm_source, utm_medium, platform, count)
                   VALUES(?,?,?,?,?)""",
                (launch_id,
                 item.get("utm_source", ""),
                 item.get("utm_medium", ""),
                 item.get("platform", ""),
                 item.get("count", 0))
            )


def get_unmatched_labels(launch_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT utm_source, utm_medium, platform, count
               FROM unmatched_labels WHERE launch_id=? ORDER BY count DESC""",
            (launch_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def save_label_mapping(utm_source: str, utm_medium: str, platform: str, channel_name: str):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO label_mappings(utm_source, utm_medium, platform, channel_name)
               VALUES(?,?,?,?)
               ON CONFLICT(utm_source, utm_medium, platform)
               DO UPDATE SET channel_name=excluded.channel_name""",
            (utm_source, utm_medium, platform, channel_name)
        )


def delete_label_mapping(utm_source: str, utm_medium: str, platform: str = ""):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM label_mappings WHERE utm_source=? AND utm_medium=? AND platform=?",
            (utm_source, utm_medium, platform)
        )


def get_label_mappings() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT utm_source, utm_medium, platform, channel_name FROM label_mappings ORDER BY utm_source, utm_medium, platform"
        ).fetchall()
        return [dict(r) for r in rows]


def create_launch(name: str, reg_start=None, reg_end=None, event_date=None,
                  event_end_date=None, total_plan: int = 0, channels: list = None) -> int:
    with get_db() as conn:
        conn.execute("UPDATE launches SET is_active=0")
        conn.execute(
            """INSERT INTO launches(name, reg_start, reg_end, event_date, event_end_date, total_plan, is_active)
               VALUES(?,?,?,?,?,?,1)""",
            (name, reg_start, reg_end, event_date, event_end_date, total_plan)
        )
        launch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        if channels:
            for ch in channels:
                ch_id = upsert_channel(conn, ch["name"])
                conn.execute(
                    """INSERT OR REPLACE INTO launch_channels(launch_id, channel_id, plan, responsible)
                       VALUES(?,?,?,?)""",
                    (launch_id, ch_id, ch.get("plan", 0), ch.get("responsible", ""))
                )
        return launch_id


def update_launch(launch_id: int, **fields) -> dict:
    """Точечное обновление метаданных запуска. Обновляются только переданные
    поля из белого списка. Пустая строка трактуется как очистка (NULL).
    Возвращает {id, updated:[...]} либо None, если запуск не найден."""
    allowed = ("name", "reg_start", "reg_end", "event_date", "event_end_date", "total_plan")
    sets, params, updated = [], [], []
    for key in allowed:
        if key in fields:
            val = fields[key]
            if isinstance(val, str) and val.strip() == "":
                val = None
            sets.append(f"{key}=?")
            params.append(val)
            updated.append(key)
    if not sets:
        return {"id": launch_id, "updated": []}
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM launches WHERE id=?", (launch_id,)).fetchone()
        if not exists:
            return None
        params.append(launch_id)
        conn.execute(f"UPDATE launches SET {', '.join(sets)} WHERE id=?", params)
    return {"id": launch_id, "updated": updated}
