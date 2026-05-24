import os
import re
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

KNOWN_CHANNELS = [
    "Email", "ТГ Боты Лайк", "ВК (посты+рассылки)", "ТГ Каналы Лайка + Платформа",
    "Боты ИИ", "ТГ канал прошлые мероприятия", "Студенты", "Кураторы", "Геткурс",
    "ТГ Боты Димы", "ТГ Канал НБ", "ТГ Канал Димы", "Рефка", "ОП",
    "Тг-бот с выдачей ЛМ (НейроБаза) (рассылка)", "Инстаграм Димы",
    "Ватсап (Бондарь и все остальное)", "Продуктовые каналы УБ",
    "без метки", "Прочее", "Екатерина Суханова ТГ", "Ютуб", "ВК Дима",
    "Суханова ВК", "МАХ Дима", "Инст Суханова", "ВК сообщество Суханова",
    "ВК канал Суханова", "МАХ Суханова на ВК", "ТГ-посевы (Дмитрий)",
    "Выступления", "Бот Саши О",
]
KNOWN_LOWER = {c.lower(): c for c in KNOWN_CHANNELS}

# Fallback historical day distribution (if sheet parse fails)
DEFAULT_DAY_PCTS = [0.066, 0.167, 0.197, 0.190, 0.212, 0.167, 0.001]


def _num(val) -> float:
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("%", "").replace(",", ".").replace("\xa0", "").strip()
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _pct(val) -> float:
    n = _num(val)
    return n / 100.0 if n > 1.5 else n


def _cumulative(values: List[int]) -> List[int]:
    result, running = [], 0
    for v in values:
        running += v
        result.append(running)
    return result


class DataProcessor:
    def __init__(
        self,
        registrations: List[Dict],
        rnp_raw: List[List],
        historical_raw: List[List],
    ):
        self.registrations = registrations
        self.rnp_raw = rnp_raw
        self.historical_raw = historical_raw
        self.day_pcts = self._parse_day_pcts()

    # ── Historical percentages ──────────────────────────────────────────────

    def _parse_day_pcts(self) -> List[float]:
        if not self.historical_raw:
            return DEFAULT_DAY_PCTS

        pct_pattern = re.compile(r"^\d{1,2}[,\.]\d+%?$")
        candidates = []

        for row in self.historical_raw:
            cells = [str(c).strip() for c in row[1:9]]
            matches = [c for c in cells if pct_pattern.match(c)]
            if len(matches) >= 4:
                values = []
                for c in cells:
                    if "%" in c:
                        v = _num(c) / 100  # explicit percent: always divide
                    else:
                        v = _num(c)
                        if v > 1:
                            v /= 100
                    values.append(v)
                if 0.5 < sum(values) < 1.5:
                    candidates.append(values)

        if candidates:
            vals = candidates[-1]
            # Strip trailing zeros from empty cells (row[1:9] always has 8 slots)
            while len(vals) > 1 and vals[-1] == 0.0:
                vals = vals[:-1]
            return vals
        return DEFAULT_DAY_PCTS

    # ── RNP sheet parsing ───────────────────────────────────────────────────

    def _parse_rnp(self) -> tuple[Dict, List[Dict], Optional[date], Optional[date], str]:
        overview = {}
        channels = {}
        start_date = end_date = None
        launch_name = os.getenv("LAUNCH_NAME", "Текущий запуск")
        date_re = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})\b")

        dates_found: List[date] = []

        for row_idx, row in enumerate(self.rnp_raw):
            if not row:
                continue
            row_str = " ".join(str(c) for c in row)

            # Extract dates from the first 12 rows (headers)
            if row_idx < 12:
                for day_s, mon_s in date_re.findall(row_str):
                    try:
                        d = date(2026, int(mon_s), int(day_s))
                        dates_found.append(d)
                    except ValueError:
                        pass

            # Overall totals row
            if ("ОБЩИЕ" in row_str or "общие" in row_str.lower()) and not overview:
                nums = [_num(c) for c in row if _num(c) > 100]
                if len(nums) >= 2:
                    overview = {
                        "total_plan": int(nums[0]),
                        "total_actual": int(nums[1]),
                    }

            # Channel rows
            name_raw = str(row[0]).strip() if row else ""
            if not name_raw:
                continue

            canonical = KNOWN_LOWER.get(name_raw.lower())
            # RNP column layout: 0=Канал 1=#REF! 2=База 3=+/- 4=План
            #                    5=Прогноз 6=Прогноз(ф) 7=Откл 8=Прогноз 9=Факт 10=% 11=Отв
            plan_val = _num(row[4]) if len(row) > 4 else 0
            fact_val = _num(row[9]) if len(row) > 9 else 0

            if canonical and (plan_val > 0 or fact_val > 0):
                responsible = str(row[11]).strip() if len(row) > 11 else ""
                channels[canonical] = {
                    "name": canonical,
                    "plan": int(plan_val),
                    "actual": int(fact_val),
                    "pct": round(fact_val / plan_val * 100, 1) if plan_val > 0 else 0,
                    "responsible": responsible,
                }

        # Remove outlier dates (e.g. old launch names like "15/09" in RNP header)
        if len(dates_found) >= 2:
            sorted_dates = sorted(dates_found)
            median_date = sorted_dates[len(sorted_dates) // 2]
            dates_found = [d for d in dates_found if abs((d - median_date).days) <= 14]

        if dates_found:
            dates_found = sorted(set(dates_found))
            start_date, end_date = dates_found[0], dates_found[-1]

        return overview, list(channels.values()), start_date, end_date, launch_name

    # ── Base sheet aggregation ──────────────────────────────────────────────

    def _find_col(self, df: pd.DataFrame, keywords: List[str]) -> Optional[str]:
        # Exact match first (e.g. "КАНАЛ"), then substring
        for kw in keywords:
            for col in df.columns:
                if col.strip().upper() == kw.upper():
                    return col
        for kw in keywords:
            for col in df.columns:
                if kw.lower() in col.lower():
                    return col
        return None

    def _filter_launch(self, df: pd.DataFrame, date_col: str, start: date, end: date) -> pd.DataFrame:
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.date
        df = df.dropna(subset=["_date"])
        # Filter out Excel/Sheets default epoch dates
        df = df[df["_date"] >= date(2020, 1, 1)]
        return df[(df["_date"] >= start) & (df["_date"] <= end)]

    def _daily_actuals(self, start: date, end: date) -> Dict[str, int]:
        if not self.registrations:
            return {}
        df = pd.DataFrame(self.registrations)
        date_col = self._find_col(df, ["дата входа", "дата"])
        if not date_col:
            return {}
        df = self._filter_launch(df, date_col, start, end)
        return {str(d): int(cnt) for d, cnt in df.groupby("_date").size().items()}

    def _channel_actuals(self, start: date, end: date) -> Dict[str, int]:
        if not self.registrations:
            return {}
        df = pd.DataFrame(self.registrations)
        ch_col = self._find_col(df, ["КАНАЛ", "Клик-канал", "канал"])
        if not ch_col:
            return {}
        date_col = self._find_col(df, ["дата входа", "дата"])
        if date_col:
            df = self._filter_launch(df, date_col, start, end)
        return {str(k): int(v) for k, v in df[ch_col].value_counts().items()}

    # ── Forecast ───────────────────────────────────────────────────────────

    def _forecast(self, total_actual: int, day_num: int, total_plan: int,
                  daily_acts: List[int]) -> Dict:
        pcts = self.day_pcts
        days = len(pcts)
        day_num = max(1, min(day_num, days))

        # Skip anomalous days (< 15% of expected) to avoid skewing the projection.
        # E.g. a "soft start" day 1 with near-zero regs would otherwise drag the whole forecast down.
        good_idxs = []
        for i in range(day_num):
            expected = total_plan * pcts[i] if pcts[i] > 0 else 1
            actual_i = daily_acts[i] if i < len(daily_acts) else 0
            if actual_i >= expected * 0.15:
                good_idxs.append(i)

        if good_idxs:
            cum_actual = sum(daily_acts[i] if i < len(daily_acts) else 0 for i in good_idxs)
            cum_pct = sum(pcts[i] for i in good_idxs)
            projected = int(cum_actual / cum_pct) if cum_pct > 0 else total_plan
        else:
            cum_so_far = sum(pcts[:day_num])
            projected = int(total_actual / cum_so_far) if cum_so_far > 0 else total_plan

        daily_fcast = [int(projected * p) for p in pcts]
        cum_fcast = _cumulative(daily_fcast)
        cum_plan = _cumulative([int(total_plan * p) for p in pcts])

        n_good = len(good_idxs)
        confidence = "низкая" if n_good <= 1 else ("средняя" if n_good <= 2 else "высокая")

        return {
            "projected_total": projected,
            "projected_pct": round(projected / total_plan * 100, 1) if total_plan > 0 else 0,
            "confidence": confidence,
            "daily_forecast": daily_fcast,
            "cumulative_forecast": cum_fcast,
            "cumulative_plan": cum_plan,
        }

    # ── Main entry point ───────────────────────────────────────────────────

    def _detect_launch_period_from_regs(self) -> tuple[Optional[date], Optional[date]]:
        """Find launch start from registrations (last 30 days), end = start + 6."""
        if not self.registrations:
            return None, None
        df = pd.DataFrame(self.registrations)
        date_col = self._find_col(df, ["дата входа", "дата"])
        if not date_col:
            return None, None
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dt.date
        df = df.dropna(subset=["_date"])
        df = df[df["_date"] >= date(2020, 1, 1)]
        cutoff = date.today() - timedelta(days=30)
        recent_dates = df[df["_date"] >= cutoff]["_date"].unique()
        if len(recent_dates) == 0:
            return None, None
        start = min(recent_dates)
        end = start + timedelta(days=len(self.day_pcts) - 1)
        return start, end

    def get_dashboard_data(self) -> Dict:
        overview, rnp_channels, rnp_start, rnp_end, launch_name = self._parse_rnp()

        today = date.today()

        # Derive dates from registrations (most reliable), fall back to RNP header
        start_date, end_date = self._detect_launch_period_from_regs()
        if not start_date:
            start_date = rnp_start
        if not end_date:
            end_date = rnp_end

        # Final fallback: current week
        if not start_date:
            start_date = today - timedelta(days=today.weekday())
        if not end_date:
            end_date = start_date + timedelta(days=6)

        days_elapsed = max(1, (today - start_date).days + 1)
        total_days = max(1, (end_date - start_date).days + 1)
        days_remaining = max(0, (end_date - today).days)

        # Actuals from База
        ch_actuals = self._channel_actuals(start_date, end_date)
        daily_acts = self._daily_actuals(start_date, end_date)

        # Merge channel data
        ch_map = {c["name"]: c for c in rnp_channels}
        # Only include channels that have a plan (from RNP) or are in the known list;
        # this prevents unrecognized UTM values from ch_actuals inflating the total.
        known_names = set(KNOWN_LOWER.values())
        all_names = set(ch_map) | (set(ch_actuals) & known_names)
        channels = []
        for name in all_names:
            rnp = ch_map.get(name, {})
            actual = ch_actuals.get(name, rnp.get("actual", 0))
            plan = rnp.get("plan", 0)
            pct = round(actual / plan * 100, 1) if plan > 0 else 0
            channels.append({
                "name": name,
                "plan": plan,
                "actual": actual,
                "pct": pct,
                "responsible": rnp.get("responsible", ""),
            })
        channels.sort(key=lambda x: x["plan"], reverse=True)

        total_plan = overview.get("total_plan") or sum(c["plan"] for c in channels) or 4710
        total_actual = sum(c["actual"] for c in channels)
        if total_actual == 0:
            total_actual = overview.get("total_actual", 0)
        completion_pct = round(total_actual / total_plan * 100, 1) if total_plan > 0 else 0

        # Daily timeline
        day_dates = [str(start_date + timedelta(days=i)) for i in range(total_days)]
        n_pcts = min(total_days, len(self.day_pcts))
        daily_plan_map = {
            str(start_date + timedelta(days=i)): int(total_plan * self.day_pcts[i])
            for i in range(n_pcts)
        }
        daily_actual_list = [daily_acts.get(d, 0) for d in day_dates]
        daily_plan_list   = [daily_plan_map.get(d, 0) for d in day_dates]

        # Today stats
        today_str = str(today)
        today_actual = daily_acts.get(today_str, 0)
        today_day_idx = (today - start_date).days
        today_plan = int(total_plan * self.day_pcts[today_day_idx]) if 0 <= today_day_idx < len(self.day_pcts) else 0
        today_pct = round(today_actual / today_plan * 100, 1) if today_plan > 0 else 0

        forecast = self._forecast(total_actual, days_elapsed, total_plan, daily_actual_list)

        # Per-channel daily breakdown
        total_actual_safe = total_actual or 1
        for ch in channels:
            ch_plan = ch["plan"]
            ch_actual = ch["actual"]
            # Plan per day: channel_plan × day distribution
            ch_daily_plan = [int(ch_plan * p) for p in self.day_pcts]
            # Estimated actual per day: proportional to total daily actuals
            ch_daily_actual = [
                int(daily_actual_list[i] * ch_actual / total_actual_safe)
                if i < len(daily_actual_list) else 0
                for i in range(len(self.day_pcts))
            ]
            # Per-channel forecast
            ch_proj_pct = sum(self.day_pcts[:days_elapsed])
            ch_proj = int(ch_actual / ch_proj_pct) if ch_proj_pct > 0 and ch_actual > 0 else 0
            ch["daily_plan"]   = ch_daily_plan
            ch["daily_actual"] = ch_daily_actual
            ch["forecast"]     = ch_proj
            ch["forecast_pct"] = round(ch_proj / ch_plan * 100, 1) if ch_plan > 0 else 0

        # Best and lagging channels
        active_chs = [c for c in channels if c["plan"] > 0 and c["actual"] > 0]
        best_channels = sorted(active_chs, key=lambda x: x["pct"], reverse=True)[:3]
        lag_channels  = sorted(
            [c for c in channels if c["plan"] > 50 and c["actual"] < c["plan"]],
            key=lambda x: x["pct"]
        )[:3]

        return {
            "overview": {
                "launch_name": launch_name,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "total_plan": total_plan,
                "total_actual": total_actual,
                "completion_pct": completion_pct,
                "days_elapsed": days_elapsed,
                "days_total": total_days,
                "days_remaining": days_remaining,
                "today_actual": today_actual,
                "today_plan": today_plan,
                "today_pct": today_pct,
                "last_updated": datetime.now().isoformat(),
            },
            "daily": {
                "dates": day_dates,
                "daily_actual": daily_actual_list,
                "daily_plan": daily_plan_list,
                "cumulative_actual": _cumulative(daily_actual_list),
                "cumulative_plan": _cumulative(daily_plan_list),
            },
            "channels": channels,
            "forecast": forecast,
            "best_channels": [{"name": c["name"], "pct": c["pct"], "actual": c["actual"]} for c in best_channels],
            "lag_channels":  [{"name": c["name"], "pct": c["pct"], "plan": c["plan"], "actual": c["actual"]} for c in lag_channels],
        }
