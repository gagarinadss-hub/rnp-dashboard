"""
planning_engine.py — расчётное ядро дневного плана RNP.

Чистый модуль: только stdlib. НЕ импортирует web/router/UI/БД, чтобы его можно
было гонять в unit-тестах без запуска сервера. БД-слой (db.py) сам конвертирует
свои данные в эти структуры и обратно.

Статус: каркас (Задача 2.1). Реализация функций — в задачах 2.2–2.4:
    build_plan_curve()      -> 2.2
    allocate_integer_plan() -> 2.3
    generate_daily_plan()   -> 2.4
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Фолбэк-кривая долей по дням (исторический профиль RNP), если истории нет.
# Та же методология, что GLOBAL_DAY_PCTS в db.py — но без связи с БД.
FALLBACK_DAY_SHARES = [0.066, 0.167, 0.197, 0.190, 0.212, 0.167, 0.001]


# ── Опции расчёта ───────────────────────────────────────────────────────────
@dataclass
class PlanOptions:
    """Параметры построения кривой плана.

    mode:
      - "window_index"    — дни сравниваются как день 1, день 2 … окна
                            регистрации (обязательный режим v1);
      - "event_relative"  — дни сравниваются относительно даты эфира (опция).
    """
    mode: str = "window_index"
    history_limit: int = 10


# ── Входные структуры ───────────────────────────────────────────────────────
@dataclass
class LaunchInput:
    id: Optional[int]
    reg_start: str          # ISO 'YYYY-MM-DD'
    reg_end: str            # ISO 'YYYY-MM-DD'
    event_date: Optional[str]
    total_plan: int

    @classmethod
    def from_dict(cls, d: dict) -> "LaunchInput":
        return cls(
            id=d.get("id") or d.get("launchId"),
            reg_start=d.get("reg_start") or d.get("regStart"),
            reg_end=d.get("reg_end") or d.get("regEnd"),
            event_date=d.get("event_date") or d.get("eventDate"),
            total_plan=int(d.get("total_plan") or d.get("totalPlan") or 0),
        )


@dataclass
class ChannelPlanInput:
    channel_id: Optional[int]
    plan_total: int

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelPlanInput":
        return cls(
            channel_id=d.get("channel_id") or d.get("channelId"),
            plan_total=int(d.get("plan_total") or d.get("planTotal") or d.get("plan") or 0),
        )


@dataclass
class DailyActualPoint:
    date: Optional[str]     # ISO 'YYYY-MM-DD' или None
    count: int

    @classmethod
    def from_dict(cls, d: dict) -> "DailyActualPoint":
        return cls(date=d.get("date"), count=int(d.get("count") or 0))


@dataclass
class HistoryChannelActual:
    channel_id: Optional[int]
    daily_actual: list[DailyActualPoint] = field(default_factory=list)


@dataclass
class HistoryLaunchInput:
    launch_id: int
    reg_start: str
    reg_end: str
    event_date: Optional[str]
    total_actual: int
    daily_actual: list[DailyActualPoint] = field(default_factory=list)
    channels: list[HistoryChannelActual] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryLaunchInput":
        """Принимает выход db.get_history_launches() (camelCase) либо snake_case."""
        daily = d.get("daily_actual") or d.get("dailyActual") or []
        chans = d.get("channels") or []
        return cls(
            launch_id=d.get("launch_id") or d.get("launchId"),
            reg_start=d.get("reg_start") or d.get("regStart"),
            reg_end=d.get("reg_end") or d.get("regEnd"),
            event_date=d.get("event_date") or d.get("eventDate"),
            total_actual=int(d.get("total_actual") or d.get("totalActual") or 0),
            daily_actual=[DailyActualPoint.from_dict(x) for x in daily],
            channels=[
                HistoryChannelActual(
                    channel_id=ch.get("channel_id") or ch.get("channelId"),
                    daily_actual=[DailyActualPoint.from_dict(x)
                                  for x in (ch.get("daily_actual") or ch.get("dailyActual") or [])],
                )
                for ch in chans
            ],
        )


# ── Выходная структура ──────────────────────────────────────────────────────
@dataclass
class DailyPlanOutput:
    launch_id: Optional[int]
    channel_id: Optional[int]
    date: str
    day_index: int          # начинается с 1
    plan_count: int
    plan_share: float
    curve_source: str = ""

    def to_dict(self) -> dict:
        return {
            "launchId": self.launch_id,
            "channelId": self.channel_id,
            "date": self.date,
            "dayIndex": self.day_index,
            "planCount": self.plan_count,
            "planShare": self.plan_share,
            "curveSource": self.curve_source,
        }


# ── Каркас функций (реализация в 2.2–2.4) ───────────────────────────────────
def _parse_date(s):
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _history_window_counts(h: HistoryLaunchInput) -> list[float]:
    """Дневные counts по СОБСТВЕННОМУ окну регистрации запуска (день1..деньN).
    Хвосты вне окна отбрасываются, пропуски = 0. [] если данных нет."""
    pts = h.daily_actual or []
    rs, re = _parse_date(h.reg_start), _parse_date(h.reg_end)
    if rs and re and (re - rs).days >= 0:
        L = (re - rs).days + 1
        counts = [0.0] * L
        for p in pts:
            d = _parse_date(p.date)
            if d and rs <= d <= re:
                counts[(d - rs).days] += max(0, p.count or 0)
    else:
        counts = [max(0, p.count or 0) for p in pts]
    return counts if sum(counts) > 0 else []


def _cumulative_shares(counts: list[float]) -> list[float]:
    """Накопительные доли в конце каждого дня: [0.1, 0.45, ..., 1.0]."""
    total = sum(counts)
    if total <= 0:
        return []
    cum, run = [], 0.0
    for c in counts:
        run += c
        cum.append(run / total)
    return cum  # длина L, cum[-1] == 1.0


def _interp_cum_at(cum: list[float], x: float) -> float:
    """Накопительная доля в позиции x∈[0,1]. Узлы: позиция 0 -> 0,
    позиция k/L -> cum[k-1]. Между узлами — линейная интерполяция."""
    L = len(cum)
    if L == 0 or x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    pos = x * L
    k_left = int(pos)
    frac = pos - k_left
    v_left = 0.0 if k_left == 0 else cum[k_left - 1]
    k_right = k_left + 1
    v_right = cum[k_right - 1] if k_right <= L else 1.0
    return v_left + (v_right - v_left) * frac


def build_plan_curve(history_launches: list[HistoryLaunchInput],
                     target_dates: list[str],
                     options: Optional[PlanOptions] = None) -> list[float]:
    """Доли регистраций по дням длиной len(target_dates), сумма == 1.0.

    Алгоритм (window_index):
      1. По каждому history launch — дневные counts по его окну регистрации.
      2. Перевести в накопительные доли (cum[-1] = 1.0).
      3. Для каждого target-дня j позиция x = j/D.
      4. Интерполировать накопительную долю запуска в этой позиции.
      5. Усреднить накопительные доли всех запусков.
      6. Перевести обратно в дневные доли (разности).
      7. Нормировать сумму к 1.

    Игнорирует запуски с totalActual <= 0 и без полезных данных.
    Если истории нет — fallback-кривая. Доли всегда >= 0, сумма == 1.0.
    """
    D = len(target_dates)
    if D <= 0:
        return []

    curves: list[list[float]] = []
    for h in history_launches:
        if (h.total_actual or 0) <= 0:      # игнорируем нулевой факт
            continue
        counts = _history_window_counts(h)
        if counts:
            curves.append(_cumulative_shares(counts))

    if not curves:                          # fallback
        curves = [_cumulative_shares(FALLBACK_DAY_SHARES)]

    # усреднённая накопительная доля в позициях конца каждого target-дня
    avg_cum = []
    for j in range(1, D + 1):
        x = j / D
        vals = [_interp_cum_at(c, x) for c in curves]
        avg_cum.append(sum(vals) / len(vals))

    # дневные доли = разности накопительных (в позиции 0 накопительная = 0)
    shares, prev = [], 0.0
    for v in avg_cum:
        shares.append(max(0.0, v - prev))
        prev = v

    s = sum(shares)
    if s <= 0:
        return [1.0 / D] * D
    return [x / s for x in shares]


def allocate_integer_plan(total: int, shares: list[float]) -> list[int]:
    """Раскладывает целое total по долям shares в целые числа без потери суммы.

    Метод наибольшего остатка:
      1. raw = total * share / sum(shares).
      2. base = floor(raw).
      3. remainder = total - sum(base).
      4. remainder раздаём дням с самыми большими дробными частями.

    Гарантии: len(result) == len(shares); sum(result) == total ровно;
    все значения >= 0. Граничные: total<=0 -> нули; пустые shares -> [];
    отрицательные доли трактуются как 0; нулевая сумма долей -> равномерно.
    """
    import math
    n = len(shares)
    if n == 0:
        return []
    if total <= 0:
        return [0] * n

    cl = [max(0.0, s) for s in shares]
    s_sum = sum(cl)
    if s_sum <= 0:
        cl = [1.0] * n          # равномерно, если доли вырождены
        s_sum = float(n)

    raw = [total * s / s_sum for s in cl]
    base = [math.floor(r) for r in raw]
    remainder = total - sum(base)   # в диапазоне [0, n)

    # индексы по убыванию дробной части (ties -> по исходному порядку)
    order = sorted(range(n), key=lambda i: raw[i] - base[i], reverse=True)
    for k in range(remainder):
        base[order[k % n]] += 1
    return base


def generate_daily_plan(launch: LaunchInput,
                        channel_plans: list[ChannelPlanInput],
                        history_launches: list[HistoryLaunchInput],
                        options: Optional[PlanOptions] = None) -> list[DailyPlanOutput]:
    """Генерирует дневной план по каналам. Реализация — Задача 2.4."""
    raise NotImplementedError("generate_daily_plan: реализуется в задаче 2.4")
