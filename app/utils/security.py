"""ابزارهای امنیتی: هش رمز، توکن، نرمال‌سازی موبایل، پاک‌سازی HTML."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from html import escape

from werkzeug.security import check_password_hash, generate_password_hash

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_STD_DIGITS = "0123456789"

_MOBILE_RE = re.compile(r"^09\d{9}$")
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\u0600-\u06FF\-]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def to_english_digits(value: str) -> str:
    """تبدیل ارقام فارسی/عربی به لاتین (برای ورودی‌های کاربر)."""
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", str(value))
    table = {ord(p): o for p, o in zip(PERSIAN_DIGITS, _STD_DIGITS)}
    table.update({ord(a): o for a, o in zip(ARABIC_DIGITS, _STD_DIGITS)})
    return value.translate(table)


def normalize_mobile(mobile: str | None) -> str:
    """نرمال‌سازی شماره موبایل ایران به شکل 09XXXXXXXXX."""
    if not mobile:
        return ""
    value = to_english_digits(mobile)
    value = re.sub(r"[\s\-()+]", "", value)
    if value.startswith("0098"):
        value = "0" + value[4:]
    elif value.startswith("98") and len(value) == 12:
        value = "0" + value[2:]
    elif value.startswith("9") and len(value) == 10:
        value = "0" + value
    return value


def is_valid_mobile(mobile: str | None) -> bool:
    return bool(_MOBILE_RE.match(normalize_mobile(mobile)))


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256:600000")


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash or not password:
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str, min_length: int = 8) -> list[str]:
    """بازگرداندن فهرست خطاهای رمز عبور (خالی یعنی معتبر)."""
    errors: list[str] = []
    if len(password or "") < min_length:
        errors.append(f"رمز عبور باید حداقل {min_length} کاراکتر باشد.")
    if not re.search(r"[A-Za-z\u0600-\u06FF]", password or ""):
        errors.append("رمز عبور باید شامل حرف باشد.")
    if not re.search(r"\d", password or ""):
        errors.append("رمز عبور باید شامل عدد باشد.")
    if password and password.lower() in {"12345678", "password", "admin123", "qwerty123"}:
        errors.append("این رمز عبور بسیار ساده و قابل حدس است.")
    return errors


def generate_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)[:length]


def generate_numeric_code(length: int = 6) -> str:
    """کد عددی امن (بدون modulo bias)."""
    return "".join(secrets.choice(_STD_DIGITS) for _ in range(length))


def hash_token(token: str, salt: str = "") -> str:
    return hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a or ""), str(b or ""))


def generate_order_number() -> str:
    """شماره سفارش یکتا و غیرقابل حدس."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"AR-{body[:4]}-{body[4:]}"


def generate_download_token() -> str:
    return secrets.token_urlsafe(32)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def slugify(value: str, max_length: int = 80) -> str:
    """ساخت Slug با پشتیبانی از حروف فارسی."""
    value = to_english_digits(value or "").strip().lower()
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "-")
    value = re.sub(r"\s+", "-", value)
    value = _SLUG_STRIP_RE.sub("-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:max_length].strip("-") or secrets.token_hex(4)


def strip_tags(value: str | None) -> str:
    return _HTML_TAG_RE.sub("", value or "").strip()


def sanitize_html(value: str | None) -> str:
    """
    پاک‌سازی سبک HTML محتوای مدیریتی (حذف script/iframe/on*/javascript:).
    محتوای بلاگ و صفحات ثابت فقط توسط مدیر ثبت می‌شود، اما دفاع در عمق انجام می‌گیرد.
    """
    if not value:
        return ""
    cleaned = value
    for pattern in (
        r"(?is)<\s*(script|iframe|object|embed|form|meta|link)[^>]*>.*?<\s*/\s*\1\s*>",
        r"(?is)<\s*(script|iframe|object|embed|form|meta|link)[^>]*/?>",
    ):
        cleaned = re.sub(pattern, "", cleaned)
    cleaned = re.sub(r"(?i)\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", cleaned)
    cleaned = re.sub(r"(?i)javascript\s*:", "", cleaned)
    return cleaned.strip()


def mask_secret(value: str | None, visible: int = 4) -> str:
    """پوشاندن بخش عمده کلید محرمانه برای نمایش در پنل."""
    if not value:
        return ""
    value = str(value)
    if len(value) <= visible:
        return "•" * len(value)
    return value[:visible] + "•" * min(12, len(value) - visible)


def escape_text(value: str | None) -> str:
    return escape(value or "", quote=True)


def truncate_words(value: str | None, length: int = 140) -> str:
    text = strip_tags(value)
    if len(text) <= length:
        return text
    return text[: length - 1].rstrip() + "…"


def persian_search_normalize(value: str | None) -> str:
    """یکسان‌سازی حروف عربی/فارسی برای جستجو."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    return (
        value.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("ۀ", "ه")
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("\u200c", " ")
        .strip()
    )
