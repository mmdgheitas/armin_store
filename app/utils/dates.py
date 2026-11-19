"""ابزارهای تاریخ و زمان (UTC در دیتابیس، نمایش به وقت تهران و تقویم جمالی)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30), name="Asia/Tehran")

PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
PERSIAN_WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def utcnow() -> datetime:
    """زمان فعلی UTC به صورت naive (برای ذخیره یکنواخت در SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def as_tehran(dt) -> datetime | None:
    """تبدیل زمان naive-UTC ذخیره‌شده به وقت تهران (تاریخ بدون ساعت نیز پذیرفته می‌شود)."""
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime(dt.year, dt.month, dt.day)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TEHRAN_TZ)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """تبدیل تاریخ میلادی به جلالی (الگوریتم استاندارد)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
        + g_d_m[gm - 1]
    )
    jy = -1595 + (33 * (days // 12053))
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + (days // 31)
        jd = 1 + (days % 31)
    else:
        jm = 7 + ((days - 186) // 30)
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """تبدیل تاریخ جلالی به میلادی."""
    jy += 1595
    days = -355668 + (365 * jy) + ((jy // 33) * 8) + (((jy % 33) + 3) // 4) + jd
    days += (jm - 1) * 31 if jm < 7 else ((jm - 7) * 30) + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)
    sal_a = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > sal_a[gm]:
        gd -= sal_a[gm]
        gm += 1
    return gy, gm, gd


def jalali_parts(dt: datetime | None) -> tuple[int, int, int] | None:
    if dt is None:
        return None
    local = as_tehran(dt)
    return gregorian_to_jalali(local.year, local.month, local.day)


def jalali_date_string(dt: datetime | None) -> str:
    """تاریخ جلالی به شکل ۱۴۰۴/۰۵/۱۲ (بدون تبدیل ارقام)."""
    parts = jalali_parts(dt)
    if not parts:
        return ""
    return f"{parts[0]:04d}/{parts[1]:02d}/{parts[2]:02d}"


def jalali_datetime_string(dt: datetime | None, with_seconds: bool = False) -> str:
    if dt is None:
        return ""
    local = as_tehran(dt)
    base = jalali_date_string(dt)
    if with_seconds:
        return f"{base} - {local.hour:02d}:{local.minute:02d}:{local.second:02d}"
    return f"{base} - {local.hour:02d}:{local.minute:02d}"


def jalali_long_datetime(dt: datetime | None) -> str:
    parts = jalali_parts(dt)
    if not parts:
        return ""
    local = as_tehran(dt)
    jy, jm, jd = parts
    weekday = PERSIAN_WEEKDAYS[local.weekday()]
    return f"{weekday} {jd} {PERSIAN_MONTHS[jm - 1]} {jy} - {local.hour:02d}:{local.minute:02d}"


def start_of_day(dt: datetime | None = None, tz_offset_minutes: int = 210) -> datetime:
    """ابتدای روز به وقت تهران، بازگشتی به صورت UTC naive."""
    local = as_tehran(dt or utcnow())
    local_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_utc(local_start)


def end_of_day(dt: datetime | None = None) -> datetime:
    return start_of_day(dt) + timedelta(days=1)


def parse_date(value: str | None) -> date | None:
    """پذیرش تاریخ‌های ISO میلادی و جلالی (۱۴۰۴-۰۵-۱۲)."""
    if not value:
        return None
    value = value.strip().replace("/", "-")
    parts = value.split("-")
    if len(parts) != 3:
        return None
    try:
        a, b, c = (int(p) for p in parts)
    except ValueError:
        return None
    if a > 1700:  # میلادی
        try:
            return date(a, b, c)
        except ValueError:
            return None
    if 1200 < a < 1600:  # جلالی
        gy, gm, gd = jalali_to_gregorian(a, b, c)
        try:
            return date(gy, gm, gd)
        except ValueError:
            return None
    return None


def humanize_delta(dt: datetime | None) -> str:
    if dt is None:
        return ""
    delta = utcnow() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "لحظه‌ای پیش"
    if seconds < 3600:
        return f"{seconds // 60} دقیقه پیش"
    if seconds < 86400:
        return f"{seconds // 3600} ساعت پیش"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} روز پیش"
    return jalali_date_string(dt)


def add_days(dt: datetime | None, days: int) -> datetime | None:
    return None if dt is None else dt + timedelta(days=days)
