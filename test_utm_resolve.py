#!/usr/bin/env python3
"""Тесты utm_resolve. Запуск: python3 test_utm_resolve.py"""
import sys
from utm_resolve import resolve_channel_by_utm as R

_PASS = _FAIL = 0


def ok(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {label}")
    else:
        _FAIL += 1
        print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))


def mp(s, m, p, cid):
    return {"utm_source": s, "utm_medium": m, "platform": p, "channel_id": cid}


# 1. точное совпадение
print("[1] точное совпадение (src,med,platform)")
maps = [mp("tgc", "nb", "tg", 11), mp("tgc", "nb", "max", 22)]
ok("tgc/nb/tg -> 11", R(maps, "tgc", "nb", "tg") == 11, str(R(maps, "tgc", "nb", "tg")))
ok("tgc/nb/MAX -> 22 (норм платформы)", R(maps, "tgc", "nb", "MAX") == 22, str(R(maps, "tgc", "nb", "MAX")))

# 2. platform-agnostic fallback
print("[2] platform-agnostic fallback")
maps = [mp("tgc", "oldmero", "", 5)]
ok("tgc/oldmero/любая -> 5", R(maps, "tgc", "oldmero", "tg") == 5)
ok("tgc/oldmero/MAX -> 5 (agnostic)", R(maps, "tgc", "oldmero", "MAX") == 5)
ok("tgc/oldmero/без платформы -> 5", R(maps, "tgc", "oldmero", "") == 5)

# 3. точное бьёт agnostic
print("[3] точное бьёт agnostic")
maps = [mp("tgc", "nb", "", 5), mp("tgc", "nb", "max", 22)]
ok("tgc/nb/MAX -> 22 (точное)", R(maps, "tgc", "nb", "MAX") == 22)
ok("tgc/nb/tg -> 5 (нет точного tg -> agnostic)", R(maps, "tgc", "nb", "tg") == 5)

# 4. неизвестная UTM -> None
print("[4] неизвестная UTM")
ok("нет в маппингах -> None", R(maps, "zzz", "qqq", "tg") is None)
ok("пустые маппинги -> None", R([], "tgc", "nb", "tg") is None)

# 5. неоднозначность -> None
print("[5] неоднозначность -> None")
amb_ag = [mp("x", "y", "", 1), mp("x", "y", "", 2)]
ok("два канала agnostic -> None", R(amb_ag, "x", "y", "tg") is None)
amb_ex = [mp("x", "y", "tg", 1), mp("x", "y", "tg", 2)]
ok("два канала на точном уровне -> None", R(amb_ex, "x", "y", "tg") is None)

# 6. пустая платформа в запросе -> только agnostic
print("[6] пустая платформа в запросе")
maps = [mp("a", "b", "tg", 7)]
ok("a/b/'' при только platform-specific -> None", R(maps, "a", "b", "") is None)
maps2 = [mp("a", "b", "tg", 7), mp("a", "b", "", 8)]
ok("a/b/'' -> 8 (agnostic)", R(maps2, "a", "b", "") == 8)

# 7. нормализация значений запроса
print("[7] нормализация запроса")
maps = [mp("tgc", "nb", "tg", 11)]
ok("'  TGC ' / 'NB' / 'ТГ' -> 11", R(maps, "  TGC ", "NB", "ТГ") == 11)

print("\n" + "=" * 44)
print(f"ИТОГ: {_PASS} PASS, {_FAIL} FAIL")
print("=" * 44)
sys.exit(1 if _FAIL else 0)
