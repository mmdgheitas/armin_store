"""ابزارهای پول: ذخیره عدد صحیح، نمایش با واحد پول قابل تنظیم.

مبالغ در دیتابیس همیشه به صورت Integer و بر مبنای «تومان» ذخیره می‌شوند.
تبدیل واحد (مثلاً ریال = تومان × ۱۰) فقط در لایه نمایش انجام می‌شود.
"""
from __future__ import annotations

import re

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_EN_DIGITS = "0123456789"

UNIT_MULTIPLIERS = {
    "toman": 1,
    "rial": 10,
}


def to_persian_digits(value) -> str:
    return str(value).translate({ord(e): ord(p) for e, p in zip(_EN_DIGITS, PERSIAN_DIGITS)})


def group_digits(value: int) -> str:
    try:
        return f"{int(value):,}".replace(",", "٬")
    except (TypeError, ValueError):
        return "0"


def format_number(value, persian: bool = True) -> str:
    text = group_digits(int(value or 0))
    return to_persian_digits(text) if persian else text.replace("٬", ",")


def to_display_amount(amount: int, unit: str = "toman", custom_ratio: int = 1) -> int:
    """تبدیل مبلغ ذخیره‌شده (تومان) به واحد نمایش."""
    unit = (unit or "toman").lower()
    if unit == "rial":
        return int(amount) * 10
    if unit == "custom":
        try:
            ratio = int(custom_ratio) or 1
        except (TypeError, ValueError):
            ratio = 1
        return int(amount) * ratio
    return int(amount)


def format_money(
    amount: int | None,
    unit: str = "toman",
    label: str = "تومان",
    persian: bool = True,
    custom_ratio: int = 1,
    with_label: bool = True,
) -> str:
    """قالب‌بندی مبلغ برای نمایش: «۱۲٬۵۰۰ تومان»."""
    value = to_display_amount(int(amount or 0), unit, custom_ratio)
    text = format_number(value, persian=persian)
    if with_label and label:
        return f"{text} {label}"
    return text


def parse_money_input(value: str | None) -> int | None:
    """تبدیل ورودی کاربر («۱٬۲۵۰٬۰۰۰ تومان») به عدد صحیح."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).translate({ord(p): ord(e) for p, e in zip(PERSIAN_DIGITS, _EN_DIGITS)})
    text = text.replace("٬", "").replace(",", "").replace(" ", "")
    text = re.sub(r"[^\d\-]", "", text)
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def percent_of(amount: int, percent) -> int:
    """درصد از مبلغ با محاسبه صحیح (بدون Float)."""
    try:
        pct_bp = int(round(float(percent) * 100))  # basis points
    except (TypeError, ValueError):
        return 0
    return int(amount) * pct_bp // 10000


def discount_percent(price: int, compare_at: int | None) -> int:
    if not compare_at or compare_at <= price or compare_at <= 0:
        return 0
    return int((compare_at - price) * 100 // compare_at)


def apply_coupon(discount_type: str, discount_value: int, subtotal: int, maximum_discount: int = 0) -> int:
    """محاسبه تخفیف کوپن (عددی صحیح، بدون سرریز منفی)."""
    if subtotal <= 0:
        return 0
    if discount_type == "percent":
        discount = subtotal * int(discount_value) // 100
    else:
        discount = int(discount_value)
    if maximum_discount and maximum_discount > 0:
        discount = min(discount, int(maximum_discount))
    return max(0, min(discount, subtotal))
