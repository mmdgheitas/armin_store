"""داده‌های اولیه: نقش‌ها، مجوزها، تنظیمات، صفحات و (به‌صورت اختیاری) داده نمونه."""
from __future__ import annotations

from app.constants import DEFAULT_ROLES, PERMISSIONS
from app.extensions import db
from app.models import (
    Advertisement,
    BlogCategory,
    BlogPost,
    Category,
    Coupon,
    Page,
    Permission,
    Product,
    ProductVariant,
    Role,
    ShippingMethod,
    User,
)
from app.services import settings_service
from app.utils.dates import utcnow
from app.utils.security import hash_password

REQUIRED_TABLES = [
    "addresses",
    "advertisements",
    "audit_logs",
    "backup_logs",
    "blog_categories",
    "blog_posts",
    "categories",
    "coupon_redemptions",
    "coupons",
    "customer_videos",
    "digital_assets",
    "download_tokens",
    "expenses",
    "inventory_movements",
    "inventory_reservations",
    "notifications",
    "order_items",
    "order_status_history",
    "orders",
    "otp_codes",
    "pages",
    "payments",
    "permissions",
    "product_media",
    "product_variants",
    "products",
    "rate_limit_entries",
    "reviews",
    "role_permissions",
    "roles",
    "schema_migrations",
    "settings",
    "shipping_methods",
    "sms_logs",
    "ticket_messages",
    "tickets",
    "user_roles",
    "users",
    "wishlists",
]

DEFAULT_PAGES = [
    {
        "title": "درباره ما",
        "slug": "about",
        "content": "<h2>درباره فروشگاه</h2><p>ما با هدف ارائه محصولات باکیفیت و خدمات پس از فروش واقعی راه‌اندازی شده‌ایم. رضایت شما مهم‌ترین سرمایه ما است.</p>",
        "sort_order": 1,
    },
    {
        "title": "تماس با ما",
        "slug": "contact",
        "content": "<h2>در ارتباط باشیم</h2><p>برای ارتباط با ما از فرم پشتیبانی یا شماره تماس درج‌شده در فوتر استفاده کنید.</p>",
        "sort_order": 2,
    },
    {
        "title": "قوانین و مقررات",
        "slug": "terms",
        "content": "<h2>قوانین خرید</h2><p>با ثبت سفارش، قوانین فروشگاه را می‌پذیرید. اطلاعات محصولات و قیمت‌ها پیش از پرداخت نمایش داده می‌شود.</p>",
        "sort_order": 3,
    },
    {
        "title": "حریم خصوصی",
        "slug": "privacy",
        "content": "<h2>سیاست حفظ حریم خصوصی</h2><p>اطلاعات شما فقط برای پردازش سفارش و ارتباط با شما استفاده می‌شود و در اختیار شخص ثالث قرار نمی‌گیرد.</p>",
        "sort_order": 4,
    },
]


def seed_permissions_and_roles() -> None:
    permissions: dict[str, Permission] = {}
    for key, title in PERMISSIONS:
        permission = db.session.query(Permission).filter(Permission.key == key).first()
        if permission is None:
            permission = Permission(key=key, title=title)
            db.session.add(permission)
        else:
            permission.title = title
        permissions[key] = permission
    db.session.flush()

    for role_name, data in DEFAULT_ROLES.items():
        role = db.session.query(Role).filter(Role.name == role_name).first()
        if role is None:
            role = Role(name=role_name, title=data["title"], created_at=utcnow())
            db.session.add(role)
            db.session.flush()
        else:
            role.title = data["title"]
        role.permissions = [permissions[key] for key in data["permissions"] if key in permissions]
    db.session.commit()


def seed_settings(overrides: dict | None = None) -> None:
    existing = {row.key for row in db.session.query(settings_service.Setting).all()}
    for defn in settings_service.DEFINITIONS:
        if defn.key in existing:
            continue
        value = defn.default
        if overrides and defn.key in overrides:
            value = overrides[defn.key]
        stored = "1" if value is True else "0" if value is False else str(value)
        db.session.add(
            settings_service.Setting(
                key=defn.key, value=stored, is_sensitive=defn.sensitive, updated_at=utcnow()
            )
        )
    db.session.commit()
    settings_service.invalidate_cache()


def seed_pages() -> None:
    for page_data in DEFAULT_PAGES:
        if db.session.query(Page.id).filter(Page.slug == page_data["slug"]).first():
            continue
        db.session.add(Page(**page_data, is_published=True, show_in_footer=True))
    db.session.commit()


def seed_shipping_methods() -> None:
    if db.session.query(ShippingMethod.id).first():
        return
    methods = [
        ShippingMethod(
            name="پست پیشتاز",
            description="ارسال به سراسر کشور با کد رهگیری؛ تحویل ۲ تا ۴ روز کاری.",
            shipping_type="flat",
            base_cost=60000,
            estimated_days="۲ تا ۴ روز کاری",
            is_active=True,
            sort_order=1,
            configuration_json={},
        ),
        ShippingMethod(
            name="تیپاکس",
            description="ارسال سریع با تیپاکس؛ تحویل ۱ تا ۲ روز کاری.",
            shipping_type="flat",
            base_cost=95000,
            estimated_days="۱ تا ۲ روز کاری",
            is_active=True,
            sort_order=2,
            configuration_json={},
        ),
        ShippingMethod(
            name="تحویل حضوری",
            description="دریافت سفارش از فروشگاه پس از هماهنگی تلفنی.",
            shipping_type="pickup",
            base_cost=0,
            estimated_days="همان روز",
            is_active=True,
            sort_order=3,
            configuration_json={},
        ),
    ]
    db.session.add_all(methods)
    db.session.commit()


def create_admin(full_name: str, mobile: str, password: str) -> User:
    from app.utils.security import normalize_mobile

    mobile = normalize_mobile(mobile)
    user = db.session.query(User).filter(User.mobile == mobile).first()
    role = db.session.query(Role).filter(Role.name == "super_admin").first()
    if user is None:
        user = User(
            full_name=full_name,
            mobile=mobile,
            password_hash=hash_password(password),
            is_verified=True,
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.session.add(user)
    else:
        user.full_name = full_name or user.full_name
        user.password_hash = hash_password(password)
        user.is_active = True
        user.is_verified = True
    db.session.flush()
    if role is not None:
        user.roles = [role]
    db.session.commit()
    return user


def seed_demo_data() -> None:
    """داده نمونه برای آزمون فوری فروشگاه (فقط هنگام نصب اختیاری)."""
    if db.session.query(Product.id).first():
        return

    cats = [
        Category(name="دیجیتال", slug="digital", description="محصولات دانلودی و لایسنس", is_active=True, sort_order=1),
        Category(name="لوازم جانبی", slug="accessories", description="لوازم جانبی کاربردی", is_active=True, sort_order=2),
        Category(name="پوشاک", slug="clothing", description="پوشاک باکیفیت", is_active=True, sort_order=3),
    ]
    db.session.add_all(cats)
    db.session.flush()

    products = [
        {
            "category": cats[0],
            "name": "پکیج آموزش برنامه‌نویسی پایتون",
            "slug": "python-course",
            "sku": "DIG-1001",
            "short_description": "آموزش گام‌به‌گام پایتون از پایه تا پروژه واقعی",
            "description": "این پکیج شامل ۱۲ ساعت ویدئوی آموزشی، فایل‌های تمرین و پشتیبانی پرسش‌وپاسخ است. بلافاصله پس از پرداخت، لینک دانلود اختصاصی برای شما فعال می‌شود.",
            "type": "digital",
            "price": 450000,
            "cost": 50000,
            "featured": True,
            "specs": [
                {"name": "مدت آموزش", "value": "۱۲ ساعت"},
                {"name": "سطح", "value": "مقدماتی تا پیشرفته"},
                {"name": "فرمت", "value": "MP4 + PDF"},
            ],
        },
        {
            "category": cats[1],
            "name": "هدفون بی‌سیم مدل Aria",
            "slug": "aria-headphone",
            "sku": "ACC-2001",
            "short_description": "هدفون بلوتوثی با حذف نویز فعال و ۳۰ ساعت باتری",
            "description": "هدفون Aria با درایور ۴۰ میلی‌متری، اتصال بلوتوث ۵.۳ و قابلیت حذف نویز فعال، تجربه‌ای شفاف از موسیقی ارائه می‌دهد. همراه با کیف حمل و کابل شارژ USB-C.",
            "type": "physical",
            "price": 2450000,
            "cost": 1600000,
            "featured": True,
            "specs": [
                {"name": "نوع اتصال", "value": "بلوتوث ۵.۳"},
                {"name": "مدت باتری", "value": "۳۰ ساعت"},
                {"name": "گارانتی", "value": "۱۸ ماه"},
            ],
            "variants": [
                {"attrs": {"رنگ": "مشکی"}, "price": 2450000, "compare": 2900000, "stock": 12, "cost": 1600000},
                {"attrs": {"رنگ": "سفید"}, "price": 2550000, "stock": 5, "cost": 1650000},
            ],
        },
        {
            "category": cats[1],
            "name": "پاوربانک ۲۰۰۰۰ میلی‌آمپر",
            "slug": "powerbank-20000",
            "sku": "ACC-2002",
            "short_description": "شارژ سریع ۲۲.۵ وات با دو خروجی USB",
            "description": "پاوربانک ۲۰۰۰۰ میلی‌آمپری با پشتیبانی از شارژ سریع ۲۲.۵ وات، نمایشگر دیجیتال درصد شارژ و بدنه مقاوم.",
            "type": "physical",
            "price": 980000,
            "cost": 620000,
            "featured": False,
            "specs": [
                {"name": "ظرفیت", "value": "۲۰۰۰۰ میلی‌آمپر"},
                {"name": "توان خروجی", "value": "۲۲.۵ وات"},
            ],
            "variants": [
                {"attrs": {"رنگ": "مشکی"}, "price": 980000, "stock": 20, "cost": 620000},
                {"attrs": {"رنگ": "آبی"}, "price": 980000, "stock": 3, "cost": 620000},
            ],
        },
        {
            "category": cats[2],
            "name": "تی‌شرت نخی ساده",
            "slug": "cotton-tshirt",
            "sku": "CLT-3001",
            "short_description": "تی‌شرت ۱۰۰٪ پنبه با دوخت تمیز",
            "description": "تی‌شرت نخی با کیفیت بالا، مناسب استفاده روزمره. قابل شستشو با ماشین لباسشویی.",
            "type": "physical",
            "price": 320000,
            "cost": 190000,
            "featured": False,
            "specs": [
                {"name": "جنس", "value": "۱۰۰٪ پنبه"},
                {"name": "کشور سازنده", "value": "ایران"},
            ],
            "variants": [
                {"attrs": {"سایز": "M", "رنگ": "سفید"}, "price": 320000, "stock": 8, "cost": 190000},
                {"attrs": {"سایز": "L", "رنگ": "سفید"}, "price": 330000, "stock": 6, "cost": 195000},
                {"attrs": {"سایز": "L", "رنگ": "مشکی"}, "price": 330000, "stock": 0, "cost": 195000},
            ],
        },
        {
            "category": cats[0],
            "name": "لایسنس نرم‌افزار مدیریت پروژه",
            "slug": "project-license",
            "sku": "DIG-1002",
            "short_description": "لایسنس یک‌ساله با فعال‌سازی آنلاین",
            "description": "لایسنس یک‌ساله نرم‌افزار مدیریت پروژه؛ کد فعال‌سازی پس از پرداخت در حساب کاربری شما نمایش داده می‌شود.",
            "type": "digital",
            "price": 1200000,
            "cost": 400000,
            "featured": False,
            "specs": [{"name": "مدت لایسنس", "value": "۱۲ ماه"}, {"name": "تعداد کاربر", "value": "۵"}],
        },
        {
            "category": cats[1],
            "name": "کیبورد مکانیکی RGB",
            "slug": "rgb-keyboard",
            "sku": "ACC-2003",
            "short_description": "کیبورد مکانیکی با سوییچ آبی و نورپردازی RGB",
            "description": "کیبورد مکانیکی با بدنه آلومینیومی، سوییچ آبی و نورپردازی RGB قابل تنظیم.",
            "type": "physical",
            "price": 1750000,
            "cost": 1100000,
            "featured": True,
            "specs": [{"name": "نوع سوییچ", "value": "آبی"}, {"name": "نورپردازی", "value": "RGB"}],
            "variants": [
                {"attrs": {"چیدمان": "فارسی"}, "price": 1750000, "compare": 1990000, "stock": 7, "cost": 1100000},
            ],
        },
    ]

    for index, data in enumerate(products):
        product = Product(
            category_id=data["category"].id,
            name=data["name"],
            slug=data["slug"],
            sku=data["sku"],
            short_description=data["short_description"],
            description=data["description"],
            product_type=data["type"],
            base_price=data["price"],
            base_cost=data["cost"],
            is_featured=data["featured"],
            is_active=True,
            specifications=data["specs"],
            seo_title=data["name"],
            seo_description=data["short_description"],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.session.add(product)
        db.session.flush()
        variants = data.get("variants") or [{"attrs": {}, "price": data["price"], "stock": 10, "cost": data["cost"]}]
        for v_index, variant_data in enumerate(variants):
            db.session.add(
                ProductVariant(
                    product_id=product.id,
                    sku=f"{data['sku']}-{v_index + 1}",
                    attributes_json=variant_data["attrs"],
                    price=variant_data["price"],
                    compare_at_price=variant_data.get("compare", 0),
                    cost_price=variant_data.get("cost", data["cost"]),
                    stock_quantity=variant_data["stock"],
                    low_stock_threshold=3,
                    is_active=True,
                    is_default=(v_index == 0),
                    sort_order=v_index,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )

    db.session.add(
        Coupon(
            code="WELCOME10",
            description="۱۰ درصد تخفیف اولین خرید",
            discount_type="percent",
            discount_value=10,
            minimum_order=200000,
            maximum_discount=300000,
            usage_limit=100,
            per_user_limit=1,
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.session.add(
        Advertisement(
            text="ارسال رایگان برای خریدهای بالای ۲ میلیون تومان",
            link="/products",
            background_color="#0f766e",
            text_color="#ffffff",
            is_active=True,
            sort_order=1,
        )
    )

    blog_cat = BlogCategory(name="راهنمای خرید", slug="buying-guide")
    db.session.add(blog_cat)
    db.session.flush()
    db.session.add(
        BlogPost(
            category_id=blog_cat.id,
            title="چگونه هدفون مناسب انتخاب کنیم؟",
            slug="how-to-choose-headphone",
            excerpt="معیارهای مهم در انتخاب هدفول؛ از نوع اتصال تا کیفیت صدا.",
            content="<p>پیش از خرید هدفون به نوع اتصال، مدت باتری و کیفیت درایور توجه کنید...</p>",
            status="published",
            published_at=utcnow(),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.session.commit()


def seed_all(include_demo: bool = False) -> None:
    seed_permissions_and_roles()
    seed_settings()
    seed_pages()
    seed_shipping_methods()
    if include_demo:
        seed_demo_data()
