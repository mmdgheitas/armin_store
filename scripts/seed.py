from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.config import Config
from app.db import create_connection, run_migrations
from app.repositories.catalog import sync_product_tags
from app.repositories.settings import DEFAULT_SETTINGS, set_settings
from app.services.auth import hash_password
from app.services.reviews import recalculate_product_rating
from app.utils.persian import now_utc_iso, slugify_persian

PROVINCES_CITIES: dict[str, list[str]] = {
    "تهران": ["تهران", "شهریار", "اسلامشهر", "ورامین", "ری", "پردیس"],
    "البرز": ["کرج", "فردیس", "ساوجبلاغ", "نظرآباد"],
    "اصفهان": ["اصفهان", "کاشان", "خمینی‌شهر", "نجف‌آباد", "شاهین‌شهر"],
    "فارس": ["شیراز", "مرودشت", "جهرم", "فسا", "کازرون"],
    "خراسان رضوی": ["مشهد", "نیشابور", "سبزوار", "تربت حیدریه", "قوچان"],
    "آذربایجان شرقی": ["تبریز", "مراغه", "مرند", "اهر", "میانه"],
    "آذربایجان غربی": ["ارومیه", "خوی", "مهاباد", "بوکان"],
    "گیلان": ["رشت", "لاهیجان", "بندر انزلی", "لنگرود", "تالش"],
    "مازندران": ["ساری", "بابل", "آمل", "قائم‌شهر", "فریدونکنار", "بابلسر"],
    "خوزستان": ["اهواز", "آبادان", "دزفول", "خرمشهر", "شوشتر"],
    "قم": ["قم"],
    "قزوین": ["قزوین", "تاکستان", "البرز"],
    "کرمان": ["کرمان", "رفسنجان", "سیرجان", "بم"],
    "کرمانشاه": ["کرمانشاه", "اسلام‌آباد غرب", "کنگاور"],
    "همدان": ["همدان", "ملایر", "نهاوند"],
    "یزد": ["یزد", "میبد", "اردکان"],
    "سمنان": ["سمنان", "شاهرود", "دامغان"],
    "زنجان": ["زنجان", "ابهر", "خرمدره"],
    "اردبیل": ["اردبیل", "پارس‌آباد", "مشگین‌شهر"],
    "گلستان": ["گرگان", "گنبد کاووس", "علی‌آباد"],
    "مرکزی": ["اراک", "ساوه", "خمین"],
    "لرستان": ["خرم‌آباد", "بروجرد", "دورود"],
    "هرمزگان": ["بندرعباس", "قشم", "کیش", "میناب"],
    "بوشهر": ["بوشهر", "برازجان", "کنگان"],
    "سیستان و بلوچستان": ["زاهدان", "چابهار", "ایرانشهر"],
    "کردستان": ["سنندج", "سقز", "مریوان"],
    "چهارمحال و بختیاری": ["شهرکرد", "بروجن"],
    "کهگیلویه و بویراحمد": ["یاسوج", "دوگنبدان"],
    "ایلام": ["ایلام", "دهلران"],
    "خراسان شمالی": ["بجنورد", "شیروان"],
    "خراسان جنوبی": ["بیرجند", "قائن"],
}


def _make_sample_image(
    upload_root: Path, rel_base: str, bg_rgb: tuple[int, int, int], accent_rgb: tuple[int, int, int], size=(800, 600)
) -> dict[str, str]:
    out = upload_root / Path(rel_base).parent
    out.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, bg_rgb)
    draw = ImageDraw.Draw(img)
    w, h = size
    # Warm decorative bands and emblem circles (avoids external font/bidi issues in Pillow)
    draw.rectangle([0, h - 90, w, h], fill=accent_rgb)
    draw.ellipse([w // 2 - 130, h // 2 - 150, w // 2 + 130, h // 2 + 110], fill=accent_rgb)
    draw.ellipse([w // 2 - 90, h // 2 - 110, w // 2 + 90, h // 2 + 70], fill=bg_rgb)
    draw.ellipse([w // 2 - 45, h // 2 - 65, w // 2 + 45, h // 2 + 25], fill=accent_rgb)

    jpg_rel = f"{rel_base}.jpg"
    webp_rel = f"{rel_base}.webp"
    thumb_rel = f"{rel_base}_thumb.webp"

    img.save(upload_root / jpg_rel, format="JPEG", quality=86)
    img.save(upload_root / webp_rel, format="WEBP", quality=84)
    thumb = img.copy()
    thumb.thumbnail((420, 420), Image.Resampling.LANCZOS)
    thumb.save(upload_root / thumb_rel, format="WEBP", quality=80)
    return {"file_path": jpg_rel, "webp_path": webp_rel, "thumb_path": thumb_rel}


def run_seed(db_path: str | None = None, upload_dir: str | None = None) -> None:
    db_file = db_path or Config.DATABASE_PATH
    up_root = Path(upload_dir or Config.UPLOAD_FOLDER)
    up_root.mkdir(parents=True, exist_ok=True)

    run_migrations(db_file)
    conn = create_connection(db_file)
    ts = now_utc_iso()

    try:
        conn.execute("BEGIN")

        # 1. Settings
        existing_settings = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        if existing_settings == 0:
            set_settings(conn, DEFAULT_SETTINGS)

        # 2. Provinces & Cities
        for p_idx, (p_name, cities) in enumerate(PROVINCES_CITIES.items()):
            p_slug = slugify_persian(p_name)
            conn.execute(
                "INSERT OR IGNORE INTO provinces (name, slug, sort_order) VALUES (?, ?, ?)",
                (p_name, p_slug, p_idx),
            )
            prow = conn.execute("SELECT id FROM provinces WHERE name = ?", (p_name,)).fetchone()
            pid = int(prow["id"])
            for c_idx, c_name in enumerate(cities):
                conn.execute(
                    "INSERT OR IGNORE INTO cities (province_id, name, slug, sort_order) VALUES (?, ?, ?, ?)",
                    (pid, c_name, slugify_persian(c_name), c_idx),
                )

        # 3. Default Admin & Sample Customer
        admin_username = os.getenv("SEED_ADMIN_USERNAME", "admin")
        admin_email = os.getenv("SEED_ADMIN_EMAIL", "admin@arminstore.ir")
        admin_mobile = os.getenv("SEED_ADMIN_MOBILE", "09120000000")
        admin_pw = os.getenv("SEED_ADMIN_PASSWORD", "ChangeMe123!@#")

        if not conn.execute("SELECT 1 FROM users WHERE username = ? OR role = 'super_admin'", (admin_username,)).fetchone():
            conn.execute(
                """
                INSERT INTO users (username, email, mobile, password_hash, full_name, role, is_active, mobile_verified, email_verified, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'مدیر کل فروشگاه', 'super_admin', 1, 1, 1, ?, ?)
                """,
                (admin_username, admin_email, admin_mobile, hash_password(admin_pw), ts, ts),
            )

        if not conn.execute("SELECT 1 FROM users WHERE mobile = '09121112233'").fetchone():
            conn.execute(
                """
                INSERT INTO users (mobile, email, password_hash, full_name, role, is_active, mobile_verified, created_at, updated_at)
                VALUES ('09121112233', 'customer@example.ir', ?, 'سارا محمدی', 'customer', 1, 1, ?, ?)
                """,
                (hash_password("Customer123!"), ts, ts),
            )

        # 4. Categories
        cat_specs = [
            ("برنج ایرانی", "برنج-ایرانی", "انواع برنج اصیل شمال، طارم، هاشمی و صدری سورت‌شده", (232, 240, 226), (27, 94, 63)),
            ("حبوبات و غلات", "حبوبات", "حبوبات دست‌چین و زودپز محصول کشاورزان ایرانی", (245, 236, 220), (180, 83, 9)),
            ("ادویه و چاشنی", "ادویه-و-چاشنی", "زعفران ممتاز قائنات و ادویه‌جات تازه آسیاب‌شده", (252, 236, 224), (217, 119, 6)),
            ("چای و دمنوش", "چای-و-دمنوش", "چای بهاره لاهیجان و دمنوش‌های طبیعی بدون اسانس", (226, 242, 234), (19, 68, 45)),
        ]
        cat_ids: dict[str, int] = {}
        for idx, (cname, cslug, cdesc, bg, acc) in enumerate(cat_specs):
            row = conn.execute("SELECT id FROM categories WHERE slug = ?", (cslug,)).fetchone()
            if not row:
                img = _make_sample_image(up_root, f"categories/cat_{idx+1}", bg, acc, (600, 400))
                cur = conn.execute(
                    """
                    INSERT INTO categories (name, slug, description, image_path, sort_order, is_active, meta_title, meta_description, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (cname, cslug, cdesc, img["webp_path"], idx, f"خرید {cname} اصل | فروشگاه آرمین", cdesc, ts, ts),
                )
                cat_ids[cslug] = int(cur.lastrowid)
            else:
                cat_ids[cslug] = int(row["id"])

        # 5. Sample Products (with multiple variants, specs, tags, images)
        products_seed = [
            {
                "cat": "برنج-ایرانی",
                "title": "برنج طارم هاشمی اعلا فریدونکنار",
                "slug": "برنج-طارم-هاشمی-فریدونکنار",
                "short": "برنج کشت اول برداشت جدید با عطر بی‌نظیر و پخت مجلسی",
                "html": "<p>برنج طارم هاشمی فریدونکنار از شالیزارهای بکر مازندران، دو بار الک و سورت لیزری شده با درصد خلوص بالا و ری عالی.</p>",
                "origin": "فریدونکنار، مازندران",
                "featured": 1,
                "sold": 84,
                "colors": ((236, 244, 232), (27, 94, 63)),
                "tags": ["برنج شمال", "طارم هاشمی", "برداشت جدید"],
                "specs": [("محل کشت", "فریدونکنار مازندران"), ("نوع دانه", "دانه بلند خوش‌عطر"), ("شرایط نگهداری", "جای خشک و خنک")],
                "variants": [
                    ("کیسه ۱ کیلوگرم", "ARM-RICE-TH-1K", 1950000, 2150000, 1000, 40),
                    ("کیسه ۵ کیلوگرم", "ARM-RICE-TH-5K", 9400000, 9950000, 5000, 25),
                    ("کیسه ۱۰ کیلوگرم", "ARM-RICE-TH-10K", 18500000, None, 10000, 15),
                ],
            },
            {
                "cat": "برنج-ایرانی",
                "title": "برنج صدری دم‌سیاه ممتاز آستانه",
                "slug": "برنج-صدری-دم-سیاه-آستانه",
                "short": "یکی از اصیل‌ترین برنج‌های گیلان با ماندگاری نرمی پس از سرد شدن",
                "html": "<p>برنج دم‌سیاه آستانه اشرفیه انتخاب اول مهمانی‌های رسمی با قد کشیدن چشمگیر و عطر طبیعی.</p>",
                "origin": "آستانه اشرفیه، گیلان",
                "featured": 1,
                "sold": 52,
                "colors": ((230, 240, 232), (19, 68, 45)),
                "tags": ["برنج گیلان", "دم‌سیاه", "مجلسی"],
                "specs": [("محل کشت", "آستانه اشرفیه گیلان"), ("خلوص", "۱۰۰٪ خالص یکدست")],
                "variants": [
                    ("کیسه ۵ کیلوگرم", "ARM-RICE-DS-5K", 10200000, None, 5000, 18),
                    ("کیسه ۱۰ کیلوگرم", "ARM-RICE-DS-10K", 19900000, 21200000, 10000, 8),
                ],
            },
            {
                "cat": "حبوبات",
                "title": "عدس سبز محلی دست‌چین اردبیل",
                "slug": "عدس-سبز-محلی-اردبیل",
                "short": "عدس دیم درشت، زودپز و سرشار از آهن و پروتئین",
                "html": "<p>عدس سبز کشت دیم منطقه اردبیل، تمیز شده بدون سنگریزه و با پخت سریع و لعاب عالی.</p>",
                "origin": "اردبیل",
                "featured": 0,
                "sold": 41,
                "colors": ((239, 237, 224), (101, 130, 62)),
                "tags": ["حبوبات دیم", "زودپز", "محلی"],
                "specs": [("منطقه تولید", "اردبیل"), ("نوع کشت", "دیم")],
                "variants": [
                    ("بسته ۱ کیلوگرم", "ARM-LEG-LNT-1K", 1450000, 1600000, 1000, 35),
                    ("بسته ۲ کیلوگرم", "ARM-LEG-LNT-2K", 2800000, None, 2000, 20),
                ],
            },
            {
                "cat": "حبوبات",
                "title": "لوبیا چیتی ممتاز خمین",
                "slug": "لوبیا-چیتی-ممتاز-خمین",
                "short": "لوبیا چیتی درشت و خوش‌لعاب مخصوص قورمه و خوراک لوبیا",
                "html": "<p>تولید مزارع خمین با سورتینگ دقیق، بدون نیاز به خیساندن طولانی و پخت یکنواخت.</p>",
                "origin": "خمین، مرکزی",
                "featured": 1,
                "sold": 63,
                "colors": ((247, 236, 224), (168, 92, 50)),
                "tags": ["حبوبات خمین", "دست‌چین"],
                "specs": [("منطقه تولید", "خمین"), ("نوع بسته‌بندی", "سلفون زیپ‌دار")],
                "variants": [
                    ("بسته ۱ کیلوگرم", "ARM-LEG-PIN-1K", 1780000, None, 1000, 30),
                    ("بسته ۵ کیلوگرم", "ARM-LEG-PIN-5K", 8500000, 9100000, 5000, 10),
                ],
            },
            {
                "cat": "ادویه-و-چاشنی",
                "title": "زعفران سرگل ممتاز قائنات",
                "slug": "زعفران-سرگل-ممتاز-قائنات",
                "short": "زعفران تمام‌قرمز با قدرت رنگ‌دهی و عطر فوق‌العاده در بسته‌بندی کادویی",
                "html": "<p>زعفران سرگل امسالی قائنات بدون ریشه و سفیدی، آزمایش‌شده از نظر کروسین و سافرانال.</p>",
                "origin": "قائنات، خراسان جنوبی",
                "featured": 1,
                "sold": 97,
                "colors": ((254, 240, 225), (194, 65, 12)),
                "tags": ["زعفران قائنات", "امسالی", "صادراتی"],
                "specs": [("مبدا", "قائنات"), ("نوع زعفران", "سرگل ممتاز")],
                "variants": [
                    ("یک مثقال (۴.۶ گرم)", "ARM-SPC-SAF-46", 4600000, 4950000, 5, 22),
                    ("دو مثقال (۹.۲ گرم)", "ARM-SPC-SAF-92", 8900000, None, 10, 12),
                ],
            },
            {
                "cat": "ادویه-و-چاشنی",
                "title": "زردچوبه قلمی هندی آسیاب تازه",
                "slug": "زردچوبه-قلمی-آسیاب-تازه",
                "short": "آسیاب‌شده از ریشه قلمی درجه یک بدون افزودنی و رنگ مصنوعی",
                "html": "<p>زردچوبه خالص با عطر تند و رنگ طلایی طبیعی، آسیاب هفتگی برای حفظ حداکثر خواص کورکومین.</p>",
                "origin": "بسته‌بندی آرمین",
                "featured": 0,
                "sold": 38,
                "colors": ((254, 243, 199), (217, 119, 6)),
                "tags": ["ادویه خالص", "آسیاب تازه"],
                "specs": [("خلوص", "۱۰۰٪ بدون نشاسته و افزودنی")],
                "variants": [
                    ("بسته ۲۵۰ گرم", "ARM-SPC-TUR-250", 650000, None, 250, 50),
                    ("بسته ۵۰۰ گرم", "ARM-SPC-TUR-500", 1220000, 1350000, 500, 20),
                ],
            },
            {
                "cat": "چای-و-دمنوش",
                "title": "چای سرگل بهاره لاهیجان",
                "slug": "چای-سرگل-بهاره-لاهیجان",
                "short": "چای سیاه طبیعی چین اول بهار بدون اسانس و رنگ افزودنی",
                "html": "<p>تهیه‌شده از جوانه‌های لطیف بهاره باغات لاهیجان با دم‌کشیدن آرام و طعم اصیل ایرانی.</p>",
                "origin": "لاهیجان، گیلان",
                "featured": 1,
                "sold": 74,
                "colors": ((228, 241, 232), (20, 83, 45)),
                "tags": ["چای طبیعی", "چین اول بهار", "بدون اسانس"],
                "specs": [("منطقه", "لاهیجان"), ("زمان دم کشیدن", "۱۲ تا ۱۵ دقیقه")],
                "variants": [
                    ("بسته ۵۰۰ گرم", "ARM-TEA-SRG-500", 2400000, 2650000, 500, 32),
                    ("بسته ۱ کیلوگرم", "ARM-TEA-SRG-1K", 4600000, None, 1000, 14),
                ],
            },
            {
                "cat": "چای-و-دمنوش",
                "title": "چای سبز قلم دست‌چین لاهیجان",
                "slug": "چای-سبز-قلم-لاهیجان",
                "short": "چای سبز ارگانیک سرشار از آنتی‌اکسیدان با طعم ملایم و گوارا",
                "html": "<p>چای سبز طبیعی شمال ایران، خشک‌شده به روش سنتی برای حفظ عطر و خواص.</p>",
                "origin": "لاهیجان، گیلان",
                "featured": 0,
                "sold": 19,
                "colors": ((236, 248, 236), (34, 139, 34)),
                "tags": ["چای سبز", "ارگانیک"],
                "specs": [("منطقه", "لاهیجان"), ("نوع", "قلم طبیعی")],
                "variants": [
                    ("بسته ۲۵۰ گرم", "ARM-TEA-GRN-250", 1350000, None, 250, 0),
                ],
            },
        ]

        cust_row = conn.execute("SELECT id FROM users WHERE role = 'customer' LIMIT 1").fetchone()
        for idx, pd in enumerate(products_seed):
            if conn.execute("SELECT 1 FROM products WHERE slug = ?", (pd["slug"],)).fetchone():
                continue
            cur = conn.execute(
                """
                INSERT INTO products (
                    category_id, title, slug, short_description, description_html,
                    brand_origin, status, is_featured, total_sold,
                    meta_title, meta_description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, ?, ?)
                """,
                (
                    cat_ids[pd["cat"]],
                    pd["title"],
                    pd["slug"],
                    pd["short"],
                    pd["html"],
                    pd["origin"],
                    pd["featured"],
                    pd["sold"],
                    f"خرید {pd['title']} | فروشگاه آرمین",
                    pd["short"],
                    ts,
                    ts,
                ),
            )
            pid = int(cur.lastrowid)

            for v_idx, (lbl, sku, price, comp, weight, stock) in enumerate(pd["variants"]):
                vcur = conn.execute(
                    """
                    INSERT INTO product_variants (
                        product_id, label, sku, price_rial, compare_at_price_rial,
                        weight_grams, stock_quantity, low_stock_threshold, is_active, sort_order, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 5, 1, ?, ?, ?)
                    """,
                    (pid, lbl, sku, price, comp, weight, stock, v_idx, ts, ts),
                )
                vid = int(vcur.lastrowid)
                if stock > 0:
                    conn.execute(
                        "INSERT INTO stock_movements (variant_id, delta, reason, note, before_qty, after_qty, created_at) VALUES (?, ?, 'initial_seed', 'موجودی اولیه نمونه', 0, ?, ?)",
                        (vid, stock, stock, ts),
                    )

            im = _make_sample_image(up_root, f"products/prod_{idx+1}", pd["colors"][0], pd["colors"][1], (800, 600))
            conn.execute(
                """
                INSERT INTO product_images (product_id, file_path, thumb_path, webp_path, alt_text, is_cover, sort_order, width, height, created_at)
                VALUES (?, ?, ?, ?, ?, 1, 0, 800, 600, ?)
                """,
                (pid, im["file_path"], im["thumb_path"], im["webp_path"], pd["title"], ts),
            )

            for s_idx, (sk, sv) in enumerate(pd["specs"]):
                conn.execute(
                    "INSERT INTO product_specs (product_id, spec_key, spec_value, sort_order) VALUES (?, ?, ?, ?)",
                    (pid, sk, sv, s_idx),
                )
            sync_product_tags(conn, pid, pd["tags"])

            if cust_row and idx < 4:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reviews (product_id, user_id, rating, title, body, status, is_verified_buyer, created_at, updated_at)
                    VALUES (?, ?, 5, 'کیفیت عالی و بسته‌بندی تمیز', 'بسیار خوش‌عطر و باکیفیت بود. بسته‌بندی محکم و ارسال سریع داشت.', 'approved', 1, ?, ?)
                    """,
                    (pid, cust_row["id"], ts, ts),
                )
                recalculate_product_rating(conn, pid)

        # 6. Hero Banners
        if conn.execute("SELECT COUNT(*) FROM banners").fetchone()[0] == 0:
            b1 = _make_sample_image(up_root, "banners/hero_1", (20, 74, 48), (217, 119, 6), (1200, 420))
            b2 = _make_sample_image(up_root, "banners/hero_2", (140, 65, 20), (27, 94, 63), (1200, 420))
            conn.execute(
                """
                INSERT INTO banners (title, subtitle, image_path, link_url, button_text, placement, sort_order, is_active, created_at, updated_at)
                VALUES
                ('برنج اصیل شمال، مستقیم از شالیزار به سفره شما', 'طارم هاشمی و صدری دم‌سیاه سورت لیزری با ضمانت عطر و پخت', ?, '/category/برنج-ایرانی', 'خرید برنج ایرانی', 'hero', 1, 1, ?, ?),
                ('زعفران سرگل قائنات و ادویه‌جات تازه آسیاب', 'عطر و رنگ بی‌نظیر با بسته‌بندی بهداشتی', ?, '/category/ادویه-و-چاشنی', 'مشاهده ادویه‌جات', 'hero', 2, 1, ?, ?)
                """,
                (b1["webp_path"], ts, ts, b2["webp_path"], ts, ts),
            )

        # 7. Sample Coupon
        if not conn.execute("SELECT 1 FROM coupons WHERE code = 'WELCOME10'").fetchone():
            conn.execute(
                """
                INSERT INTO coupons (code, title, discount_type, discount_value, min_order_rial, max_discount_rial, per_user_limit, is_active, created_at, updated_at)
                VALUES ('WELCOME10', 'تخفیف ۱۰ درصدی اولین خرید', 'percent', 10, 1000000, 3000000, 2, 1, ?, ?)
                """,
                (ts, ts),
            )

        # 8. Static Pages
        static_pages = [
            ("درباره ما", "about", "<p>فروشگاه آنلاین آرمین با هدف عرضه مستقیم برنج ایرانی، حبوبات دست‌چین، ادویه‌جات خالص و چای اصیل شمال فعالیت خود را آغاز کرده است.</p>", "standard", "[]"),
            ("تماس با ما", "contact", "<p>برای پشتیبانی سفارش‌ها و مشاوره خرید می‌توانید همه‌روزه با شماره پشتیبانی فروشگاه تماس بگیرید یا از طریق ایمیل با ما در ارتباط باشید.</p>", "standard", "[]"),
            (
                "سوالات متداول",
                "faq",
                "<p>پاسخ به پرسش‌های پرتکرار مشتریان درباره خرید، ارسال و ضمانت کیفیت:</p>",
                "faq",
                json.dumps(
                    [
                        {"q": "چطور از اصالت برنج و زعفران مطمئن شویم؟", "a": "تمامی محصولات با ضمانت ۷ روزه کیفیت و بازگشت وجه عرضه می‌شوند."},
                        {"q": "زمان ارسال سفارش‌ها چقدر است؟", "a": "سفارش‌ها طی ۱ تا ۳ روز کاری با بسته‌بندی ایمن ارسال می‌گردند."},
                        {"q": "آیا امکان خرید بالای ۱۰ کیلوگرم هم هست؟", "a": "بله، می‌توانید تعداد بسته‌ها را در سبد خرید افزایش دهید یا با پشتیبانی تماس بگیرید."},
                    ],
                    ensure_ascii=False,
                ),
            ),
            ("قوانین و مقررات", "terms", "<p>ثبت سفارش در فروشگاه آرمین به منزله پذیرش شرایط ارسال، نگهداری صحیح کالا و قوانین تجارت الکترونیک است.</p>", "standard", "[]"),
            ("حریم خصوصی", "privacy", "<p>اطلاعات تماس و نشانی شما صرفاً جهت پردازش و ارسال سفارش استفاده می‌شود و در اختیار اشخاص ثالث قرار نمی‌گیرد.</p>", "standard", "[]"),
            ("شرایط ارسال و مرجوعی", "shipping-returns", "<p>سفارش‌های بالای سقف تعیین‌شده شامل ارسال رایگان هستند. در صورت مغایرت یا نارضایتی کیفی تا ۷ روز امکان مرجوعی وجود دارد.</p>", "standard", "[]"),
        ]
        for title, slug, html, ptype, fjson in static_pages:
            if not conn.execute("SELECT 1 FROM pages WHERE slug = ?", (slug,)).fetchone():
                conn.execute(
                    """
                    INSERT INTO pages (title, slug, content_html, page_type, faq_json, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (title, slug, html, ptype, fjson, ts, ts),
                )

        conn.execute("COMMIT")
        print("Seed completed successfully.")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    run_seed()
