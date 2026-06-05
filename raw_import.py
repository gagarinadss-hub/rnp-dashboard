"""
raw_import.py — построчный идемпотентный импорт регистраций в raw_registrations.

Этап 3 (Задача 3.3). Работает ПАРАЛЛЕЛЬНО старому агрегатному run_import
(sheets_importer.py) и не трогает daily_registrations. Дашборд переключится
на raw_registrations в Задаче 5.1.

Поток:
  import_run(running)
   -> читаем строки Google Sheets
   -> normalize_sheet_row + build_registration_row_hash
   -> привязка launch_id по дате
   -> resolve_channel_by_utm (неизвестные -> channel_id=null)
   -> INSERT OR IGNORE (дубли по row_hash пропускаются)
   -> import_run(success/failed)
"""
import logging
from pathlib import Path

import db
from sheet_normalize import normalize_sheet_row, build_registration_row_hash

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).parent


def _match_launch(windows, reg_date, prefer=None):
    """Найти launch по дате регистрации (reg_start <= date <= reg_end).
    Если подходит prefer — берём его; иначе активный; иначе первый; иначе None."""
    if not reg_date:
        return prefer
    matches = [w for w in windows
               if w["reg_start"] and w["reg_end"] and w["reg_start"] <= reg_date <= w["reg_end"]]
    if not matches:
        return None
    if prefer is not None:
        for w in matches:
            if w["id"] == prefer:
                return prefer
    for w in matches:
        if w["is_active"]:
            return w["id"]
    return matches[0]["id"]


def import_registrations_from_sheets(launch_id=None, source: str = "manual",
                                     sheet_id: str = None, sheet_name: str = None) -> dict:
    """Импортировать регистрации из Google Sheets в raw_registrations.
    Возвращает сводку: import_run_id, rows_read/imported/skipped/failed, unknown_utm_count."""
    import gspread
    from sheets_importer import SHEET_ID_REGS, MAIN_SHEET_NAME

    sheet_id = sheet_id or SHEET_ID_REGS
    sheet_name = sheet_name or MAIN_SHEET_NAME

    run_id = db.create_import_run(source=source)
    rows_read = rows_imported = rows_skipped = rows_failed = 0
    unknown = set()

    try:
        gc = gspread.service_account(filename=str(BASE_DIR / "credentials.json"))
        ws = gc.open_by_key(sheet_id).worksheet(sheet_name)
        all_rows = ws.get_all_values()
        data_rows = all_rows[1:] if all_rows else []
        windows = db.get_launch_windows()

        with db.get_db() as conn:
            for r in data_rows:
                if not any((c or "").strip() for c in r):
                    continue
                rows_read += 1
                try:
                    n = normalize_sheet_row(r)
                    lid = _match_launch(windows, n["registration_date"], prefer=launch_id)
                    rhash = build_registration_row_hash(n, launch_id=lid)
                    chan = db.resolve_channel_by_utm(conn, n["utm_source"], n["utm_medium"], n["platform"])
                    if chan is None and (n["utm_source"] or n["utm_medium"] or n["platform"]):
                        unknown.add((n["utm_source"], n["utm_medium"], n["platform"]))
                    if db.insert_raw_registration(conn, rhash, n, lid, chan):
                        rows_imported += 1
                    else:
                        rows_skipped += 1
                except Exception as row_err:
                    rows_failed += 1
                    log.warning(f"[raw_import] строка пропущена: {row_err}")

        db.finish_import_run(run_id, "success", rows_read, rows_imported,
                             rows_skipped, rows_failed, len(unknown))
        result = {"status": "success"}
    except Exception as e:
        log.exception("[raw_import] импорт упал")
        db.finish_import_run(run_id, "failed", rows_read, rows_imported,
                             rows_skipped, rows_failed, len(unknown), str(e))
        result = {"status": "failed", "error": str(e)}

    result.update({
        "import_run_id": run_id,
        "rows_read": rows_read,
        "rows_imported": rows_imported,
        "rows_skipped": rows_skipped,
        "rows_failed": rows_failed,
        "unknown_utm_count": len(unknown),
    })
    return result
