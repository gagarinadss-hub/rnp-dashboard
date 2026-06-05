#!/usr/bin/env python3
"""
Unit-тесты для planning_engine. Без pytest — запуск: python3 test_planning_engine.py
Exit code 0 — все прошли, 1 — есть падения.
"""
import sys
import planning_engine as pe

_PASS = 0
_FAIL = 0


def ok(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {label}")
    else:
        _FAIL += 1
        print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))


def approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


def H(launch_id, reg_start, reg_end, counts, total=None):
    """Хелпер: HistoryLaunchInput с дневным фактом по окну [reg_start..]."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(reg_start)
    daily = [{"date": (d0 + timedelta(days=i)).isoformat(), "count": c}
             for i, c in enumerate(counts)]
    return pe.HistoryLaunchInput.from_dict({
        "launchId": launch_id, "regStart": reg_start, "regEnd": reg_end,
        "eventDate": reg_end, "totalActual": total if total is not None else sum(counts),
        "dailyActual": daily, "channels": [],
    })


def common_invariants(label, shares, n):
    ok(f"{label}: длина == {n}", len(shares) == n, f"len={len(shares)}")
    ok(f"{label}: сумма == 1.0", approx(sum(shares), 1.0), f"sum={sum(shares)}")
    ok(f"{label}: все доли >= 0", all(s >= -1e-12 for s in shares),
       f"min={min(shares) if shares else None}")


# ── 1. Пустая история -> fallback ───────────────────────────────────────────
print("[1] пустая история -> fallback")
sh = pe.build_plan_curve([], ["2026-06-02", "2026-06-03", "2026-06-04"])
common_invariants("empty", sh, 3)

# ── 2. Один запуск той же длины -> доли совпадают ───────────────────────────
print("[2] один запуск той же длины")
hist = [H(1, "2026-01-01", "2026-01-03", [10, 35, 55])]  # доли 0.10/0.35/0.55
sh = pe.build_plan_curve(hist, ["a", "b", "c"])
common_invariants("same-len", sh, 3)
ok("same-len: доли == [0.10,0.35,0.55]",
   approx(sh[0], 0.10) and approx(sh[1], 0.35) and approx(sh[2], 0.55),
   detail=str([round(x, 4) for x in sh]))

# ── 3. Один запуск другой длины -> интерполяция ─────────────────────────────
print("[3] один запуск другой длины (3 -> 6 дней)")
sh = pe.build_plan_curve(hist, ["d%d" % i for i in range(6)])
common_invariants("diff-len", sh, 6)
ok("diff-len: монотонная накопительная (доли >= 0)", all(s >= -1e-12 for s in sh))

# ── 4. Несколько запусков -> усреднение ─────────────────────────────────────
print("[4] несколько запусков")
hist3 = [
    H(1, "2026-01-01", "2026-01-03", [10, 35, 55]),
    H(2, "2026-02-01", "2026-02-03", [50, 30, 20]),
    H(3, "2026-03-01", "2026-03-03", [33, 33, 34]),
]
sh = pe.build_plan_curve(hist3, ["a", "b", "c"])
common_invariants("multi", sh, 3)
# день1 ~ среднее (0.10+0.50+0.33)/3 = 0.31
ok("multi: день1 ≈ среднее долей дня1", approx(sh[0], (0.10 + 0.50 + 0.33) / 3, eps=0.02),
   detail=str([round(x, 4) for x in sh]))

# ── 5. Запуск с нулевым totalActual игнорируется ────────────────────────────
print("[5] запуск с нулевым totalActual игнорируется")
zero = pe.HistoryLaunchInput.from_dict({
    "launchId": 9, "regStart": "2026-01-01", "regEnd": "2026-01-03",
    "eventDate": "2026-01-03", "totalActual": 0,
    "dailyActual": [{"date": "2026-01-01", "count": 999}], "channels": [],
})
base = pe.build_plan_curve([hist[0]], ["a", "b", "c"])
withzero = pe.build_plan_curve([zero, hist[0]], ["a", "b", "c"])
ok("zero-fact не влияет на результат",
   all(approx(a, b) for a, b in zip(base, withzero)),
   detail=f"{[round(x,4) for x in base]} vs {[round(x,4) for x in withzero]}")

# ── 6. Неотрицательность и сумма 1 на разных длинах ─────────────────────────
print("[6] инварианты на длинах 1..7")
for n in range(1, 8):
    sh = pe.build_plan_curve(hist3, ["x"] * n)
    common_invariants(f"len{n}", sh, n)

# ── 7. dailyActual не покрывает все дни окна -> пропуски = 0 ─────────────────
print("[7] неполный dailyActual (пропуски = 0)")
sparse = pe.HistoryLaunchInput.from_dict({
    "launchId": 5, "regStart": "2026-01-01", "regEnd": "2026-01-03",
    "eventDate": "2026-01-03", "totalActual": 100,
    "dailyActual": [{"date": "2026-01-01", "count": 40},
                    {"date": "2026-01-03", "count": 60}],  # день 2 отсутствует
    "channels": [],
})
sh = pe.build_plan_curve([sparse], ["a", "b", "c"])
common_invariants("sparse", sh, 3)
ok("sparse: день2 доля == 0", approx(sh[1], 0.0), detail=str([round(x, 4) for x in sh]))

# ── итог ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 46)
print(f"ИТОГ: {_PASS} PASS, {_FAIL} FAIL")
print("=" * 46)
sys.exit(1 if _FAIL else 0)
