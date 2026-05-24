#!/usr/bin/env python3
"""One-time migration: Google Sheets -> SQLite"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from db import init_db, get_db, upsert_channel, add_daily_registration
from sheets_client import SheetsClient


def _num(val) -> int:
    if not val or str(val).strip() in ('', '-', '#REF!', '#N/A'):
        return 0
    try:
        cleaned = str(val).replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
        return int(float(cleaned))
    except Exception:
        return 0


# ── Name matching between "Каналы" sheet launch names and "Дни" sheet names ──
LAUNCH_NAME_MAP = {
    'вайбкодинг': 'ВАЙБКОДИНГ 1 сезон 8-10 февраля',
    'нкц 7': '12-14.12 НКЦ7',
    'антитренды': 'АНТИТРЕНДЫ 2026 23-25 января',
    'нкц 8': 'НКЦ 20-22 февраля',
    'практикум': '09-11.03 ПРАКТИКУМ',
}


def match_launch_name(raw_name: str, name_to_id: dict) -> int | None:
    """Try to match a raw launch name (from Каналы sheet) to a launch id in DB."""
    raw_lower = raw_name.lower().strip()
    # Direct match
    if raw_name in name_to_id:
        return name_to_id[raw_name]
    # Keyword match via LAUNCH_NAME_MAP
    for keyword, canonical in LAUNCH_NAME_MAP.items():
        if keyword in raw_lower:
            if canonical in name_to_id:
                return name_to_id[canonical]
    # Fuzzy: check if raw_name fragments are in any key
    for db_name, db_id in name_to_id.items():
        if raw_lower in db_name.lower() or db_name.lower() in raw_lower:
            return db_id
    return None


def migrate_dni_sheet(client: SheetsClient, conn) -> dict:
    """Read 'дни' sheet, create launches, insert daily totals. Returns name->id map."""
    print("\n[1/3] Читаю лист 'дни'...")
    try:
        ws = client._find_ws(["процент", "для расчёта", "дни"])
        rows = ws.get_all_values()
    except Exception as e:
        print(f"  Ошибка: {e}")
        return {}

    name_to_id = {}
    imported = 0
    skipped = 0

    for i, row in enumerate(rows):
        if i == 0:
            continue  # header
        if not row or not str(row[0]).strip():
            continue
        name = str(row[0]).strip()
        # col 10 = total (index 10)
        total = _num(row[10]) if len(row) > 10 else 0
        if total == 0:
            skipped += 1
            continue

        # Insert launch (ignore if exists by name)
        existing = conn.execute("SELECT id FROM launches WHERE name=?", (name,)).fetchone()
        if existing:
            launch_id = existing["id"]
        else:
            conn.execute(
                "INSERT INTO launches(name, total_plan, is_active) VALUES(?,?,0)",
                (name, 0)
            )
            launch_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

        name_to_id[name] = launch_id

        # Daily totals in cols 1-8 (day 1 through day 8), channel_id=NULL
        for day_idx in range(1, 9):
            if day_idx < len(row):
                cnt = _num(row[day_idx])
                if cnt > 0:
                    conn.execute(
                        """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                           VALUES(?,NULL,?,?)
                           ON CONFLICT(launch_id, channel_id, day_num)
                           DO UPDATE SET count=excluded.count""",
                        (launch_id, day_idx, cnt)
                    )
        imported += 1
        print(f"  + {name} (итого {total})")

    print(f"  Импортировано: {imported}, пропущено (нет данных): {skipped}")
    return name_to_id


def migrate_kanaly_sheet(client: SheetsClient, conn, name_to_id: dict):
    """Read 'Каналы' sheet, insert per-channel daily data."""
    print("\n[2/3] Читаю лист 'Каналы'...")
    try:
        ws = client._find_ws(["каналы", "канал"])
        rows = ws.get_all_values()
    except Exception as e:
        print(f"  Ошибка: {e}")
        return

    if len(rows) < 4:
        print("  Лист слишком короткий, пропускаю.")
        return

    # Row 1 (index 1): launch names at cols 3, 14, 25, 36, 47
    launch_name_row = rows[1] if len(rows) > 1 else []
    LAUNCH_COL_OFFSETS = [3, 14, 25, 36, 47]  # col where day-1 data starts per launch block
    TOTAL_COLS = [11, 22, 33, 44, 55]  # total col per block

    # Collect launch names from row 1
    block_launch_ids = []
    for col_offset in LAUNCH_COL_OFFSETS:
        raw = launch_name_row[col_offset - 1] if col_offset - 1 < len(launch_name_row) else ''
        if not raw:
            # try adjacent columns
            for delta in range(-2, 3):
                idx = col_offset - 1 + delta
                if 0 <= idx < len(launch_name_row) and launch_name_row[idx].strip():
                    raw = launch_name_row[idx]
                    break
        lid = match_launch_name(raw.strip(), name_to_id) if raw.strip() else None
        block_launch_ids.append((raw.strip(), lid))
        print(f"  Блок col={col_offset}: '{raw.strip()}' -> launch_id={lid}")

    # Rows 4+ (index 4): channel data
    channels_imported = 0
    for row in rows[4:]:
        if not row:
            continue
        ch_name = str(row[0]).strip()
        if not ch_name:
            continue

        ch_id = upsert_channel(conn, ch_name)

        for block_idx, (col_offset, total_col) in enumerate(zip(LAUNCH_COL_OFFSETS, TOTAL_COLS)):
            raw_name, launch_id = block_launch_ids[block_idx]
            if not launch_id:
                continue

            # Days 1-8 in cols col_offset to col_offset+7
            any_data = False
            for day_num in range(1, 9):
                col_idx = col_offset + day_num - 1
                if col_idx < len(row):
                    cnt = _num(row[col_idx])
                    if cnt > 0:
                        any_data = True
                        conn.execute(
                            """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                               VALUES(?,?,?,?)
                               ON CONFLICT(launch_id, channel_id, day_num)
                               DO UPDATE SET count=excluded.count""",
                            (launch_id, ch_id, day_num, cnt)
                        )

            # Ensure channel is in launch_channels if it has data
            if any_data:
                conn.execute(
                    """INSERT OR IGNORE INTO launch_channels(launch_id, channel_id, plan, responsible)
                       VALUES(?,?,0,'')""",
                    (launch_id, ch_id)
                )
                channels_imported += 1

    print(f"  Поканальных записей добавлено: {channels_imported}")


def migrate_cache(conn):
    """Load data_cache.json, create active launch 'Вайбкодим с телефона'."""
    print("\n[3/3] Импортирую data_cache.json (текущий запуск)...")
    cache_path = Path(__file__).parent / "data_cache.json"
    if not cache_path.exists():
        print("  data_cache.json не найден, пропускаю.")
        return

    with open(cache_path, encoding="utf-8") as f:
        data = json.load(f)

    overview = data.get("overview", {})
    daily = data.get("daily", {})
    channels_raw = data.get("channels", [])

    name = "Вайбкодим с телефона"
    reg_start = overview.get("start_date")
    reg_end = overview.get("end_date")
    total_plan = overview.get("total_plan", 0)

    # Deactivate existing
    conn.execute("UPDATE launches SET is_active=0")

    existing = conn.execute("SELECT id FROM launches WHERE name=?", (name,)).fetchone()
    if existing:
        launch_id = existing["id"]
        conn.execute(
            "UPDATE launches SET is_active=1, reg_start=?, reg_end=?, total_plan=? WHERE id=?",
            (reg_start, reg_end, total_plan, launch_id)
        )
    else:
        conn.execute(
            "INSERT INTO launches(name, reg_start, reg_end, total_plan, is_active) VALUES(?,?,?,?,1)",
            (name, reg_start, reg_end, total_plan)
        )
        launch_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

    # Insert channel plan/actual
    for ch in channels_raw:
        ch_name = ch.get("name", "")
        if not ch_name:
            continue
        ch_id = upsert_channel(conn, ch_name)
        conn.execute(
            """INSERT OR REPLACE INTO launch_channels(launch_id, channel_id, plan, responsible)
               VALUES(?,?,?,?)""",
            (launch_id, ch_id, ch.get("plan", 0), ch.get("responsible", ""))
        )
        # actual goes into day_num=0 placeholder (we don't have daily breakdown per channel in cache)
        actual = ch.get("actual", 0)
        if actual > 0:
            conn.execute(
                """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                   VALUES(?,?,?,?)
                   ON CONFLICT(launch_id, channel_id, day_num)
                   DO UPDATE SET count=excluded.count""",
                (launch_id, ch_id, 0, actual)
            )

    # Insert daily totals (channel_id NULL)
    dates = daily.get("dates", [])
    daily_actual = daily.get("daily_actual", [])
    for i, cnt in enumerate(daily_actual):
        if cnt > 0:
            conn.execute(
                """INSERT INTO daily_registrations(launch_id, channel_id, day_num, count)
                   VALUES(?,NULL,?,?)
                   ON CONFLICT(launch_id, channel_id, day_num)
                   DO UPDATE SET count=excluded.count""",
                (launch_id, i + 1, cnt)
            )

    print(f"  Активный запуск: '{name}' (id={launch_id}), план={total_plan}")
    print(f"  Каналов: {len(channels_raw)}, дней с данными: {sum(1 for c in daily_actual if c > 0)}")


def main():
    print("=== Миграция Google Sheets -> SQLite ===")
    init_db()
    print("БД инициализирована.")

    try:
        client = SheetsClient()
        print("Подключился к Google Sheets.")
        use_sheets = True
    except Exception as e:
        print(f"Google Sheets недоступен: {e}")
        use_sheets = False

    with get_db() as conn:
        name_to_id = {}
        if use_sheets:
            name_to_id = migrate_dni_sheet(client, conn)
            migrate_kanaly_sheet(client, conn, name_to_id)
        migrate_cache(conn)

    print("\n=== Готово ===")

    # Print summary
    with get_db() as conn:
        n_launches = conn.execute("SELECT COUNT(*) as c FROM launches").fetchone()["c"]
        n_channels = conn.execute("SELECT COUNT(*) as c FROM channels").fetchone()["c"]
        n_regs = conn.execute("SELECT COUNT(*) as c FROM daily_registrations").fetchone()["c"]
        active = conn.execute("SELECT name FROM launches WHERE is_active=1").fetchone()
        print(f"Запусков: {n_launches}, каналов: {n_channels}, записей регистраций: {n_regs}")
        print(f"Активный запуск: {active['name'] if active else 'нет'}")


if __name__ == "__main__":
    main()
