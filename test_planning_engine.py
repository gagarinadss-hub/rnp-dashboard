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

# ── 8. allocate_integer_plan ────────────────────────────────────────────────
print("[8] allocate_integer_plan")
alloc = pe.allocate_integer_plan

# total 0
r = alloc(0, [0.5, 0.3, 0.2])
ok("total 0 -> все нули", r == [0, 0, 0], str(r))

# total меньше числа дней
r = alloc(2, [0.5, 0.3, 0.2])
ok("total 2 на 3 дня: сумма == 2", sum(r) == 2, str(r))
ok("total 2 на 3 дня: длина 3, без отриц.", len(r) == 3 and all(x >= 0 for x in r), str(r))

# total 1496 на 3 дня по кривой 0.4667/0.4667/0.0667
r = alloc(1496, [0.4667, 0.4667, 0.0667])
ok("1496 на 3 дня: сумма == 1496", sum(r) == 1496, str(r))
ok("1496 на 3 дня: ≈ [698,698,100]", r == [698, 698, 100], str(r))

# пустые shares
ok("пустые shares -> []", alloc(100, []) == [])

# доли с суммой != 1 (ненормированные)
r = alloc(100, [2, 1, 1])
ok("ненормированные доли: сумма == 100", sum(r) == 100, str(r))
ok("ненормированные [2,1,1] -> [50,25,25]", r == [50, 25, 25], str(r))

# вырожденные доли (все 0) -> равномерно
r = alloc(10, [0, 0, 0, 0])
ok("нулевые доли: сумма == 10, равномерно", sum(r) == 10 and max(r) - min(r) <= 1, str(r))

# инвариант: сумма всегда == total, без отрицательных (перебор)
bad = []
for total in [0, 1, 2, 7, 13, 100, 1496, 9999]:
    for shares in ([0.4667, 0.4667, 0.0667], [0.1, 0.2, 0.3, 0.4],
                   [1.0], [0.33, 0.33, 0.34], [0.5, 0.5]):
        rr = pe.allocate_integer_plan(total, shares)
        if sum(rr) != total or len(rr) != len(shares) or any(x < 0 for x in rr):
            bad.append((total, shares, rr))
ok("перебор: сумма==total, длина, без отриц.", not bad, f"провалов: {len(bad)} {bad[:2]}")

# ── 9. generate_daily_plan ──────────────────────────────────────────────────
print("[9] generate_daily_plan")
launch = pe.LaunchInput.from_dict({
    "id": 21, "regStart": "2026-06-02", "regEnd": "2026-06-04",
    "eventDate": "2026-06-04", "totalPlan": 1496,
})
chans = [pe.ChannelPlanInput(channel_id=11, plan_total=1000),
         pe.ChannelPlanInput(channel_id=12, plan_total=496)]
rows = pe.generate_daily_plan(launch, chans, hist3)

# даты соответствуют окну регистрации
got_dates = sorted({r.date for r in rows})
ok("даты == окну [02,03,04 июня]",
   got_dates == ["2026-06-02", "2026-06-03", "2026-06-04"], str(got_dates))
ok("строк == каналы×дни (2×3=6)", len(rows) == 6, str(len(rows)))
ok("dayIndex с 1 по 3", sorted({r.day_index for r in rows}) == [1, 2, 3])

# сумма по каждому каналу == planTotal
def chan_sum(cid):
    return sum(r.plan_count for r in rows if r.channel_id == cid)
ok("канал 11: сумма == 1000", chan_sum(11) == 1000, str(chan_sum(11)))
ok("канал 12: сумма == 496", chan_sum(12) == 496, str(chan_sum(12)))

# сумма всех строк == total_plan (т.к. планы каналов в сумме == total_plan)
ok("сумма всех строк == total_plan 1496",
   sum(r.plan_count for r in rows) == 1496, str(sum(r.plan_count for r in rows)))

# planShare одинаков для всех каналов в один день
by_day_share = {}
share_ok = True
for r in rows:
    if r.day_index in by_day_share and not approx(by_day_share[r.day_index], r.plan_share):
        share_ok = False
    by_day_share[r.day_index] = r.plan_share
ok("planShare одинаков по каналам в одном дне", share_ok)
ok("curve_source == 'history'", all(r.curve_source == "history" for r in rows))

# канал с planTotal 0 -> нули по дням
rows0 = pe.generate_daily_plan(launch, [pe.ChannelPlanInput(channel_id=99, plan_total=0)], hist3)
ok("канал planTotal 0 -> все нули", all(r.plan_count == 0 for r in rows0) and len(rows0) == 3, str([r.plan_count for r in rows0]))

# разные длины запуска (5 дней)
launch5 = pe.LaunchInput.from_dict({
    "id": 30, "regStart": "2026-07-01", "regEnd": "2026-07-05",
    "eventDate": "2026-07-05", "totalPlan": 100,
})
rows5 = pe.generate_daily_plan(launch5, [pe.ChannelPlanInput(channel_id=1, plan_total=100)], hist3)
ok("5-дневное окно: 5 строк", len({r.date for r in rows5}) == 5 and len(rows5) == 5, str(len(rows5)))
ok("5-дневное окно: сумма == 100", sum(r.plan_count for r in rows5) == 100, str(sum(r.plan_count for r in rows5)))

# пустой/некорректный launch -> []
empty_launch = pe.LaunchInput(id=1, reg_start=None, reg_end=None, event_date=None, total_plan=0)
ok("некорректное окно -> []", pe.generate_daily_plan(empty_launch, chans, hist3) == [])

# ── итог ────────────────────────────────────────────────────────────────────
print("\n" + "=" * 46)
print(f"ИТОГ: {_PASS} PASS, {_FAIL} FAIL")
print("=" * 46)
sys.exit(1 if _FAIL else 0)
