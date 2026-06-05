#!/usr/bin/env python3
"""Тесты sheet_normalize. Запуск: python3 test_sheet_normalize.py"""
import sys
from sheet_normalize import normalize_sheet_row, build_registration_row_hash, DEFAULT_COLUMNS

_PASS = _FAIL = 0


def ok(label, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {label}")
    else:
        _FAIL += 1
        print(f"  ❌ {label}" + (f"  — {detail}" if detail else ""))


def make_row(ext="", dt="", trigger="", src="", med="", platform=""):
    """Строка по DEFAULT_COLUMNS (нужно покрыть индекс 17 -> длина 18)."""
    row = [""] * 18
    row[0] = ext
    row[3] = dt
    row[7] = trigger
    row[8] = src
    row[9] = med
    row[17] = platform
    return row


# 1. пробелы и регистр UTM
print("[1] пробелы и регистр UTM")
n = normalize_sheet_row(make_row(src="  TgC  ", med="OldMero "))
ok("utm_source '  TgC  ' -> 'tgc'", n["utm_source"] == "tgc", n["utm_source"])
ok("utm_medium 'OldMero ' -> 'oldmero'", n["utm_medium"] == "oldmero", n["utm_medium"])

# 2. пустые UTM -> None
print("[2] пустые UTM -> None")
n = normalize_sheet_row(make_row(src="", med="   "))
ok("пустой utm_source -> None", n["utm_source"] is None, str(n["utm_source"]))
ok("пробельный utm_medium -> None", n["utm_medium"] is None, str(n["utm_medium"]))

# 3. дата: валидная
print("[3] парс даты")
n = normalize_sheet_row(make_row(dt="02.06.2026 9:08"))
ok("registered_at ISO", n["registered_at"] == "2026-06-02T09:08:00", n["registered_at"])
ok("registration_date '2026-06-02'", n["registration_date"] == "2026-06-02", n["registration_date"])
n2 = normalize_sheet_row(make_row(dt="2026-06-02"))
ok("ISO-дата без времени", n2["registration_date"] == "2026-06-02", n2["registration_date"])

# 4. дата: битая -> None
print("[4] битая дата -> None")
n = normalize_sheet_row(make_row(dt="не дата"))
ok("registered_at None", n["registered_at"] is None)
ok("registration_date None", n["registration_date"] is None)

# 5. платформа: рус/англ -> канон
print("[5] платформа рус/англ")
cases = {"ТГ": "tg", "tg": "tg", "Telegram": "tg", "MAX": "max", "мах": "max",
         "лендинг": "landing", "ВК": "vk", "": None, "  ": None, "Неизвестно": "неизвестно"}
for raw, exp in cases.items():
    n = normalize_sheet_row(make_row(platform=raw))
    ok(f"platform {raw!r} -> {exp!r}", n["platform"] == exp, str(n["platform"]))

# 6. external_row_id
print("[6] external_row_id")
ok("'123' -> '123'", normalize_sheet_row(make_row(ext="123"))["external_row_id"] == "123")
ok("'' -> None", normalize_sheet_row(make_row(ext=""))["external_row_id"] is None)
ok("'  7  ' -> '7'", normalize_sheet_row(make_row(ext="  7  "))["external_row_id"] == "7")

# 7. raw_payload сохраняет исходную строку
print("[7] raw_payload")
row = make_row(ext="1", dt="02.06.2026 9:08", src="tgc")
n = normalize_sheet_row(row)
ok("raw_payload == исходная строка", n["raw_payload"] == list(row))

# 8. trigger нормализуется
print("[8] trigger")
ok("trigger 'REF' -> 'ref'", normalize_sheet_row(make_row(trigger="REF"))["trigger"] == "ref")

# 9. словарь-строка с произвольной раскладкой колонок
print("[9] произвольная раскладка (dict-row)")
n = normalize_sheet_row({"d": "02.06.2026", "s": "EVB", "p": "MAX"},
                        columns={"registered_at": "d", "utm_source": "s", "platform": "p"})
ok("dict-row: дата+utm+platform", n["registration_date"] == "2026-06-02" and n["utm_source"] == "evb" and n["platform"] == "max",
   str((n["registration_date"], n["utm_source"], n["platform"])))

# ── row_hash ────────────────────────────────────────────────────────────────
H = build_registration_row_hash

print("[10] row_hash: одинаковая строка -> одинаковый хеш")
r1 = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="tgc", med="nb", platform="ТГ"))
r2 = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="tgc", med="nb", platform="ТГ"))
ok("идентичные строки -> равный хеш", H(r1) == H(r2), f"{H(r1)[:8]} vs {H(r2)[:8]}")

print("[11] row_hash: пробелы/регистр не влияют (после нормализации)")
a = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="  TGC ", med="NB", platform="тг"))
b = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="tgc", med="nb", platform="ТГ"))
ok("пробелы/регистр UTM не меняют хеш", H(a) == H(b), f"{H(a)[:8]} vs {H(b)[:8]}")

print("[12] row_hash: разные строки -> разные хеши")
base = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="tgc", med="nb", platform="ТГ"))
diff_user = normalize_sheet_row(make_row(ext="101", dt="02.06.2026 9:08", src="tgc", med="nb", platform="ТГ"))
diff_time = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:09", src="tgc", med="nb", platform="ТГ"))
diff_plat = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="tgc", med="nb", platform="лендинг"))
diff_src  = normalize_sheet_row(make_row(ext="100", dt="02.06.2026 9:08", src="tgb", med="nb", platform="ТГ"))
ok("другой User ID -> другой хеш", H(base) != H(diff_user))
ok("другое время -> другой хеш", H(base) != H(diff_time))
ok("другая платформа -> другой хеш (касания различимы)", H(base) != H(diff_plat))
ok("другой utm_source -> другой хеш", H(base) != H(diff_src))

print("[13] row_hash: launch_id различает запуски, стабильность пустых полей")
ok("разный launch_id -> разный хеш", H(base, launch_id=1) != H(base, launch_id=2))
empty = normalize_sheet_row(make_row())
ok("пустая строка -> стабильный хеш", H(empty) == H(empty) and len(H(empty)) == 64)

print("\n" + "=" * 44)
print(f"ИТОГ: {_PASS} PASS, {_FAIL} FAIL")
print("=" * 44)
sys.exit(1 if _FAIL else 0)
