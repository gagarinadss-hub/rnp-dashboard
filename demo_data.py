"""Returns realistic mock data so the dashboard works before Google API is set up."""
from datetime import date, timedelta, datetime
import random

random.seed(42)

CHANNELS = [
    ("Email",                               600,  "Сергей"),
    ("ТГ Боты Лайк",                        40,   "Сергей"),
    ("ВК (посты+рассылки)",                 40,   "Сергей"),
    ("ТГ Каналы Лайка + Платформа",         60,   "Сергей"),
    ("Боты ИИ",                             40,   "Даша"),
    ("ТГ канал прошлые мероприятия",        250,  "Даша"),
    ("Студенты",                            40,   "Боровик"),
    ("Кураторы",                            10,   "Боровик"),
    ("Геткурс",                             120,  ""),
    ("ТГ Боты Димы",                        900,  "Даша"),
    ("ТГ Канал НБ",                         300,  "Даша"),
    ("ТГ Канал Димы",                       70,   "Даша"),
    ("Рефка",                               400,  "Иван"),
    ("ОП",                                  100,  "Лиза"),
    ("Тг-бот с выдачей ЛМ (НейроБаза) (рассылка)", 500, "Евгений"),
    ("Инстаграм Димы",                      100,  "Саша О"),
    ("Ватсап (Бондарь и все остальное)",    70,   "Саша Б"),
    ("без метки",                           100,  ""),
    ("Прочее",                              35,   ""),
    ("Бот Саши О",                          50,   "Саша О"),
]

DAY_PCTS = [0.066, 0.167, 0.197, 0.190, 0.212, 0.167, 0.001]

START = date(2026, 5, 22)
TODAY = date(2026, 5, 23)
TOTAL_DAYS = 7
TOTAL_PLAN = sum(p for _, p, _ in CHANNELS)  # 4710


def _daily_actuals():
    elapsed = (TODAY - START).days + 1
    actuals = []
    for i in range(TOTAL_DAYS):
        if i < elapsed:
            base = int(TOTAL_PLAN * DAY_PCTS[i] * random.uniform(0.85, 1.35))
            actuals.append(base)
        else:
            actuals.append(0)
    return actuals


def get_demo_data() -> dict:
    daily_acts = _daily_actuals()
    day_dates = [str(START + timedelta(days=i)) for i in range(TOTAL_DAYS)]
    daily_plan = [int(TOTAL_PLAN * p) for p in DAY_PCTS]

    elapsed = (TODAY - START).days + 1
    total_actual = sum(daily_acts)
    completion_pct = round(total_actual / TOTAL_PLAN * 100, 1)

    cum_so_far = sum(DAY_PCTS[:elapsed])
    projected = int(total_actual / cum_so_far) if cum_so_far else TOTAL_PLAN
    proj_pcts = [int(projected * p) for p in DAY_PCTS]

    def cumul(lst):
        r, s = [], 0
        for v in lst:
            s += v
            r.append(s)
        return r

    # Channel actuals (scaled by their plan share)
    channels = []
    for name, plan, resp in CHANNELS:
        share = plan / TOTAL_PLAN
        actual = int(total_actual * share * random.uniform(0.5, 1.6))
        actual = min(actual, plan + 50)
        pct = round(actual / plan * 100, 1) if plan else 0
        channels.append({"name": name, "plan": plan, "actual": actual, "pct": pct, "responsible": resp})
    channels.sort(key=lambda x: x["plan"], reverse=True)

    confidence_map = {1: "низкая", 2: "средняя"}
    confidence = confidence_map.get(elapsed, "высокая")

    return {
        "overview": {
            "launch_name": "ДЕМО — Вайбкодинг 2.0",
            "start_date": str(START),
            "end_date": str(START + timedelta(days=TOTAL_DAYS - 1)),
            "total_plan": TOTAL_PLAN,
            "total_actual": total_actual,
            "completion_pct": completion_pct,
            "days_elapsed": elapsed,
            "days_total": TOTAL_DAYS,
            "days_remaining": TOTAL_DAYS - elapsed,
            "last_updated": datetime.now().isoformat(),
        },
        "daily": {
            "dates": day_dates,
            "daily_actual": daily_acts,
            "daily_plan": daily_plan,
            "cumulative_actual": cumul(daily_acts),
            "cumulative_plan": cumul(daily_plan),
        },
        "channels": channels,
        "forecast": {
            "projected_total": projected,
            "projected_pct": round(projected / TOTAL_PLAN * 100, 1),
            "confidence": confidence,
            "daily_forecast": proj_pcts,
            "cumulative_forecast": cumul(proj_pcts),
            "cumulative_plan": cumul(daily_plan),
        },
    }
