#!/usr/bin/env python3
"""
rnp_core_smoke.py — end-to-end проверка расчётного ядра на mock-данных (Задача 6.1).
Изолированно: временная БД (DATA_DIR), без сети. Запуск: python3 rnp_core_smoke.py

Сценарий: 3 истории -> 3 канала -> новый запуск -> план -> mock-импорт
(известная/неизвестная UTM + дубль) -> импорт x2 -> нет дублей -> unknown UTM
-> назначение -> пересчёт факта -> dashboard payload -> план/факт/прогноз.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

# изолированная временная БД ДО импорта db
_tmp = tempfile.mkdtemp(prefix="rnp_smoke_")
os.environ["DATA_DIR"] = _tmp

import db
import raw_import

_PASS = _FAIL = 0


def ok(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {label}")
    else:
        _FAIL += 1
        print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))


def d(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def dt(offset, hm="10:00"):
    return (date.today() + timedelta(days=offset)).strftime("%d.%m.%Y") + " " + hm


def mock_row(user, when, src="", med="", platform="", phone="", trigger="reg"):
    r = [""] * 18
    r[0] = user; r[3] = when; r[6] = phone; r[7] = trigger
    r[8] = src; r[9] = med; r[17] = platform
    return r


print("RNP core smoke (mock, временная БД)")
db.init_db()

# ── 1. история: 3 прошлых запуска с дневным фактом ──────────────────────────
print("\n[1] 3 исторических запуска")
with db.get_db() as conn:
    for i, (nm, start, total) in enumerate([
        ("История A", d(-40), 300), ("История B", d(-30), 600), ("История C", d(-20), 450),
    ]):
        conn.execute("INSERT INTO launches(name,reg_start,reg_end,event_date,total_plan,is_active) VALUES(?,?,?,?,?,0)",
                     (nm, start, (date.fromisoformat(start) + timedelta(days=2)).isoformat(),
                      (date.fromisoformat(start) + timedelta(days=2)).isoformat(), total))
        lid = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        # дневной агрегат (channel_id NULL): фронт-лоад
        shares = [0.5, 0.3, 0.2]
        for day in range(3):
            conn.execute("INSERT INTO daily_registrations(launch_id,channel_id,day_num,count) VALUES(?,?,?,?)",
                         (lid, None, day + 1, round(total * shares[day])))
hist = db.get_history_launches(limit=10)
ok("история доступна (>=3)", len(hist) >= 3, str(len(hist)))

# ── 2-4. новый запуск с каналами и планами (авто-генерация плана) ───────────
print("\n[2-4] новый запуск + каналы + планы")
new_id = db.create_launch(
    name="СМОУК новый", reg_start=d(-1), reg_end=d(1), event_date=d(1), total_plan=300,
    channels=[{"name": "ТГ Боты Димы", "plan": 150}, {"name": "Email", "plan": 100}, {"name": "без метки", "plan": 50}],
)
ok("запуск создан", isinstance(new_id, int))

# ── 5. план сохранён ────────────────────────────────────────────────────────
print("\n[5] daily_plans сгенерированы")
ap = db.get_active_daily_plan(new_id)
plan_sum = sum(r["plan_count"] for r in ap["rows"])
ok("план сохранён (версия 1)", ap["plan_version"] == 1, str(ap["plan_version"]))
ok("сумма плана == 300", plan_sum == 300, str(plan_sum))
ok("снапшот истории есть", bool(ap["method_snapshot"] and ap["method_snapshot"].get("historyLaunchIds")))

# ── 6. mock-строки: известная UTM, неизвестная, дубль ───────────────────────
print("\n[6-7] mock-импорт (known/unknown/дубль) x2")
# правило для известной метки promo/launch -> ТГ Боты Димы
with db.get_db() as conn:
    cid_tgb = db.upsert_channel(conn, "ТГ Боты Димы")
db.assign_utm_to_channel("promo", "launch", "", channel_id=cid_tgb)

rows = [
    mock_row("u1", dt(-1, "09:00"), "promo", "launch", "tg", phone="111"),   # known
    mock_row("u2", dt(-1, "09:05"), "promo", "launch", "tg", phone="222"),   # known
    mock_row("u3", dt(0, "10:00"), "mystery", "qq", "tg", phone="333"),      # unknown
    mock_row("u1", dt(-1, "09:00"), "promo", "launch", "tg", phone="111"),   # ДУБЛЬ u1
]
r1 = raw_import.import_registrations_from_sheets(launch_id=new_id, source="smoke", rows=rows)
r2 = raw_import.import_registrations_from_sheets(launch_id=new_id, source="smoke", rows=rows)
ok("1-й импорт: 3 уникальных (дубль отсеян)", r1["rows_imported"] == 3, str(r1["rows_imported"]))
ok("дубль в 1-м прогоне пропущен", r1["rows_skipped"] == 1, str(r1["rows_skipped"]))

# ── 8. идемпотентность ──────────────────────────────────────────────────────
print("\n[8] идемпотентность")
ok("2-й импорт: 0 новых", r2["rows_imported"] == 0, str(r2["rows_imported"]))
ok("2-й импорт: всё пропущено (4)", r2["rows_skipped"] == 4, str(r2["rows_skipped"]))

# ── 9. unknown UTM ──────────────────────────────────────────────────────────
print("\n[9] unknown UTM")
unk = db.get_unknown_utm(new_id)
ok("в списке есть mystery/qq", any(u["utmSource"] == "mystery" for u in unk), str(unk))
ok("known promo НЕ в unknown", not any(u["utmSource"] == "promo" for u in unk))

# ── 10-11. назначение -> пересчёт факта ─────────────────────────────────────
print("\n[10-11] назначение UTM -> пересчёт")
res = db.assign_utm_to_channel("mystery", "qq", "tg", channel_name="Email")
ok("перераспределено >=1 строка", res["updated_rows"] >= 1, str(res["updated_rows"]))
unk2 = db.get_unknown_utm(new_id)
ok("mystery исчез из unknown", not any(u["utmSource"] == "mystery" for u in unk2))
agg = db.aggregate_fact_from_raw(new_id)
email_id = None
with db.get_db() as conn:
    email_id = conn.execute("SELECT id FROM channels WHERE name='Email'").fetchone()["id"]
ok("Email получил факт после назначения", agg["by_channel"].get(email_id, 0) >= 1, str(agg["by_channel"]))

# ── 12-13. dashboard payload ────────────────────────────────────────────────
print("\n[12-13] dashboard payload")
ov = raw_import.db  # noqa
from db import build_raw_override, get_dashboard_from_db
dash = get_dashboard_from_db(new_id, live_override=build_raw_override(new_id))
o = dash["overview"]
ok("payload: overview/daily/channels", all(k in dash for k in ("overview", "daily", "channels")))
ok("факт == 3 уникальных", o["total_actual"] == 3, str(o["total_actual"]))
ok("dailyPlan.length == dates.length", len(dash["daily"]["daily_plan"]) == len(dash["daily"]["dates"]))
ok("cumulativePlan[-1] == total_plan", dash["daily"]["cumulative_plan"][-1] == o["total_plan"])
ok("unknownUtm — массив", isinstance(dash.get("unknownUtm"), list))
ok("forecastTotal присутствует", "forecastTotal" in o)
ok("сумма факта по каналам == факт", sum((c.get("actual") or 0) for c in dash["channels"]) == o["total_actual"],
   str(sum((c.get("actual") or 0) for c in dash["channels"])))

print("\n" + "=" * 46)
print(f"ИТОГ: {_PASS} PASS, {_FAIL} FAIL  (temp: {_tmp})")
print("=" * 46)
sys.exit(1 if _FAIL else 0)
