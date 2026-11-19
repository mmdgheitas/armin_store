# فروشگاه آنلاین آرمین (Armin Store)

فروشگاه اینترنتی کامل و آماده تولید با رابط کاربری **تمام‌فارسی و راست‌به‌چپ (`fa-IR`, RTL)** جهت عرضه مستقیم برنج ایرانی، حبوبات، ادویه‌جات، زعفران و چای شمال؛ پیاده‌سازی‌شده با **Flask**، **Jinja2**، **SQLite3 استاندارد (بدون ORM)** و **HTML5 / CSS3 / Vanilla ES6+**.

---

## ویژگی‌های کلیدی

- **بومی‌سازی کامل ایرانی**:
  - فونت خودمیزبان **Vazirmatn** (`v33.003` WOFF2)، نمایش اعداد فارسی، تاریخ **شمسی (جلالی)** در منطقه زمانی `Asia/Tehran` (`jdatetime`) و ذخیره زمان‌ها به صورت `UTC ISO-8601` در دیتابیس.
  - نگهداری تمامی مبالغ در دیتابیس و محاسبات به **ریال (`INTEGER`)** و نمایش به **تومان** در رابط کاربری و فاکتورها.
  - نرمال‌سازی خودکار حروف عربی/فارسی (`ي/ی`، `ك/ک`، نیم‌فاصله) و اعداد فارسی/عربی در جستجو، شماره موبایل و کد پستی.
  - لیست کامل ۳۱ استان و شهرهای ایران.
- **موجودی هم‌زمان‌امن (Concurrency-Safe Inventory)**:
  - تراکنش‌های `BEGIN IMMEDIATE`، به‌روزرسانی اتمیک شرطی (`WHERE stock_quantity + ? >= 0`)، محدودیت `CHECK (stock_quantity >= 0)`، دفترکل `stock_movements`، رزرو موقت سبد سفارش در انتظار پرداخت و بازگردانی دقیقاً یک‌باره (`stock_restocked`) در انقضا/لغو.
- **پرداخت آنلاین `Bitpay.ir` (با حالت `mock` / `test` / `production`)**:
  - پیاده‌سازی پشت رابط انتزاعی `PaymentGateway`، ارسال مبلغ به ریال، تایید سرور-به-سرور (`gateway-result-second` با `json=1`)، تطبیق دقیق `amount` و `factorId`، ثبت لاگ امن در جدول `payments` (بدون کلید API)، Callback کاملاً Idempotent و دکمه استعلام مجدد در پنل مدیر.
- **احراز هویت دوگانه و پیامک `SMS.ir`**:
  - ورود با **موبایل + کد تایید یک‌بارمصرف (OTP ۶ رقمی)** با انقضای ۲ دقیقه‌ای، محدودیت ۵ خطا، وقفه ۶۰ ثانیه‌ای ارسال مجدد، Rate Limit بر اساس IP و موبایل و ذخیره صرفاً به صورت هش HMAC.
  - پشتیبانی از افزودن و ورود با **ایمیل + رمز عبور** (`scrypt`) و ورود مدیران با پشتیبانی اختیاری از **۲FA پیامکی** و کنترل دسترسی **RBAC** (`super_admin`, `manager`, `order_operator`, `content_editor`).
  - صف پیامک در دیتابیس (`sms_queue`) و گزارش کامل در `sms_logs`؛ پیاده‌سازی پشت `SmsService` سازگار با API v1 سامانه `sms.ir`.
- **پنل مدیریت جامع**:
  - داشبورد با نمودار SVG فروش و شاخص‌های کلیدی، مدیریت کامل محصولات (تنوع وزنی/قیمتی، آپلود و برش WebP با Pillow، آپلود/امبد ویدیو، مشخصات فنی، برچسب‌ها، پیش‌نمایش SERP گوگل، ایجاد ریدایرکت ۳۰۱ خودکار هنگام تغییر اسلاگ)، مدیریت کامل سفارش‌ها (فیلتر، ثبت دستی سفارش تلفنی، ویرایش اقلام با تعدیل خودکار موجودی، تغییر وضعیت بر اساس ماشین وضعیت، فاکتور چاپی و خروجی CSV)، دسته‌بندی‌ها، انبار و گردش کالا، مشتریان، تایید دیدگاه‌ها، کدهای تخفیف، بنرها، صفحات ثابت، ریدایرکت‌ها، تنظیمات سایت/ارسال/پرداخت/پیامک، کاربران مدیر و لاگ ممیزی (`audit_logs`).
- **سئو فنی کامل**:
  - اسلاگ‌های فارسی یکتا، Canonical، کنترل `noindex` برای صفحات خصوصی/فیلترشده، OpenGraph/Twitter، داده‌های ساختاریافته JSON-LD (`Organization`, `WebSite`, `BreadcrumbList`, `Product`, `VideoObject`, `FAQPage`)، `/sitemap.xml` و `/robots.txt`.

---

## راه‌اندازی سریع در محیط توسعه

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# در صورت نیاز مقادیر .env را ویرایش کنید (پیش‌فرض PAYMENT_MODE=mock و SMS_MODE=mock است)

python scripts/seed.py
flask --app wsgi:app run --host 0.0.0.0 --port 5000
```

اطلاعات پیش‌فرض مدیر کل ایجادشده توسط `scripts/seed.py` (مقادیر قابل تنظیم از `.env`):
- آدرس پنل مدیریت: `/admin/login`
- نام کاربری: `admin`
- رمز عبور اولیه: `ChangeMe123!@#` *(پس از اولین ورود حتماً تغییر دهید)*

### اجرای تست‌های خودکار (`pytest`)

```bash
.venv/bin/pytest -v
```

تست‌ها شامل خرید هم‌زمان آخرین موجودی کالا، بازگردانی یک‌باره موجودی، ویرایش موجودی در سفارش مدیر، ماشین وضعیت سفارش‌ها، Idempotency درگاه پرداخت و تطبیق مبلغ، قوانین کد تخفیف و ارسال، قوانین OTP، ریدایرکت ۳۰۱ تغییر اسلاگ، سئو، RBAC و مسیر کامل خرید می‌باشند.

---

## نکات استقرار در محیط تولید (Production)

### ۱. متغیرهای محیطی (`.env`)
مقادیر زیر را پیش از اجرا در تولید تنظیم کنید:
- `FLASK_ENV=production`
- `SECRET_KEY` (رشته تصادفی امن حداقل ۳۲ کاراکتر)
- `SESSION_COOKIE_SECURE=1` (با HTTPS)
- `PAYMENT_MODE=production` (یا `test` برای سندباکس بیت‌پی) و `BITPAY_API_KEY`
- `SMS_MODE=production`، `SMSIR_API_KEY`، `SMSIR_LINE_NUMBER` و شناسه‌های قالب (`SMSIR_*_TEMPLATE_ID`) در پنل SMS.ir

> **توضیح یکپارچه‌سازی Bitpay.ir و SMS.ir**:
> - **Bitpay.ir**: از آدرس‌های رسمی `https://bitpay.ir/payment/gateway-send`، `gateway-{id_get}-get` و `gateway-result-second` (و در حالت تست `/payment-test/`) با پارامترهای فرم `api`, `amount` (ریال), `redirect`, `factorId`, `id_get`, `trans_id`, `json=1` استفاده می‌کند. تمامی متدها در `app/services/bitpay.py` متمرکز هستند.
> - **SMS.ir**: از API v1 (`https://api.sms.ir/v1` و هدر `x-api-key`) برای `/send/verify` و `/send/bulk` استفاده می‌کند. پارامترهای قالب تایید سریع در `SMS.ir` محدودیت ۲۵ کاراکتر دارند؛ در `app/services/smsir.py` ارسال الگو و خط اختصاصی به صورت مجزا و قابل پیکربندی پیاده‌سازی شده است.

### ۲. اجرای سرور با Gunicorn

```bash
.venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 --timeout 60 wsgi:app
```
*(نکته: با SQLite در حالت `WAL` استفاده از چند Worker مجاز است؛ در محیط تولید می‌توانید ۱ تا ۴ Worker هم‌زمان استفاده کنید.)*

### ۳. نمونه پیکربندی Nginx

```nginx
server {
    listen 80;
    server_name example.ir www.example.ir;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name example.ir www.example.ir;

    # گواهی SSL
    ssl_certificate /etc/letsencrypt/live/example.ir/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.ir/privkey.pem;

    client_max_body_size 30M;

    location /static/ {
        alias /srv/armin_store/app/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### ۴. زمان‌بندی وظایف پس‌زمینه (Cron / Systemd)

```cron
# هر ۱ دقیقه: انقضای سفارش‌های پرداخت‌نشده و بازگردانی موجودی
* * * * * cd /srv/armin_store && /srv/armin_store/.venv/bin/python scripts/expire_orders.py >> instance/logs/cron.log 2>&1

# هر ۱ دقیقه: ارسال صف پیامک‌ها (اگر پیامکی در حالت صف باقی مانده باشد)
* * * * * cd /srv/armin_store && /srv/armin_store/.venv/bin/python scripts/sms_worker.py >> instance/logs/cron.log 2>&1

# هر شب ساعت ۳: پشتیبان‌گیری آنلاین از SQLite و فایل‌های رسانه
0 3 * * * cd /srv/armin_store && /srv/armin_store/.venv/bin/python scripts/backup.py >> instance/logs/cron.log 2>&1
```

همچنین دستورات CLI فلاسک نیز در دسترس هستند:
```bash
flask --app wsgi:app expire-orders
flask --app wsgi:app process-sms
```
