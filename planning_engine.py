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
from typing import Optional


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
def build_plan_curve(history_launches: list[HistoryLaunchInput],
                     target_dates: list[str],
                     options: Optional[PlanOptions] = None) -> list[float]:
    """Доли регистраций по дням длиной len(target_dates), сумма == 1.0.
    Реализация — Задача 2.2."""
    raise NotImplementedError("build_plan_curve: реализуется в задаче 2.2")


def allocate_integer_plan(total: int, shares: list[float]) -> list[int]:
    """Раскладывает целое total по долям shares в целые числа без потери суммы.
    Реализация — Задача 2.3."""
    raise NotImplementedError("allocate_integer_plan: реализуется в задаче 2.3")


def generate_daily_plan(launch: LaunchInput,
                        channel_plans: list[ChannelPlanInput],
                        history_launches: list[HistoryLaunchInput],
                        options: Optional[PlanOptions] = None) -> list[DailyPlanOutput]:
    """Генерирует дневной план по каналам. Реализация — Задача 2.4."""
    raise NotImplementedError("generate_daily_plan: реализуется в задаче 2.4")
