from __future__ import annotations

import datetime as dt
import hashlib
import re
import secrets
from html import escape
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import jdatetime

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

_EN_TO_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_FA_AR_TO_EN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_persian_digits(value: str | int | float) -> str:
    return str(value).translate(_EN_TO_FA_DIGITS)


def to_english_digits(value: str) -> str:
    return (value or "").translate(_FA_AR_TO_EN_DIGITS)


def normalize_persian(text: str) -> str:
    """Normalize Arabic/Persian variants, digits, ZWNJ, and extra whitespace for search & matching."""
    if not text:
        return ""
    s = to_english_digits(text)
    s = s.replace("ي", "ی").replace("ى", "ی").replace("ئ", "ی")
    s = s.replace("ك", "ک")
    s = s.replace("ة", "ه").replace("ۀ", "ه")
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "آ")
    s = s.replace("\u200c", " ")  # ZWNJ -> space for search matching
    s = re.sub(r"[\u064b-\u065f\u0670]", "", s)  # strip diacritics
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def format_toman(rial: int | None, *, with_unit: bool = True) -> str:
    if rial is None:
        return ""
    toman = int(rial) // 10
    formatted = f"{toman:,}"
    fa = to_persian_digits(formatted)
    return f"{fa} تومان" if with_unit else fa


def format_rial(rial: int | None) -> str:
    if rial is None:
        return ""
    return f"{to_persian_digits(f'{int(rial):,}')} ریال"


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc_iso(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        return None


def to_jalali(ts: str | dt.datetime | None, fmt: str = "%Y/%m/%d - %H:%M") -> str:
    if not ts:
        return "—"
    if isinstance(ts, str):
        parsed = parse_utc_iso(ts)
        if parsed is None:
            return ts
    else:
        parsed = ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    tehran_dt = parsed.astimezone(TEHRAN_TZ)
    jdt = jdatetime.datetime.fromgregorian(datetime=tehran_dt)
    return to_persian_digits(jdt.strftime(fmt))


def to_jalali_date(ts: str | dt.datetime | None) -> str:
    return to_jalali(ts, fmt="%Y/%m/%d")


def jalali_input_to_utc_range(jalali_str: str, *, end_of_day: bool = False) -> str | None:
    """Convert YYYY/MM/DD or YYYY-MM-DD Jalali date string to UTC ISO string."""
    s = to_english_digits((jalali_str or "").strip()).replace("-", "/")
    m = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", s)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if end_of_day:
            jdt = jdatetime.datetime(y, mo, d, 23, 59, 59)
        else:
            jdt = jdatetime.datetime(y, mo, d, 0, 0, 0)
        gdt = jdt.togregorian().replace(tzinfo=TEHRAN_TZ).astimezone(dt.timezone.utc)
        return gdt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except Exception:
        return None


def normalize_iran_mobile(raw: str | None) -> str | None:
    """Normalize Iranian mobile numbers (+989..., 00989..., 989..., 9..., 09...) to 09xxxxxxxxx."""
    if not raw:
        return None
    s = to_english_digits(raw).strip()
    s = re.sub(r"[\s\-().]+", "", s)
    if s.startswith("+98"):
        s = "0" + s[3:]
    elif s.startswith("0098"):
        s = "0" + s[4:]
    elif s.startswith("98") and len(s) == 12:
        s = "0" + s[2:]
    elif s.startswith("9") and len(s) == 10:
        s = "0" + s
    if re.fullmatch(r"09\d{9}", s):
        return s
    return None


def validate_postal_code(raw: str | None) -> str | None:
    if not raw:
        return None
    s = to_english_digits(raw).strip()
    s = re.sub(r"[\s\-]+", "", s)
    if re.fullmatch(r"\d{10}", s) and not s.startswith("0") and len(set(s)) > 1:
        return s
    if re.fullmatch(r"\d{10}", s):
        return s
    return None


def slugify_persian(text: str, max_length: int = 120) -> str:
    """Create a clean URL slug preserving Persian letters, ASCII letters, digits, and hyphens."""
    s = to_english_digits((text or "").strip())
    s = s.replace("ي", "ی").replace("ك", "ک").replace("\u200c", "-")
    s = re.sub(r"[^\w\u0600-\u06FF\s-]+", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-").lower()
    if len(s) > max_length:
        s = s[:max_length].rstrip("-")
    return s or f"item-{secrets.token_hex(3)}"


def generate_order_number(prefix: str = "ARM") -> str:
    now_tehran = dt.datetime.now(TEHRAN_TZ)
    jdt = jdatetime.datetime.fromgregorian(datetime=now_tehran)
    date_part = f"{jdt.year % 100:02d}{jdt.month:02d}{jdt.day:02d}"
    rand_part = "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(5))
    clean_prefix = re.sub(r"[^A-Za-z0-9]", "", prefix or "ARM").upper()[:6] or "ARM"
    return f"{clean_prefix}-{date_part}-{rand_part}"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --- HTML Allow-list Sanitizer ---

_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "a",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
    "div",
}
_VOID_TAGS = {"br"}
_DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "svg"}
_ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


class _AllowListHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth > 0:
            return
        if tag not in _ALLOWED_TAGS:
            return
        allowed = _ALLOWED_ATTRS.get(tag, set())
        clean_attrs: list[str] = []
        for k, v in attrs:
            k_low = k.lower()
            if k_low not in allowed or v is None:
                continue
            val = v.strip()
            if k_low == "href":
                low_val = val.lower()
                if not (
                    low_val.startswith("http://")
                    or low_val.startswith("https://")
                    or low_val.startswith("/")
                ):
                    continue
            if k_low == "target" and val not in {"_blank", "_self"}:
                continue
            clean_attrs.append(f'{k_low}="{escape(val, quote=True)}"')
        if tag == "a" and any(a.startswith('target="_blank"') for a in clean_attrs):
            if not any(a.startswith("rel=") for a in clean_attrs):
                clean_attrs.append('rel="noopener noreferrer"')
        attr_str = (" " + " ".join(clean_attrs)) if clean_attrs else ""
        self.parts.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self._drop_depth > 0:
                self._drop_depth -= 1
            return
        if self._drop_depth > 0 or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._drop_depth > 0:
            return
        self.parts.append(escape(data, quote=False))


def sanitize_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    parser = _AllowListHTMLSanitizer()
    parser.feed(raw_html)
    parser.close()
    return "".join(parser.parts)
