"""
sheet_normalize.py — чистая нормализация строки Google Sheets во внутренний формат.

Без gspread/БД — тестируется без сети. Используется новым импорт-сервисом
(Этап 3) для построчного факта raw_registrations.

normalize_sheet_row(row, columns) -> {
    registered_at, registration_date, utm_source, utm_medium,
    platform, trigger, external_row_id, raw_payload
}
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

# Раскладка колонок текущего листа регистраций ('вайбкодинг 4.06.26' и т.п.).
DEFAULT_COLUMNS = {
    "external_row_id": 0,   # User ID
    "registered_at": 3,     # Дата входа
    "trigger": 7,
    "utm_source": 8,
    "utm_medium": 9,
    "platform": 17,         # Платформа
}

_DATE_FORMATS = [
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%d/%m/%Y %H:%M", "%d/%m/%Y",
]

# Платформа -> стабильный канонический токен (рус/англ синонимы).
PLATFORM_CANON = {
    "тг": "tg", "tg": "tg", "telegram": "tg", "телеграм": "tg", "телеграмм": "tg",
    "max": "max", "мах": "max", "макс": "max",
    "вк": "vk", "vk": "vk", "vkontakte": "vk", "вконтакте": "vk",
    "лендинг": "landing", "лэндинг": "landing", "landing": "landing",
    "ютуб": "youtube", "youtube": "youtube",
    "инст": "instagram", "инстаграм": "instagram", "instagram": "instagram",
    "ватсап": "whatsapp", "whatsapp": "whatsapp", "wa": "whatsapp",
    "email": "email", "почта": "email",
}


def _norm_utm(v) -> Optional[str]:
    """trim + lowercase, пустое -> None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    return s or None


def _norm_platform(v) -> Optional[str]:
    """trim, стабильный канонический вид, пустое -> None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return PLATFORM_CANON.get(s.lower(), s.lower())


def _parse_dt(v) -> Optional[datetime]:
    """Стабильный парс даты/времени. None, если не распарсилось."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _cell(row, idx):
    if idx is None:
        return None
    if isinstance(row, dict):
        return row.get(idx)
    if isinstance(row, (list, tuple)) and idx < len(row):
        return row[idx]
    return None


def normalize_sheet_row(row, columns: Optional[dict] = None) -> dict:
    """Привести строку Google Sheets к внутреннему формату.

    Правила:
      - UTM (source/medium/trigger): trim + lowercase, пустая строка -> None;
      - platform: trim + канонизация (рус/англ), пустая -> None;
      - дата парсится стабильно (несколько форматов), иначе None;
      - external_row_id: trim, пустой -> None;
      - raw_payload: исходная строка целиком.
    """
    cols = columns or DEFAULT_COLUMNS
    dt = _parse_dt(_cell(row, cols.get("registered_at")))
    raw_ext = _cell(row, cols.get("external_row_id"))
    ext = (str(raw_ext).strip() if raw_ext is not None else "") or None

    return {
        "registered_at":     dt.isoformat() if dt else None,
        "registration_date": dt.date().isoformat() if dt else None,
        "utm_source":        _norm_utm(_cell(row, cols.get("utm_source"))),
        "utm_medium":        _norm_utm(_cell(row, cols.get("utm_medium"))),
        "platform":          _norm_platform(_cell(row, cols.get("platform"))),
        "trigger":           _norm_utm(_cell(row, cols.get("trigger"))),
        "external_row_id":   ext,
        "raw_payload":       list(row) if isinstance(row, (list, tuple)) else row,
    }
