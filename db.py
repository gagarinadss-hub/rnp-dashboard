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
        for sql in [
            "ALTER TABLE launches ADD COLUMN event_end_date TEXT",
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


def _cumulative(values):
    result, running = [], 0
    for v in values:
        running += v
        result.append(running)
    return result


def get_dashboard_from_db(launch_id: int) -> dict:
    """Compute a full dashboard payload from SQLite data only."""
    with get_db() as conn:
        l = conn.execute(
            "SELECT id, name, reg_start, reg_end, event_date, event_end_date, total_plan, is_active FROM launches WHERE id=?",
            (launch_id,)
        ).fetchone()
        if not l:
            return None

        today = date.today()
        reg_start = date.fromisoformat(l["reg_start"]) if l["reg_start"] else today - timedelta(days=6)
        reg_end   = date.fromisoformat(l["reg_end"])   if l["reg_end"]   else today

        total_days     = max(1, (reg_end - reg_start).days + 1)
        days_elapsed   = max(1, min((today - reg_start).days + 1, total_days))
        days_remaining = max(0, (reg_end - today).days)

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

        # yesterday / day-before indices (0-based)
        yesterday_idx   = days_elapsed - 2   # day before today
        day_before_idx  = days_elapsed - 3   # two days ago

        channels = []
        for ch in ch_rows:
            dregs = conn.execute(
                "SELECT day_num, count FROM daily_registrations WHERE launch_id=? AND channel_id=? ORDER BY day_num",
                (launch_id, ch["ch_id"])
            ).fetchall()
            day_map = {r["day_num"]: r["count"] for r in dregs}

            daily_actual = [day_map.get(i + 1, 0) for i in range(total_days)]
            total_actual_ch = sum(daily_actual)
            ch_plan = ch["plan"] or 0

            # Even distribution of plan across days
            if ch_plan > 0 and total_days > 0:
                base      = ch_plan // total_days
                remainder = ch_plan % total_days
                daily_plan = [base] * total_days
                if remainder:
                    daily_plan[-1] += remainder
            else:
                daily_plan = [0] * total_days

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

        # Forecast scenarios
        completed_days = daily_total_actual[:days_elapsed]
        good_days      = [a for a in completed_days if a > 0]
        avg_all        = sum(good_days) / len(good_days) if good_days else 0

        # Realistic: average of all days
        proj_realistic  = int(avg_all * total_days) if avg_all > 0 else total_actual

        # Optimistic: average of top-3 days
        top3 = sorted(good_days, reverse=True)[:3]
        proj_optimistic = int((sum(top3) / len(top3)) * total_days) if top3 else proj_realistic

        # Pessimistic: average of last-3 days (may be declining)
        last3 = good_days[-3:] if len(good_days) >= 3 else good_days
        proj_pessimistic = int((sum(last3) / len(last3)) * total_days) if last3 else proj_realistic

        projected_total = proj_realistic
        projected_pct   = round(projected_total / total_plan * 100, 1) if total_plan > 0 else 0

        # Cumulative forecast line (real + projected average for future)
        avg_for_future = int(total_actual / max(days_elapsed, 1))
        forecast_cum   = _cumulative(daily_total_actual[:days_elapsed])
        for _ in range(days_elapsed, total_days):
            forecast_cum.append((forecast_cum[-1] if forecast_cum else 0) + avg_for_future)

        confidence = "высокая" if days_elapsed >= 3 else ("средняя" if days_elapsed >= 1 else "низкая")

        # Best / lag channels (sorted by pace_ratio)
        active_chs    = [c for c in channels if c["plan"] > 0 and c["actual"] > 0]
        best_channels = sorted(active_chs, key=lambda x: x["pct"], reverse=True)[:3]
        lag_channels  = sorted(
            [c for c in channels if c["plan"] > 50 and c["actual"] < c["plan"]],
            key=lambda x: x["pct"]
        )[:3]

        return {
            "overview": {
                "launch_id":        launch_id,
                "launch_name":      l["name"],
                "start_date":       str(reg_start),
                "end_date":         str(reg_end),
                "event_date":       l["event_date"],
                "event_end_date":   l["event_end_date"],
                "total_plan":       total_plan,
                "total_actual":     total_actual,
                "completion_pct":   completion_pct,
                "days_elapsed":     days_elapsed,
                "days_total":       total_days,
                "days_remaining":   days_remaining,
                "today_actual":     today_actual,
                "today_plan":       today_plan,
                "today_pct":        today_pct,
                "yesterday_actual": yesterday_actual,
                "yesterday_delta":  yesterday_delta,
                "pace_needed":      pace_needed,
                "last_updated":     datetime.now().isoformat(),
                "_source":          "db",
            },
            "daily": {
                "dates":             day_dates,
                "daily_actual":      daily_total_actual,
                "daily_plan":        daily_plan_list,
                "cumulative_actual": _cumulative(daily_total_actual),
                "cumulative_plan":   _cumulative(daily_plan_list),
            },
            "channels": channels,
            "forecast": {
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
            },
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
            "SELECT id, name, reg_start, reg_end, event_date, total_plan, is_active FROM launches WHERE id=?",
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
                "total_plan":     l["total_plan"],
                "total_actual":   total_actual,
                "completion_pct": completion_pct,
                "is_active":      l["is_active"],
            },
            "channels":    channels_out,
            "daily_total": daily_total_list,
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
