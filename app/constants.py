"""ثابت‌های دامنه: وضعیت‌ها، مجوزها، نقش‌های پیش‌فرض."""
from __future__ import annotations

# --- سفارش ---
ORDER_STATUSES = [
    ("pending_payment", "در انتظار پرداخت"),
    ("paid", "پرداخت‌شده"),
    ("processing", "در حال پردازش"),
    ("ready_to_ship", "آماده ارسال"),
    ("shipped", "ارسال‌شده"),
    ("delivered", "تحویل‌شده"),
    ("cancelled", "لغوشده"),
    ("returned", "مرجوع‌شده"),
    ("refunded", "بازپرداخت‌شده"),
]
ORDER_STATUS_LABELS = dict(ORDER_STATUSES)

# جریان مجاز تغییر وضعیت سفارش (جلوگیری از تغییر غیرمجاز)
ORDER_STATUS_TRANSITIONS = {
    "pending_payment": {"paid", "cancelled"},
    "paid": {"processing", "cancelled", "refunded"},
    "processing": {"ready_to_ship", "cancelled", "refunded"},
    "ready_to_ship": {"shipped", "cancelled", "refunded"},
    "shipped": {"delivered", "returned"},
    "delivered": {"returned", "refunded"},
    "cancelled": set(),
    "returned": {"refunded"},
    "refunded": set(),
}

PAYMENT_STATUSES = [
    ("unpaid", "پرداخت‌نشده"),
    ("initiated", "آغازشده"),
    ("pending", "در انتظار تأیید"),
    ("paid", "پرداخت‌شده"),
    ("failed", "ناموفق"),
    ("cancelled", "لغوشده"),
    ("unknown", "نامشخص"),
    ("refunded", "بازپرداخت‌شده"),
]
PAYMENT_STATUS_LABELS = dict(PAYMENT_STATUSES)

PAYMENT_TRANSACTION_STATUSES = [
    ("initiated", "آغازشده"),
    ("pending", "در انتظار"),
    ("successful", "موفق"),
    ("failed", "ناموفق"),
    ("cancelled", "لغوشده"),
    ("unknown", "نامشخص"),
    ("refunded", "بازپرداخت‌شده"),
]

SHIPPING_STATUSES = [
    ("pending", "در انتظار آماده‌سازی"),
    ("preparing", "در حال آماده‌سازی"),
    ("ready", "آماده ارسال"),
    ("shipped", "ارسال‌شده"),
    ("delivered", "تحویل‌شده"),
    ("returned", "مرجوع‌شده"),
]
SHIPPING_STATUS_LABELS = dict(SHIPPING_STATUSES)

REVIEW_STATUSES = [("pending", "در انتظار تأیید"), ("approved", "تأییدشده"), ("rejected", "ردشده")]
VIDEO_STATUSES = REVIEW_STATUSES
COUPON_DISCOUNT_TYPES = [("percent", "درصدی"), ("fixed", "مبلغ ثابت")]
SHIPPING_TYPES = [
    ("flat", "هزینه ثابت"),
    ("province", "هزینه بر اساس استان"),
    ("city", "هزینه بر اساس شهر"),
    ("free_threshold", "آزاد برای خرید بالای مبلغ مشخص"),
    ("pickup", "تحویل حضوری"),
]
TICKET_STATUSES = [
    ("open", "باز"),
    ("answered", "پاسخ داده‌شده"),
    ("pending", "در انتظار مشتری"),
    ("closed", "بسته"),
]
TICKET_PRIORITIES = [("low", "کم"), ("normal", "عادی"), ("high", "زیاد"), ("urgent", "فوری")]
TICKET_DEPARTMENTS = [
    ("sales", "فروش"),
    ("support", "پشتیبانی فنی"),
    ("order", "پیگیری سفارش"),
    ("other", "سایر"),
]
PRODUCT_TYPES = [("physical", "فیزیکی"), ("digital", "دیجیتال")]
MEDIA_TYPES = [("image", "تصویر"), ("video", "ویدئو")]
DIGITAL_DELIVERY_TYPES = [
    ("file", "فایل دانلودی"),
    ("link", "لینک خارجی"),
    ("license", "کد لایسنس"),
]
RESERVATION_STATUSES = ["active", "consumed", "released", "expired"]
INVENTORY_MOVEMENT_TYPES = [
    ("in", "ورود کالا"),
    ("out", "خروج کالا"),
    ("adjust", "اصلاح موجودی"),
    ("sale", "فروش"),
    ("reserve", "رزرو"),
    ("release", "آزادسازی رزرو"),
    ("return", "مرجوعی"),
]

# --- مجوزها ---
PERMISSIONS = [
    ("dashboard.view", "مشاهده داشبورد"),
    ("products.view", "مشاهده محصولات"),
    ("products.manage", "مدیریت محصولات"),
    ("categories.manage", "مدیریت دسته‌بندی‌ها"),
    ("variants.manage", "مدیریت تنوع محصولات"),
    ("inventory.view", "مشاهده انبار"),
    ("inventory.manage", "مدیریت انبار"),
    ("orders.view", "مشاهده سفارش‌ها"),
    ("orders.manage", "مدیریت سفارش‌ها"),
    ("payments.view", "مشاهده پرداخت‌ها"),
    ("payments.manage", "مدیریت پرداخت‌ها"),
    ("customers.view", "مشاهده مشتریان"),
    ("customers.manage", "مدیریت مشتریان"),
    ("reviews.moderate", "مدیریت نظرات"),
    ("videos.moderate", "مدیریت ویدئوهای مشتریان"),
    ("ads.manage", "مدیریت تبلیغات"),
    ("coupons.manage", "مدیریت کدهای تخفیف"),
    ("shipping.manage", "مدیریت روش‌های ارسال"),
    ("settings.manage", "مدیریت تنظیمات"),
    ("payment_settings.manage", "تنظیمات درگاه پرداخت"),
    ("sms_settings.manage", "تنظیمات پیامک"),
    ("reports.view", "مشاهده گزارش‌ها"),
    ("expenses.manage", "مدیریت هزینه‌ها"),
    ("blog.manage", "مدیریت وبلاگ"),
    ("pages.manage", "مدیریت صفحات"),
    ("tickets.manage", "مدیریت تیکت‌ها"),
    ("seo.manage", "مدیریت SEO"),
    ("users.manage", "مدیریت کاربران و نقش‌ها"),
    ("backups.manage", "مدیریت بکاپ"),
    ("audit.view", "مشاهده لاگ رویدادها"),
]

DEFAULT_ROLES = {
    "super_admin": {
        "title": "مدیر کل",
        "permissions": [key for key, _ in PERMISSIONS],
    },
    "product_manager": {
        "title": "مدیر محصولات و انبار",
        "permissions": [
            "dashboard.view",
            "products.view",
            "products.manage",
            "categories.manage",
            "variants.manage",
            "inventory.view",
            "inventory.manage",
        ],
    },
    "order_manager": {
        "title": "مدیر سفارش‌ها",
        "permissions": [
            "dashboard.view",
            "orders.view",
            "orders.manage",
            "payments.view",
            "shipping.manage",
            "customers.view",
        ],
    },
    "support_manager": {
        "title": "پشتیبان مشتریان",
        "permissions": [
            "dashboard.view",
            "customers.view",
            "tickets.manage",
            "reviews.moderate",
            "videos.moderate",
            "orders.view",
        ],
    },
}

SMS_EVENTS = [
    ("otp", "کد ورود / تأیید"),
    ("order_created", "ثبت سفارش"),
    ("payment_success", "پرداخت موفق"),
    ("order_processing", "در حال پردازش"),
    ("order_ready", "آماده ارسال"),
    ("order_shipped", "ارسال سفارش"),
    ("order_delivered", "تحویل سفارش"),
    ("order_cancelled", "لغو سفارش"),
    ("ticket_reply", "پاسخ به تیکت"),
]

IRAN_PROVINCES = [
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز", "ایلام",
    "بوشهر", "تهران", "چهارمحال و بختیاری", "خراسان جنوبی", "خراسان رضوی",
    "خراسان شمالی", "خوزستان", "زنجان", "سمنان", "سیستان و بلوچستان", "فارس",
    "قزوین", "قم", "کردستان", "کرمان", "کرمانشاه", "کهگیلویه و بویراحمد",
    "گلستان", "گیلان", "لرستان", "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
]
