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


def _resolve_channel(conn, normalized, trigger, mapping, db_map):
    """Канал для строки: сначала utm_mappings (новый путь), затем fallback на
    старый резолвер sheets_importer._resolve (хардкод-правила + Справочник).
    Возвращает channel_id или None (только для настоящих 'без метки')."""
    cid = db.resolve_channel_by_utm(conn, normalized.get("utm_source"),
                                    normalized.get("utm_medium"), normalized.get("platform"))
    if cid is not None:
        return cid
    from sheets_importer import _resolve
    name = _resolve(normalized.get("utm_source") or "", normalized.get("utm_medium") or "",
                    trigger or "", normalized.get("platform") or "", mapping, db_map)
    if name and name != "без метки":
        return db.upsert_channel(conn, name)
    return None


def reresolve_raw_channels(launch_id) -> dict:
    """Переразобрать канал у уже импортированных raw_registrations
    (после расширения правил). Обновляет channel_id, возвращает счётчики."""
    import gspread
    from sheets_importer import _build_mapping, _load_db_mappings
    gc = gspread.service_account(filename=str(BASE_DIR / "credentials.json"))
    try:
        mapping = _build_mapping(gc)
    except Exception as e:
        log.warning(f"[reresolve] Справочник недоступен: {e}")
        mapping = {}
    db_map = _load_db_mappings()

    changed = resolved_now = 0
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, utm_source, utm_medium, platform, channel_id, "
            "json_extract(raw_payload,'$[7]') AS trig FROM raw_registrations WHERE launch_id=?",
            (launch_id,)
        ).fetchall()
        for r in rows:
            n = {"utm_source": r["utm_source"], "utm_medium": r["utm_medium"], "platform": r["platform"]}
            new_cid = _resolve_channel(conn, n, r["trig"], mapping, db_map)
            if new_cid != r["channel_id"]:
                conn.execute("UPDATE raw_registrations SET channel_id=? WHERE id=?", (new_cid, r["id"]))
                changed += 1
            if new_cid is not None:
                resolved_now += 1
    return {"launch_id": launch_id, "rows": len(rows), "changed": changed, "resolved": resolved_now}


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
                                     sheet_id: str = None, sheet_name: str = None,
                                     rows=None) -> dict:
    """Импортировать регистрации в raw_registrations.
    Если передан ``rows`` (список строк-списков) — импортируем их (mock/тест,
    без сети). Иначе читаем Google Sheets.
    Возвращает сводку: import_run_id, rows_read/imported/skipped/failed, unknown_utm_count."""
    run_id = db.create_import_run(source=source)
    rows_read = rows_imported = rows_skipped = rows_failed = 0
    unknown = set()

    try:
        from sheets_importer import _load_db_mappings
        if rows is None:
            import gspread
            from sheets_importer import SHEET_ID_REGS, MAIN_SHEET_NAME, _build_mapping
            sheet_id = sheet_id or SHEET_ID_REGS
            sheet_name = sheet_name or MAIN_SHEET_NAME
            gc = gspread.service_account(filename=str(BASE_DIR / "credentials.json"))
            try:
                mapping = _build_mapping(gc)
            except Exception as e:
                log.warning(f"[raw_import] Справочник недоступен: {e}")
                mapping = {}
            ws = gc.open_by_key(sheet_id).worksheet(sheet_name)
            all_rows = ws.get_all_values()
            data_rows = all_rows[1:] if all_rows else []
        else:
            mapping = {}            # mock-режим: без Справочника
            data_rows = rows
        db_map = _load_db_mappings()
        windows = db.get_launch_windows()

        with db.get_db() as conn:
            for r in data_rows:
                if not any((c or "").strip() for c in r):
                    continue
                rows_read += 1
                try:
                    n = normalize_sheet_row(r)
                    trigger = r[7].strip() if len(r) > 7 else ""
                    lid = _match_launch(windows, n["registration_date"], prefer=launch_id)
                    rhash = build_registration_row_hash(n, launch_id=lid)
                    chan = _resolve_channel(conn, n, trigger, mapping, db_map)
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
