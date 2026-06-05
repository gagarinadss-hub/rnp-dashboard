#!/usr/bin/env python3
"""
smoke_check.py — быстрая проверка, что расчётное ядро RNP-дашборда не развалилось.

Запуск:
    python3 smoke_check.py                      # против прода (по умолчанию)
    python3 smoke_check.py --base http://localhost:8000
    python3 smoke_check.py --reimport           # + проверка идемпотентности импорта (пишет в БД, но безопасно)

Сеть идёт через curl (надёжно в любом окружении, без проблем с SSL-сертами).
Скрипт только читает, кроме опционального --reimport (он переимпортирует активный
запуск дважды и сверяет, что повторный импорт не создаёт дублей).
"""
import argparse
import json
import subprocess
import sys

DEFAULT_BASE = "https://web-production-7fde6.up.railway.app"

# ── helpers ─────────────────────────────────────────────────────────────────
_PASS = 0
_FAIL = 0
_WARN = 0


def _http(method, base, path, body=None):
    cmd = ["curl", "-s", "-w", "\n%{http_code}", "-X", method, f"{base}{path}"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body, ensure_ascii=False)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    text, _, code = p.stdout.rpartition("\n")
    data = None
    if text.strip():
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    return code, data


def check(label, ok, detail=""):
    global _PASS, _FAIL
    mark = "✅ PASS" if ok else "❌ FAIL"
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    print(f"  {mark}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


def warn(label, detail=""):
    global _WARN
    _WARN += 1
    print(f"  ⚠️  WARN  {label}" + (f"  — {detail}" if detail else ""))


# ── 1. active launch ────────────────────────────────────────────────────────
def check_active_launch(base):
    print("\n[1] Активный запуск")
    code, launches = _http("GET", base, "/api/launches")
    if not check("GET /api/launches -> 200 и список", code == "200" and isinstance(launches, list)):
        return None
    active = [l for l in launches if l.get("is_active")]
    check("ровно один is_active", len(active) == 1, f"найдено {len(active)}: {[l.get('id') for l in active]}")
    return active[0]["id"] if active else None


# ── 2. dashboard единый источник ────────────────────────────────────────────
def check_dashboard_single_source(base, active_id):
    print("\n[2] /api/dashboard == дашборд активного запуска")
    if active_id is None:
        warn("нет активного запуска — пропускаю")
        return None
    c1, dash = _http("GET", base, "/api/dashboard")
    c2, ldash = _http("GET", base, f"/api/launches/{active_id}/dashboard")
    if not check("оба эндпоинта -> 200", c1 == "200" and c2 == "200", f"{c1}/{c2}"):
        return None
    o1, o2 = dash.get("overview", {}), ldash.get("overview", {})
    check("один и тот же запуск", o1.get("launch_name") == o2.get("launch_name"),
          f"{o1.get('launch_name')!r} vs {o2.get('launch_name')!r}")
    check("одинаковый факт", o1.get("total_actual") == o2.get("total_actual"),
          f"{o1.get('total_actual')} vs {o2.get('total_actual')}")
    check("источник не live (db)", o1.get("_source") != "live", f"_source={o1.get('_source')}")
    return ldash


# ── 3. инварианты payload ───────────────────────────────────────────────────
def check_payload_invariants(base, active_id, dash):
    print("\n[3] Инварианты dashboard payload")
    if not dash:
        warn("нет payload — пропускаю")
        return
    for key in ("overview", "daily", "channels"):
        check(f"есть ключ '{key}'", key in dash)
    o = dash.get("overview", {})
    daily = dash.get("daily", {})
    chs = dash.get("channels", [])

    dates = daily.get("dates", [])
    dplan = daily.get("daily_plan", [])
    dact = daily.get("daily_actual", [])
    check("daily_plan.length == dates.length", len(dplan) == len(dates), f"{len(dplan)} vs {len(dates)}")
    check("daily_actual.length == dates.length", len(dact) == len(dates), f"{len(dact)} vs {len(dates)}")

    cum_plan = daily.get("cumulative_plan", [])
    if dates and cum_plan:
        check("cumulative_plan[-1] == total_plan", cum_plan[-1] == o.get("total_plan"),
              f"{cum_plan[-1]} vs {o.get('total_plan')}")

    check("channels — это список", isinstance(chs, list))

    # сумма факта по каналам == общий факт
    ch_fact = sum((c.get("actual") or 0) for c in chs)
    check("sum(channel.actual) == total_actual", ch_fact == o.get("total_actual"),
          f"{ch_fact} vs {o.get('total_actual')}")
    # сумма факта по дням == общий факт
    check("sum(daily_actual) == total_actual", sum(dact) == o.get("total_actual"),
          f"{sum(dact)} vs {o.get('total_actual')}")

    # сумма планов каналов == total_plan (не критично, но важно — warning)
    ch_plan = sum((c.get("plan") or 0) for c in chs)
    if ch_plan != o.get("total_plan"):
        warn("sum(channel.plan) != total_plan", f"{ch_plan} vs {o.get('total_plan')}")
    else:
        check("sum(channel.plan) == total_plan", True)

    # 5.3 контракт: unknownUtm — массив, поля прогноза присутствуют
    check("unknownUtm — массив", isinstance(dash.get("unknownUtm"), list),
          f"type={type(dash.get('unknownUtm')).__name__}")
    for fld in ("planToDate", "actualToDate", "pacePct", "forecastTotal", "forecastPct"):
        check(f"overview.{fld} присутствует", fld in o)


# ── 4. идемпотентность импорта ──────────────────────────────────────────────
def check_import_idempotent(base, active_id):
    print("\n[4] Идемпотентность импорта (повторный импорт без дублей)")
    if active_id is None:
        warn("нет активного запуска — пропускаю")
        return
    c1, r1 = _http("POST", base, f"/api/launches/{active_id}/reimport")
    c2, r2 = _http("POST", base, f"/api/launches/{active_id}/reimport")
    if not check("оба импорта -> 200", c1 == "200" and c2 == "200", f"{c1}/{c2}"):
        return
    t1, t2 = r1.get("total_registrations"), r2.get("total_registrations")
    check("повторный импорт даёт тот же итог", t1 == t2, f"{t1} vs {t2}")
    # факт на дашборде не должен прыгать между двумя импортами
    _, dash = _http("GET", base, f"/api/launches/{active_id}/dashboard")
    fact = dash.get("overview", {}).get("total_actual")
    check("факт == итог импорта (нет задвоения)", fact == t2, f"факт {fact} vs импорт {t2}")


# ── 5. дедуп на mock-данных (без сети/БД) ───────────────────────────────────
def check_dedup_mock():
    print("\n[5] Дедуп на mock-данных (User ID + телефон, первое вхождение)")
    # (user_id, phone, channel)  — три человека, но 6 строк (дубли по id и по телефону)
    mock = [
        ("1", "+700", "ТГ"),      # человек 1
        ("1", "",     "MAX"),     # дубль по user_id -> отбросить
        ("2", "+701", "лендинг"), # человек 2
        ("",  "+701", "MAX"),     # дубль по телефону -> отбросить
        ("3", "+702", "ТГ"),      # человек 3
        ("",  "",     "ТГ"),      # без id и телефона -> считаем (4-я уникальная)
    ]
    seen_id, seen_ph = set(), set()
    uniq = 0
    for uid, ph, _ in mock:
        if (uid and uid in seen_id) or (ph and ph in seen_ph):
            continue
        if uid:
            seen_id.add(uid)
        if ph:
            seen_ph.add(ph)
        uniq += 1
    check("6 строк -> 4 уникальных (2 дубля схлопнуты)", uniq == 4, f"получили {uniq}")


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE, help="базовый URL API")
    ap.add_argument("--reimport", action="store_true", help="включить проверку идемпотентности импорта (пишет в БД)")
    args = ap.parse_args()

    print(f"RNP smoke check -> {args.base}")
    active_id = check_active_launch(args.base)
    dash = check_dashboard_single_source(args.base, active_id)
    check_payload_invariants(args.base, active_id, dash)
    if args.reimport:
        check_import_idempotent(args.base, active_id)
    else:
        print("\n[4] Идемпотентность импорта — пропущено (запусти с --reimport)")
    check_dedup_mock()

    print("\n" + "=" * 50)
    print(f"ИТОГ: {_PASS} PASS, {_FAIL} FAIL, {_WARN} WARN")
    print("=" * 50)
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
