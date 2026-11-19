"""
لایه تنظیمات: تنها منبع حقیقت برای پیکربندی قابل تغییر از پنل مدیریت.

مقادیر محرمانه (کلید API) در دیتابیس ذخیره می‌شوند اما:
  * هرگز در لاگ نوشته نمی‌شوند
  * در پنل به صورت ماسک‌شده نمایش داده می‌شوند
  * دیتابیس و پوشه‌های instance/storage با .htaccess از دسترس وب خارج می‌شوند
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flask import g, has_app_context

from app.extensions import db
from app.models.content import Setting
from app.utils.dates import utcnow
from app.utils.money import format_money, format_number


@dataclass(frozen=True)
class SettingDef:
    key: str
    default: Any
    type: str = "str"  # str | int | bool | text | color | select
    group: str = "general"
    label: str = ""
    help: str = ""
    sensitive: bool = False
    options: tuple = ()
    rows: int = 3


GROUPS = {
    "general": "اطلاعات فروشگاه",
    "currency": "واحد پول و نمایش",
    "commerce": "تنظیمات فروش",
    "upload": "آپلود فایل",
    "auth": "ورود و امنیت حساب",
    "payment": "درگاه پرداخت",
    "sms": "پیامک",
    "seo": "سئو",
    "security": "امنیت",
    "backup": "پشتیبان‌گیری",
    "support": "پشتیبانی",
}


def _d(key, default, type="str", group="general", label="", help="", sensitive=False, options=(), rows=3):
    return SettingDef(key, default, type, group, label, help, sensitive, options, rows)


DEFINITIONS: list[SettingDef] = [
    # --- اطلاعات فروشگاه ---
    _d("store_name", "فروشگاه من", label="نام فروشگاه", group="general"),
    _d("store_slogan", "خرید آسان و مطمئن", label="شعار فروشگاه", group="general"),
    _d("store_description", "فروشگاه اینترنتی با ارسال سریع و پشتیبانی واقعی.", type="text", label="توضیح کوتاه فروشگاه", group="general"),
    _d("store_logo", "", label="لوگو (مسیر فایل)", group="general", help="از بخش تنظیمات رسانه یا آپلود لوگو استفاده کنید."),
    _d("store_favicon", "", label="فاوآیکون", group="general"),
    _d("contact_email", "", label="ایمیل پشتیبانی", group="general"),
    _d("contact_phone", "", label="تلفن ثابت", group="general"),
    _d("contact_mobile", "", label="موبایل پشتیبانی", group="general"),
    _d("contact_address", "", type="text", label="آدرس", group="general"),
    _d("work_hours", "شنبه تا پنجشنبه ۹ تا ۱۸", label="ساعات کاری", group="general"),
    _d("social_instagram", "", label="اینستاگرام", group="general"),
    _d("social_telegram", "", label="تلگرام", group="general"),
    _d("social_whatsapp", "", label="واتس‌اپ", group="general"),
    _d("social_twitter", "", label="ایکس (توییتر)", group="general"),
    _d("footer_about", "فروشگاه اینترنتی ما با هدف ارائه محصولات باکیفیت و خدمات پس از فروش واقعی راه‌اندازی شده است.", type="text", label="متن معرفی در فوتر", group="general"),
    _d("maintenance_mode", False, type="bool", label="حالت تعمیرات", group="general", help="در این حالت فروشگاه برای مشتریان بسته است و پنل مدیریت فعال می‌ماند."),
    _d("maintenance_message", "فروشگاه در حال به‌روزرسانی است. لطفاً کمی بعد مراجعه کنید.", type="text", label="پیام حالت تعمیرات", group="general"),

    # --- واحد پول ---
    _d("currency_label", "تومان", label="عنوان واحد پول", group="currency"),
    _d("currency_code", "IRT", label="کد واحد پول", group="currency"),
    _d(
        "currency_unit", "toman", type="select", label="واحد محاسبه نمایش",
        group="currency",
        options=(("toman", "تومان"), ("rial", "ریال"), ("custom", "واحد سفارشی")),
        help="مبالغ در دیتابیس همیشه به تومان (عدد صحیح) ذخیره می‌شوند و تبدیل فقط در نمایش انجام می‌شود.",
    ),
    _d("currency_ratio", 1, type="int", label="ضریب واحد سفارشی", group="currency", help="مثلاً ۱۰ برای نمایش ریال."),
    _d("display_persian_digits", True, type="bool", label="نمایش ارقام فارسی", group="currency"),
    _d("date_calendar", "jalali", type="select", label="تقویم نمایش", group="currency", options=(("jalali", "شمسی"), ("gregorian", "میلادی"))),

    # --- فروش ---
    _d("tax_enabled", False, type="bool", label="فعال‌سازی مالیات", group="commerce"),
    _d("tax_percent", 0, type="int", label="درصد مالیات", group="commerce"),
    _d("reservation_minutes", 20, type="int", label="مدت رزرو موجودی (دقیقه)", group="commerce"),
    _d("low_stock_threshold", 3, type="int", label="آستانه هشدار کمبود موجودی", group="commerce"),
    _d("products_per_page", 12, type="int", label="تعداد محصول در هر صفحه", group="commerce"),
    _d("orders_per_page", 20, type="int", label="تعداد سفارش در هر صفحه پنل", group="commerce"),
    _d("related_products_count", 4, type="int", label="تعداد محصولات مرتبط", group="commerce"),
    _d("review_require_purchase", False, type="bool", label="نظر فقط برای خریداران", group="commerce"),
    _d("review_auto_approve", False, type="bool", label="انتشار خودکار نظرات", group="commerce"),
    _d("comparison_limit", 4, type="int", label="حداکثر محصول در مقایسه", group="commerce"),
    _d("wishlist_enabled", True, type="bool", label="فعال بودن علاقه‌مندی‌ها", group="commerce"),
    _d("customer_video_enabled", True, type="bool", label="اجازه ارسال ویدئو توسط مشتریان", group="commerce"),
    _d("guest_checkout_enabled", True, type="bool", label="خرید مهمان", group="commerce"),
    _d("min_order_amount", 0, type="int", label="حداقل مبلغ سفارش (تومان)", group="commerce"),
    _d("show_stock_count", True, type="bool", label="نمایش تعداد موجودی به مشتری", group="commerce"),

    # --- آپلود ---
    _d("image_max_mb", 5, type="int", label="حداکثر حجم تصویر (مگابایت)", group="upload"),
    _d("video_max_mb", 50, type="int", label="حداکثر حجم ویدئوی محصول (مگابایت)", group="upload"),
    _d("customer_video_max_mb", 60, type="int", label="حداکثر حجم ویدئوی مشتری (مگابایت)", group="upload"),
    _d("ticket_attachment_max_mb", 5, type="int", label="حداکثر حجم پیوست تیکت (مگابایت)", group="upload"),
    _d("image_max_dimension", 1600, type="int", label="حداکثر بعد تصویر", group="upload"),
    _d("optimize_images", True, type="bool", label="بهینه‌سازی خودکار تصاویر", group="upload"),
    _d("uploads_enabled", True, type="bool", label="فعال بودن آپلود فایل", group="upload"),

    # --- احراز هویت ---
    _d("otp_length", 6, type="int", label="طول کد یکبارمصرف", group="auth"),
    _d("otp_ttl_minutes", 5, type="int", label="اعتبار کد (دقیقه)", group="auth"),
    _d("otp_resend_seconds", 90, type="int", label="فاصله مجاز ارسال مجدد (ثانیه)", group="auth"),
    _d("otp_max_per_hour", 6, type="int", label="حداکثر ارسال کد در ساعت", group="auth"),
    _d("otp_max_attempts", 5, type="int", label="حداکثر تلاش برای هر کد", group="auth"),
    _d("login_max_attempts", 5, type="int", label="حداکثر تلاش ورود ناموفق", group="auth"),
    _d("login_lock_minutes", 15, type="int", label="مدت قفل حساب پس از تلاش ناموفق", group="auth"),
    _d("password_min_length", 8, type="int", label="حداقل طول رمز عبور", group="auth"),
    _d("session_lifetime_hours", 168, type="int", label="مدت اعتبار نشست ورود (ساعت)", group="auth"),
    _d("allow_registration", True, type="bool", label="اجازه ثبت‌نام جدید", group="auth"),
    _d("otp_required_for_orders", False, type="bool", label="الزام تأیید موبایل برای پیگیری سفارش", group="auth"),

    # --- درگاه پرداخت ---
    _d("payment_online_enabled", True, type="bool", label="فعال بودن پرداخت آنلاین", group="payment"),
    _d("bitpay_enabled", False, type="bool", label="فعال بودن درگاه BitPay", group="payment"),
    _d("bitpay_api_key", "", type="str", label="کلید API درگاه", group="payment", sensitive=True,
       help="کلید در پنل درگاه (BitPay) دریافت می‌شود. مقدار قبلی ماسک می‌شود."),
    _d("bitpay_base_url", "https://bitpay.ir", label="آدرس پایه درگاه", group="payment"),
    _d("bitpay_token_path", "/payment/gateway-{api}-get-token", label="مسیر دریافت توکن", group="payment",
       help="مطابق مستندات رسمی درگاه. {api} با کلید API جایگزین می‌شود."),
    _d("bitpay_verify_path", "/payment/verify-{api}/{trans_id}/{id_get}", label="مسیر تأیید تراکنش", group="payment",
       help="مطابق مستندات رسمی درگاه."),
    _d("bitpay_gateway_url", "https://bitpay.ir/payment/gateway-{token}", label="آدرس صفحه پرداخت", group="payment"),
    _d("bitpay_amount_multiplier", 10, type="int", label="ضریب تبدیل مبلغ برای درگاه", group="payment",
       help="اگر مبالغ فروشگاه به تومان است و درگاه ریالی است، مقدار ۱۰ مناسب است."),
    _d("bitpay_test_mode", False, type="bool", label="حالت آزمایشی درگاه داخلی", group="payment",
       help="برای تست فرآیند بدون درگاه واقعی. در این حالت پرداخت واقعی انجام نمی‌شود و به کاربر اعلام می‌گردد."),
    _d("payment_timeout_minutes", 30, type="int", label="مهلت تکمیل پرداخت (دقیقه)", group="payment"),
    _d("payment_success_note", "پرداخت شما با موفقیت ثبت شد.", type="text", label="پیام موفقیت پرداخت", group="payment"),

    # --- پیامک ---
    _d("sms_enabled", False, type="bool", label="فعال بودن سامانه پیامک", group="sms"),
    _d("sms_api_key", "", label="کلید API پیامک", group="sms", sensitive=True),
    _d("sms_base_url", "https://api.sms.ir", label="آدرس پایه سرویس پیامک", group="sms"),
    _d("sms_verify_path", "/v1/send/verify", label="مسیر ارسال الگو", group="sms"),
    _d("sms_bulk_path", "/v1/send/bulk", label="مسیر ارسال انبوه", group="sms"),
    _d("sms_line_number", "", label="شماره خط", group="sms"),
    _d("sms_pattern_otp", "", label="کد الگوی OTP", group="sms"),
    _d("sms_pattern_order_created", "", label="کد الگوی ثبت سفارش", group="sms"),
    _d("sms_pattern_payment_success", "", label="کد الگوی پرداخت موفق", group="sms"),
    _d("sms_pattern_order_processing", "", label="کد الگوی در حال پردازش", group="sms"),
    _d("sms_pattern_order_ready", "", label="کد الگوی آماده ارسال", group="sms"),
    _d("sms_pattern_order_shipped", "", label="کد الگوی ارسال سفارش", group="sms"),
    _d("sms_pattern_order_delivered", "", label="کد الگوی تحویل سفارش", group="sms"),
    _d("sms_pattern_order_cancelled", "", label="کد الگوی لغو سفارش", group="sms"),
    _d("sms_pattern_ticket_reply", "", label="کد الگوی پاسخ تیکت", group="sms"),
    _d("sms_notify_order_created", True, type="bool", label="ارسال پیامک ثبت سفارش", group="sms"),
    _d("sms_notify_payment_success", True, type="bool", label="ارسال پیامک پرداخت موفق", group="sms"),
    _d("sms_notify_order_processing", False, type="bool", label="ارسال پیامک در حال پردازش", group="sms"),
    _d("sms_notify_order_ready", False, type="bool", label="ارسال پیامک آماده ارسال", group="sms"),
    _d("sms_notify_order_shipped", True, type="bool", label="ارسال پیامک ارسال سفارش", group="sms"),
    _d("sms_notify_order_delivered", True, type="bool", label="ارسال پیامک تحویل سفارش", group="sms"),
    _d("sms_notify_order_cancelled", True, type="bool", label="ارسال پیامک لغو سفارش", group="sms"),
    _d("sms_notify_ticket_reply", True, type="bool", label="ارسال پیامک پاسخ تیکت", group="sms"),

    # --- سئو ---
    _d("seo_default_title", "", label="عنوان پیش‌فرض سئو", group="seo"),
    _d("seo_default_description", "", type="text", label="توضیح پیش‌فرض سئو", group="seo"),
    _d("seo_og_image", "", label="تصویر اشتراک‌گذاری", group="seo"),
    _d("sitemap_enabled", True, type="bool", label="فعال بودن Sitemap", group="seo"),
    _d("robots_txt", "User-agent: *\nDisallow: /admin/\nDisallow: /account/\nDisallow: /cart/\nDisallow: /checkout/\nDisallow: /payment/\nDisallow: /download/\nAllow: /", type="text", label="محتوای robots.txt", group="seo", rows=6),
    _d("google_analytics_id", "", label="شناسه Google Analytics", group="seo"),
    _d("structured_data_enabled", True, type="bool", label="داده ساختاریافته (Schema.org)", group="seo"),

    # --- امنیت ---
    _d("force_https", True, type="bool", label="هدایت اجباری به HTTPS", group="security"),
    _d("trust_proxy_headers", True, type="bool", label="اعتماد به هدرهای پروکسی (cPanel)", group="security"),
    _d("max_login_attempts_per_ip", 20, type="int", label="حداکثر تلاش ورود از هر IP در ساعت", group="security"),

    # --- بکاپ ---
    _d("backup_keep_count", 10, type="int", label="تعداد بکاپ نگهداری‌شده", group="backup"),
    _d("backup_auto_note", "", label="یادداشت پیش‌فرض بکاپ", group="backup"),

    # --- پشتیبانی ---
    _d("support_enabled", True, type="bool", label="فعال بودن تیکت پشتیبانی", group="support"),
    _d("support_welcome_text", "پرسش خود را ثبت کنید؛ کارشناسان ما در اولین فرصت پاسخ می‌دهند.", type="text", label="متن خوش‌آمد پشتیبانی", group="support"),

    # --- نصب ---
    _d("setup_completed", False, type="bool", label="نصب انجام شده", group="security"),
    _d("schema_version", 1, type="int", label="نسخه ساختار دیتابیس", group="security"),
    _d("last_expiry_sweep", "", label="آخرین پاکسازی رزروها", group="security"),
]

DEFINITION_MAP = {d.key: d for d in DEFINITIONS}
DEFAULT_VALUES = {d.key: d.default for d in DEFINITIONS}


def _cast(defn: SettingDef | None, raw: str | None) -> Any:
    if defn is None:
        return raw
    if defn.type == "bool":
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if defn.type == "int":
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return int(defn.default)
    return "" if raw is None else str(raw)


def load_all(force: bool = False) -> dict[str, Any]:
    """بارگذاری تمام تنظیمات از دیتابیس (کش در سطح درخواست)."""
    if has_app_context() and not force:
        cached = getattr(g, "_settings_cache", None)
        if cached is not None:
            return cached
    values: dict[str, Any] = {}
    try:
        rows = db.session.query(Setting).all()
        stored = {row.key: row.value for row in rows}
    except Exception:  # جداول هنوز ساخته نشده‌اند
        stored = {}
    for defn in DEFINITIONS:
        values[defn.key] = _cast(defn, stored.get(defn.key, defn.default))
    if has_app_context():
        g._settings_cache = values
    return values


def invalidate_cache() -> None:
    if has_app_context():
        g.pop("_settings_cache", None)


def get(key: str, default: Any = None) -> Any:
    values = load_all()
    if key in values:
        return values[key]
    return default


def get_raw(key: str) -> str | None:
    row = db.session.query(Setting).filter(Setting.key == key).first()
    return row.value if row else None


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get(key, default))
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    value = get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_str(key: str, default: str = "") -> str:
    value = get(key, default)
    return "" if value is None else str(value)


def set_value(key: str, value: Any) -> None:
    defn = DEFINITION_MAP.get(key)
    if isinstance(value, bool):
        stored = "1" if value else "0"
    else:
        stored = "" if value is None else str(value)
    row = db.session.query(Setting).filter(Setting.key == key).first()
    if row is None:
        row = Setting(key=key, value=stored, is_sensitive=bool(defn and defn.sensitive), updated_at=utcnow())
        db.session.add(row)
    else:
        row.value = stored
        row.updated_at = utcnow()
        if defn is not None:
            row.is_sensitive = defn.sensitive
    invalidate_cache()


def set_many(values: dict[str, Any], commit: bool = True) -> None:
    for key, value in values.items():
        if key in DEFINITION_MAP:
            set_value(key, value)
    if commit:
        db.session.commit()
    invalidate_cache()


def is_sensitive(key: str) -> bool:
    defn = DEFINITION_MAP.get(key)
    return bool(defn and defn.sensitive)


def group_definitions(group: str) -> list[SettingDef]:
    return [d for d in DEFINITIONS if d.group == group]


def money(amount: int | None, with_label: bool = True) -> str:
    """قالب‌بندی مبلغ بر اساس تنظیمات واحد پول."""
    values = load_all()
    return format_money(
        amount,
        unit=values.get("currency_unit", "toman"),
        label=values.get("currency_label", "تومان"),
        persian=bool(values.get("display_persian_digits", True)),
        custom_ratio=int(values.get("currency_ratio", 1) or 1),
        with_label=with_label,
    )


def number(value) -> str:
    values = load_all()
    return format_number(value, persian=bool(values.get("display_persian_digits", True)))
