"""
utm_resolve.py — резолв канала по UTM (Задача 4.1). Чистая логика, без БД.

resolve_channel_by_utm(mappings, utm_source, utm_medium, platform) -> channel_id | None

Приоритет:
  1. точное совпадение (utm_source, utm_medium, platform);
  2. platform-agnostic (utm_source, utm_medium, '') — только если нет неоднозначности.
Неоднозначность (несколько РАЗНЫХ каналов на одном уровне) -> None.
Значения сравниваются нормализованными (lowercase + канон платформы),
поэтому старые маппинги ('MAX','ТГ') совпадают с новыми ('max','tg').
"""
from __future__ import annotations

from sheet_normalize import _norm_platform


def resolve_channel_by_utm(mappings, utm_source, utm_medium, platform):
    """mappings: iterable dict-ов {utm_source, utm_medium, platform, channel_id}."""
    s = (utm_source or "").strip().lower()
    m = (utm_medium or "").strip().lower()
    p = _norm_platform(platform) or ""

    exact = set()      # каналы с совпавшей платформой
    agnostic = set()   # каналы с platform='' (любая платформа)

    for mp in mappings:
        cid = mp.get("channel_id")
        if cid is None:
            continue
        ms = (mp.get("utm_source") or "").strip().lower()
        mm = (mp.get("utm_medium") or "").strip().lower()
        if ms != s or mm != m:
            continue
        mp_plat = _norm_platform(mp.get("platform")) or ""
        if mp_plat == "":
            agnostic.add(cid)
        elif mp_plat == p and p != "":
            exact.add(cid)

    # 1. точный уровень
    if len(exact) == 1:
        return next(iter(exact))
    if len(exact) > 1:
        return None  # конфликт — не угадываем
    # 2. platform-agnostic
    if len(agnostic) == 1:
        return next(iter(agnostic))
    return None  # 0 совпадений или неоднозначность
