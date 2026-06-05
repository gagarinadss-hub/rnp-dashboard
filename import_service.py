"""
import_service.py — единая точка импорта регистраций (Задача 3.4).

И ручная кнопка, и авто-обновление раз в 5 минут вызывают ОДНУ функцию
run_import_service(). Защита от параллельных импортов через неблокирующий лок:
если импорт уже идёт, второй вызов возвращает {"status": "already_running"}.

На время миграции сервис запускает ОБА пути:
  - raw_import (построчно -> raw_registrations, новый источник правды);
  - aggregate run_import (-> daily_registrations), чтобы текущий дашборд
    оставался актуальным. После Задачи 5.1 агрегат отключим.
"""
import logging
import threading

log = logging.getLogger(__name__)

_import_lock = threading.Lock()


def is_running() -> bool:
    return _import_lock.locked()


def run_import_service(launch_id=None, source: str = "manual",
                       include_aggregate: bool = True) -> dict:
    """Единый импорт. Возвращает {status, raw, aggregate}.
    status: 'ok' | 'already_running' | 'error'."""
    if not _import_lock.acquire(blocking=False):
        log.info("[import_service] импорт уже идёт — пропускаю параллельный вызов")
        return {"status": "already_running", "raw": None, "aggregate": None}
    try:
        raw_result = None
        agg_result = None
        try:
            from raw_import import import_registrations_from_sheets
            raw_result = import_registrations_from_sheets(launch_id=launch_id, source=source)
        except Exception as e:
            log.exception("[import_service] raw-импорт упал")
            raw_result = {"status": "failed", "error": str(e)}

        if include_aggregate and launch_id:
            try:
                from sheets_importer import run_import
                agg_result = run_import(launch_id)
            except Exception as e:
                log.exception("[import_service] агрегатный импорт упал")
                agg_result = {"error": str(e)}

        return {"status": "ok", "raw": raw_result, "aggregate": agg_result}
    finally:
        _import_lock.release()
