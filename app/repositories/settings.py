from __future__ import annotations

import sqlite3
from typing import Any

from app.utils.persian import now_utc_iso

DEFAULT_SETTINGS: dict[str, str] = {
    "site_name": "فروشگاه آنلاین آرمین",
    "site_tagline": "برنج ایرانی، حبوبات، ادویه و چای اصیل مستقیم از مزرعه",
    "site_logo_path": "",
    "site_favicon_path": "",
    "contact_phone": "021-88990011",
    "contact_mobile": "09123456789",
    "contact_email": "info@arminstore.ir",
    "contact_address": "تهران، خیابان ولیعصر، پلاک ۱۲۰",
    "support_hours": "شنبه تا پنج‌شنبه ۹ تا ۱۸",
    "instagram_url": "https://instagram.com/arminstore",
    "telegram_url": "https://t.me/arminstore",
    "whatsapp_url": "",
    "default_meta_title": "فروشگاه آرمین | خرید برنج ایرانی، حبوبات، ادویه و چای تازه",
    "default_meta_description": "خرید اینترنتی برنج طارم و هاشمی، حبوبات دست‌چین، ادویه‌جات تازه و چای اصیل ایرانی با ضمانت کیفیت و ارسال سریع.",
    "analytics_id": "",
    "robots_txt_extra": "",
    "trust_badge_html": '<div class="trust-badge-box"><span>نماد اعتماد و پرداخت امن بانکی</span></div>',
    "order_number_prefix": "ARM",
    "low_stock_default_threshold": "5",
    "reservation_timeout_minutes": "20",
    "shipping_enabled": "1",
    "shipping_fee_rial": "650000",
    "free_shipping_threshold_rial": "15000000",
    "payment_bitpay_enabled": "1",
    "payment_cod_enabled": "0",
    "admin_alert_mobiles": "09120000000",
    "auto_approve_verified_reviews": "0",
    # Bitpay settings (DB overrides or falls back to .env)
    "bitpay_api_key": "",
    "bitpay_test_mode": "1",
    # SMS.ir settings
    "smsir_api_key": "",
    "smsir_line_number": "",
    "smsir_otp_template_id": "100000",
    "smsir_order_paid_template_id": "100001",
    "smsir_order_status_template_id": "100002",
    "smsir_admin_order_template_id": "100003",
    "smsir_low_stock_template_id": "100004",
    "smsir_back_in_stock_template_id": "100005",
    "sms_event_order_paid_customer": "1",
    "sms_event_order_status_customer": "1",
    "sms_event_new_order_admin": "1",
    "sms_event_low_stock_admin": "1",
    "sms_event_back_in_stock": "1",
}


def get_all_settings(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = dict(DEFAULT_SETTINGS)
    for r in rows:
        result[r["key"]] = r["value"]
    return result


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key, "")


def get_int_setting(conn: sqlite3.Connection, key: str, default: int = 0) -> int:
    raw = get_setting(conn, key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def get_bool_setting(conn: sqlite3.Connection, key: str, default: bool = False) -> bool:
    raw = get_setting(conn, key, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def set_settings(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    ts = now_utc_iso()
    for k, v in values.items():
        val_str = "" if v is None else str(v)
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (k, val_str, ts),
        )
