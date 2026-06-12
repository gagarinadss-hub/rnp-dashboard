"""
sheets_importer.py
Периодически читает регистрации из Google Таблицы запуска
и записывает их в SQLite daily_registrations.
"""
import gspread
import sqlite3
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent

# ── Spreadsheet IDs ────────────────────────────────────────────────────────
SHEET_ID_REGS    = '10674VDmFpo1s_NU02H0ScJ1BqYyWBwlAGh4OMBOZMHA'  # новая таблица с рег.
SHEET_ID_REF_BOOK = '1bDbcketkM9SC5FY0rMHvwy8bUaf4Op3hPxFQJHBKH9E'  # Справочник

MAIN_SHEET_NAME = 'ЗАПУСК 16-17 ИЮНЯ'
REF_SHEET_NAME  = 'РЕФЕРАЛКА 16-17 ИЮНЯ'  # реферальный лист (формат как у основного)

# ── Hardcoded extras (подтверждены пользователем) ─────────────────────────
_EXTRA = {
    'evb':      'Тг-бот с выдачей ЛМ (НейроБаза) (рассылка)',
    'ecspert':  'Екатерина Суханова ТГ',
    'curators': 'Кураторы',
    'students': 'Студенты',
    'gk':       'Геткурс',
    'gc':       'Геткурс',              # GetCourse: в данных метка 'gc', не 'gk'
    'tgp':      'ТГ-посевы (Дмитрий)',  # ТГ-посевы: ссылки poreg_tgp_bars_* через MAX-бот
}

# ── State ──────────────────────────────────────────────────────────────────
_last_import: datetime | None = None
_last_total:  int = 0


def _build_mapping(gc: gspread.Client) -> dict:
    """(utm_source.lower(), utm_medium.lower()) -> channel_name из Справочника."""
    sh  = gc.open_by_key(SHEET_ID_REF_BOOK)
    ws  = sh.worksheet('Справочник')
    rows = ws.get_all_values()
    mapping = {}
    for r in rows[2:]:
        channel = r[0].strip() if len(r) > 0 else ''
        src     = r[3].strip().lower() if len(r) > 3 else ''
        med     = r[4].strip().lower() if len(r) > 4 else ''
        if channel and src:
            key = (src, med)
            if key not in mapping:
                mapping[key] = channel
    return mapping


def _load_db_mappings() -> dict:
    """Load user-defined label mappings from SQLite.
    Returns dict keyed by (src, med, platform) → channel_name.
    Platform='' means "any platform" (fallback).
    """
    try:
        from db import DB_PATH as _db_path_obj
        _db_path = str(_db_path_obj)
    except Exception:
        _db_path = str(BASE_DIR / 'launches.db')
    try:
        con = sqlite3.connect(_db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT utm_source, utm_medium, platform, channel_name FROM label_mappings").fetchall()
        con.close()
        return {
            (r['utm_source'].lower(), r['utm_medium'].lower(), r['platform'].upper()):
            r['channel_name']
            for r in rows
        }
    except Exception as e:
        log.warning(f'[importer] не удалось загрузить пользовательские маппинги: {e}')
        return {}


# Platform → канал-суффикс для фолбэков
_PLATFORM_SUFFIX = {'MAX': ' МАХ', 'ТГ': ''}

def _resolve(src: str, med: str, trigger: str, platform: str, mapping: dict, db_mapping: dict) -> str:
    """Resolve (utm_source, utm_medium, platform) → channel_name.

    Priority:
    1. User DB mapping: exact (src, med, PLATFORM)
    2. User DB mapping: (src, med, '') — platform-agnostic
    3. Справочник mapping: (src, med)
    4. Справочник mapping: (src, '') — source-only
    5. Hardcoded _EXTRA
    6. Platform-aware fallbacks by source
    7. 'без метки'
    """
    s = src.strip().lower()
    m = med.strip().lower()
    p = platform.strip().upper()   # 'ТГ', 'MAX', ''

    # 1. User DB exact match (src, med, platform)
    if (s, m, p) in db_mapping:
        return db_mapping[(s, m, p)]
    # 2. User DB platform-agnostic
    if (s, m, '') in db_mapping:
        return db_mapping[(s, m, '')]

    # 3. Справочник exact (src, med)
    if (s, m) in mapping:
        ch = mapping[(s, m)]
        # If this source can come from MAX, add suffix
        if p == 'MAX' and s in ('tgc', 'tgb'):
            return _platform_channel(ch, p)
        return ch
    # 4. Справочник source-only
    if (s, '') in mapping:
        ch = mapping[(s, '')]
        if p == 'MAX' and s in ('tgc', 'tgb'):
            return _platform_channel(ch, p)
        return ch

    # 5. Hardcoded extras
    if s in _EXTRA:
        return _EXTRA[s]

    # 6. Platform-aware fallbacks
    #    tgp (ТГ-посевы) обрабатывается выше в _EXTRA — сюда не доходит.
    if s == 'tgb':
        return 'МАХ Дима' if p == 'MAX' else 'ТГ Боты Димы'
    if s == 'tgc':
        return 'МАХ Дима' if p == 'MAX' else 'ТГ Канал Димы'
    if s == 'vk':    return 'ВК (посты+рассылки)'
    if s == 'email': return 'Email'
    if s == 'inst':  return 'Инстаграм Димы'

    # 7. Empty source
    if not s:
        return 'Рефка' if trigger.strip().lower() == 'ref' else 'без метки'
    return 'без метки'


def _platform_channel(channel: str, platform: str) -> str:
    """Map a TG channel name to its MAX equivalent, or return as-is."""
    tg_to_max = {
        'ТГ Канал Димы':  'МАХ Дима',
        'ТГ Боты Димы':   'МАХ Дима',
        'ТГ Канал НБ':    'МАХ НБ',
    }
    if platform == 'MAX':
        return tg_to_max.get(channel, channel)
    return channel


def run_import(launch_id: int) -> dict:
    """
    Читает регистрации из Google Таблицы и перезаписывает daily_registrations
    для указанного launch_id.
    Возвращает словарь с результатами.
    """
    global _last_import, _last_total

    # credentials.json: either next to code or restored from env at startup
    creds_path = str(BASE_DIR / 'credentials.json')
    try:
        gc = gspread.service_account(filename=creds_path)
    except Exception as e:
        log.error(f'[importer] не удалось авторизоваться: {e}')
        return {'error': str(e)}

    # Use the same DB_PATH as db.py (respects DATA_DIR env var on Railway)
    try:
        from db import DB_PATH as _db_path_obj
        db_path = str(_db_path_obj)
    except Exception:
        db_path = str(BASE_DIR / 'launches.db')
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT reg_start, reg_end, event_date, event_end_date FROM launches WHERE id = ?",
        (launch_id,)
    ).fetchone()
    con.close()

    if not row or not row['reg_start']:
        return {'error': f'launch {launch_id} не найден или нет reg_start'}

    reg_start = datetime.fromisoformat(row['reg_start']).date()

    # Окно дат запуска: регистрации засчитываем только если их дата попадает
    # в [reg_start .. последний день события]. Это не даёт занести в запуск
    # чужие данные (напр. майские строки в июньский запуск).
    def _pd(v):
        try:
            return datetime.fromisoformat(v).date() if v else None
        except Exception:
            return None
    _ends = [d for d in (_pd(row['reg_end']), _pd(row['event_date']),
                         _pd(row['event_end_date'])) if d]
    window_end = max(_ends) if _ends else None
    skipped_out_of_window = 0
    dropped_dups = 0   # сколько строк отброшено как дубли (один человек = одна рег.)

    def _day_num(dt):
        """Возвращает day_num (>=1) или None, если дата вне окна запуска."""
        if dt < reg_start:
            return None
        if window_end and dt > window_end:
            return None
        return (dt - reg_start).days + 1

    # Строим маппинг из Справочника
    try:
        mapping = _build_mapping(gc)
    except Exception as e:
        log.warning(f'[importer] Справочник недоступен: {e}. Используем только хардкод.')
        mapping = {}

    # Пользовательские маппинги из БД (высший приоритет, platform-aware)
    try:
        db_mappings = _load_db_mappings()
    except Exception as e:
        log.warning(f'[importer] пользовательские маппинги не загружены: {e}')
        db_mappings = {}

    # Читаем регистрации
    try:
        sh_new = gc.open_by_key(SHEET_ID_REGS)
    except Exception as e:
        log.error(f'[importer] не могу открыть таблицу регистраций: {e}')
        return {'error': str(e)}

    channel_day: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    # Трекаем ВСЕ метки: (src, med, platform) -> {channel, count}
    raw_stats: dict[tuple, dict] = {}
    # Нераспределённые (resolved to 'без метки')
    raw_unmatched: dict[tuple, int] = defaultdict(int)

    # Основной лист
    try:
        ws_main = sh_new.worksheet(MAIN_SHEET_NAME)
        all_main = ws_main.get_all_values()
        hdr = [str(c).strip().lower() for c in all_main[0]] if all_main else []

        # Дедуп: команда считает УНИКАЛЬНЫЕ регистрации (один человек = одна,
        # первое вхождение). Комбинированный ключ User ID + телефон: строка —
        # дубль, если уже встречался ЕЁ User ID ИЛИ ЕЁ телефон. Это ловит и
        # обычные повторы, и кросс-платформенные входы (один человек заходит
        # с разных аккаунтов под одним телефоном).
        def _find_col(header, *names):
            for nm in names:
                if nm in header:
                    return header.index(nm)
            return None
        id_col    = _find_col(hdr, 'sb_id', 'user id', 'tg id', 'tg/vk/max id')
        phone_col = _find_col(hdr, 'телефон', 'phone')
        if id_col is None:
            id_col = 0
        seen_ids:    set[str] = set()
        seen_phones: set[str] = set()

        for r in all_main[1:]:
            if not any(c.strip() for c in r):
                continue
            # Дедуп по первому вхождению (лист отсортирован по дате входа).
            uid   = r[id_col].strip()    if (id_col    is not None and id_col    < len(r)) else ''
            phone = r[phone_col].strip() if (phone_col is not None and phone_col < len(r)) else ''
            if (uid and uid in seen_ids) or (phone and phone in seen_phones):
                dropped_dups += 1
                continue
            if uid:   seen_ids.add(uid)
            if phone: seen_phones.add(phone)
            date_str = r[3].strip()  if len(r) > 3  else ''
            src      = r[8].strip()  if len(r) > 8  else ''
            med      = r[9].strip()  if len(r) > 9  else ''
            trigger  = r[7].strip()  if len(r) > 7  else ''
            platform = r[17].strip() if len(r) > 17 else ''
            try:
                dt = datetime.strptime(date_str[:10], '%d.%m.%Y').date()
            except ValueError:
                continue
            day_num = _day_num(dt)
            if day_num is None:
                skipped_out_of_window += 1
                continue
            ch = _resolve(src, med, trigger, platform, mapping, db_mappings)
            channel_day[ch][day_num] += 1
            key = (src, med, platform)
            if key not in raw_stats:
                raw_stats[key] = {'channel': ch, 'count': 0}
            raw_stats[key]['count'] += 1
            if ch == 'без метки':
                raw_unmatched[key] += 1
    except Exception as e:
        log.error(f'[importer] ошибка чтения основного листа: {e}')
        return {'error': str(e)}

    # Лист рефералок
    try:
        ws_ref = sh_new.worksheet(REF_SHEET_NAME)
        for r in ws_ref.get_all_values()[1:]:
            if not any(c.strip() for c in r):
                continue
            date_str = r[0].strip() if len(r) > 0 else ''
            try:
                dt = datetime.strptime(date_str[:10], '%d.%m.%Y').date()
            except ValueError:
                continue
            day_num = _day_num(dt)
            if day_num is None:
                skipped_out_of_window += 1
                continue
            channel_day['Рефка'][day_num] += 1
    except Exception as e:
        log.warning(f'[importer] лист рефералок не прочитан: {e}')

    # Записываем в БД
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    ch_rows = con.execute("""
        SELECT c.id, c.name
        FROM channels c
        JOIN launch_channels lc ON lc.channel_id = c.id
        WHERE lc.launch_id = ?
    """, (launch_id,)).fetchall()
    ch_id_map = {r['name']: r['id'] for r in ch_rows}

    con.execute("DELETE FROM daily_registrations WHERE launch_id = ?", (launch_id,))

    inserted = 0
    skipped: list[str] = []
    for ch_name, days in channel_day.items():
        ch_id = ch_id_map.get(ch_name)
        if ch_id is None:
            if ch_name not in skipped:
                skipped.append(ch_name)
            continue
        for day_num, count in days.items():
            con.execute("""
                INSERT INTO daily_registrations (launch_id, channel_id, day_num, count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(launch_id, channel_id, day_num)
                DO UPDATE SET count = excluded.count
            """, (launch_id, ch_id, day_num, count))
            inserted += 1

    con.commit()
    con.close()

    total = sum(sum(d.values()) for d in channel_day.values())
    _last_import = datetime.now(timezone.utc)
    _last_total  = total

    # Сохраняем все utm-метки и нераспределённые
    try:
        from db import save_unmatched_labels, save_utm_stats
        utm_stats_list = [
            {'utm_source': s, 'utm_medium': m, 'platform': p,
             'count': v['count'], 'resolved_channel': v['channel']}
            for (s, m, p), v in sorted(raw_stats.items(), key=lambda x: -x[1]['count'])
        ]
        save_utm_stats(launch_id, utm_stats_list)
        unmatched_list = [
            {'utm_source': s, 'utm_medium': m, 'platform': p, 'count': c}
            for (s, m, p), c in sorted(raw_unmatched.items(), key=lambda x: -x[1])
        ]
        save_unmatched_labels(launch_id, unmatched_list)
    except Exception as e:
        log.warning(f'[importer] не удалось сохранить метки: {e}')
        unmatched_list = []
        utm_stats_list = []

    log.info(f'[importer] ✅ launch={launch_id}  всего={total}  записей={inserted}  дублей_отброшено={dropped_dups}  пропущено={skipped}  вне_окна={skipped_out_of_window}  нераспред.меток={len(unmatched_list)}')
    return {
        'launch_id':            launch_id,
        'total_registrations':  total,
        'db_records':           inserted,
        'dropped_duplicates':   dropped_dups,
        'skipped_channels':     skipped,
        'skipped_out_of_window': skipped_out_of_window,
        'unmatched_labels':     len(unmatched_list),
        'imported_at':          _last_import.isoformat(),
    }


def get_status() -> dict:
    return {
        'last_import':  _last_import.isoformat() if _last_import else None,
        'last_total':   _last_total,
    }
