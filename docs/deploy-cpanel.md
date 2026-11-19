# راهنمای نصب و راه‌اندازی روی cPanel (Passenger)

این راهنما برای نصب «آرمین استور» روی هاست اشتراکی cPanel که از **Setup Python App / Passenger** پشتیبانی می‌کند نوشته شده است. مسیرها با نام کاربری فرضی `USER` و دامنه‌ی `shop.example.com` نمایش داده می‌شوند؛ آن‌ها را با مقادیر واقعی خود جایگزین کنید.

> **خلاصه:** پروژه را خارج از `public_html` قرار دهید، یک Python App با startup file `passenger_wsgi.py` و entry point `application` بسازید، وابستگی‌ها را نصب کنید، سه پوشه‌ی داده را قابل نوشتن کنید، برنامه را Restart کنید و از `/setup/` نصب را کامل کنید.

---

## ۰) پیش‌نیازها

- cPanel با گزینه‌ی **Setup Python App** (برخی هاست‌ها آن را «Python App» یا «Application Manager» می‌نامند).
- **Python 3.9 تا 3.13** در بخش Python App (ترجیحاً 3.11).
- دامنه یا زیردامنه‌ی متصل به هاست و **SSL فعال** (Let’s Encrypt از cPanel کافی است).
- دسترسی به **File Manager** و **Terminal** (یا «Run Command»). اگر Terminal ندارید، از بخش «Run Pip Install» و ویرایشگر فایل cPanel استفاده کنید.

اگر هاست شما Passenger ندارد، این راهنما کافی نیست؛ برنامه را روی VPS با Gunicorn/uWSGI و Nginx اجرا کنید. فایل `passenger_wsgi.py` فقط مخصوص Passenger است.

---

## ۱) آپلود و استخراج فایل‌ها

1. فایل ZIP پروژه را از **File Manager → Upload** آپلود کنید.
2. آن را در مسیر **خارج از `public_html`** استخراج کنید؛ مثلاً:
   ```text
   /home/USER/armin_store
   ```
   قرار دادن کد در `public_html` امن نیست و می‌تواند فایل‌های داخلی را در معرض دسترسی قرار دهد.
3. مطمئن شوید ساختار به این شکل است:
   ```text
   /home/USER/armin_store/app.py
   /home/USER/armin_store/passenger_wsgi.py
   /home/USER/armin_store/app/
   /home/USER/armin_store/instance/
   /home/USER/armin_store/storage/
   /home/USER/armin_store/backups/
   ```
4. پوشه‌های `instance`، `storage` و `backups` باید وجود داشته باشند. اگر در ZIP خالی بودند، دستی بسازید:
   ```bash
   mkdir -p /home/USER/armin_store/{instance,storage/public,storage/private,backups}
   ```

> فایل `.gitignore` و پوشه‌های `tests/` برای اجرا لازم نیستند، اما نگه‌داشتن آن‌ها ضرری ندارد. فایل `.env.example` را حذف نکنید؛ مرجع تنظیمات است.

---

## ۲) ساخت برنامه‌ی پایتون در cPanel

در cPanel → **Setup Python App** → **Create Application**:

| فیلد | مقدار پیشنهادی |
|---|---|
| Python version | `3.11` (یا نزدیک‌ترین نسخه موجود) |
| Application root | `armin_store` (همان مسیر `/home/USER/armin_store`) |
| Application URL | دامنه یا زیردامنه، مثلاً `shop.example.com` |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

نکته‌ها:

- اگر دامنه‌ی اصلی را انتخاب می‌کنید، بهتر است یک **زیردامنه** مثل `shop.example.com` بسازید و همان را به Python App بدهید تا با سایت/فایل‌های موجود در `public_html` تداخل نکند.
- اگر Application URL را روی مسیر فرعی مثل `example.com/shop` گذاشتید، برای تولید لینک‌های صحیح متغیر `APPLICATION_ROOT=/shop` را هم اضافه کنید و همه‌ی لینک‌ها را با همان پیشوند تست کنید.
- cPanel ممکن است فایل `.htaccess` یا پیکربندی Passenger را خودکار ایجاد کند؛ آن را حذف نکنید.
- پس از ساخت، cPanel مسیر محیط مجازی را نشان می‌دهد؛ معمولاً:
  ```text
  /home/USER/virtualenv/armin_store/3.11
  ```

روش جایگزین (بدون wizard): اگر هاست شما فقط Passenger دارد، پوشه‌ی برنامه را با یک فایل `tmp/restart.txt` و پیکربندی دامنه به Passenger معرفی کنید، اما روش wizard ساده‌تر و مطمئن‌تر است.

---

## ۳) نصب وابستگی‌ها

### روش الف — دکمه Run Pip Install

در صفحه‌ی برنامه، فیلد **requirements.txt** را روی `armin_store/requirements.txt` تنظیم کنید و **Run Pip Install** را بزنید.

### روش ب — Terminal

```bash
source /home/USER/virtualenv/armin_store/3.11/bin/activate
cd /home/USER/armin_store
python -m pip install --upgrade pip
pip install -r requirements.txt
```

بررسی نصب:

```bash
python -c "import flask, sqlalchemy, openpyxl, reportlab, PIL; print('OK')"
```

اگر `Pillow` یا `reportlab` نصب نشوند، برنامه باز هم اجرا می‌شود (تصویر بهینه نمی‌شود و خروجی PDF کار نمی‌کند)، اما نصب کامل توصیه می‌شود.

---

## ۴) تنظیم متغیرهای محیطی

در cPanel → Setup Python App → بخش **Environment variables** (یا فایل `.env` در ریشه پروژه) مقادیر زیر را اضافه کنید. متغیرهای cPanel بر `.env` اولویت دارند.

| متغیر | مقدار | الزامی؟ |
|---|---|---|
| `FLASK_ENV` | `production` | بله |
| `SESSION_COOKIE_SECURE` | `1` | بله روی HTTPS |
| `PREFERRED_URL_SCHEME` | `https` | بله |
| `TRUST_PROXY_HEADERS` | `1` | بله |
| `MAX_CONTENT_LENGTH_MB` | `80` (یا سقف هاست) | توصیه‌شده |
| `LOG_LEVEL` | `INFO` | توصیه‌شده |
| `SECRET_KEY` | یک رشته‌ی تصادفی طولانی | اختیاری* |
| `SETUP_KEY` | کلید نصب دلخواه | اختیاری** |
| `DATABASE_PATH` | `/home/USER/armin_store/instance/store.db` | اختیاری |
| `INSTANCE_DIR` | `/home/USER/armin_store/instance` | اختیاری |
| `STORAGE_DIR` | `/home/USER/armin_store/storage` | اختیاری |
| `BACKUP_DIR` | `/home/USER/armin_store/backups` | اختیاری |
| `APPLICATION_ROOT` | `/shop` فقط در نصب زیرمسیر | اختیاری |

\* اگر `SECRET_KEY` را خالی بگذارید، برنامه یک کلید پایدار در `instance/secret_key.txt` با مجوز `600` می‌سازد. برای چند سرور/چند نسخه، کلید ثابت در env بهتر است.

\** اگر `SETUP_KEY` تنظیم شود، در صفحه‌ی نصب به‌جای خواندن `instance/install_key.txt` همان مقدار وارد می‌شود.

**نکنید:** `PORT` را روی cPanel تنظیم نکنید؛ Passenger خودش سوکت را مدیریت می‌کند. `DATABASE_URL` را هم فقط در صورت مهاجرت به دیتابیس دیگر تنظیم کنید.

پس از تغییر متغیرها، برنامه را **Restart** کنید.

---

## ۵) مجوزهای فایل و پوشه

مالک فایل‌ها باید کاربر cPanel باشد (همان کاربری که Passenger با آن اجرا می‌شود). از `chmod 777` استفاده نکنید.

```bash
cd /home/USER/armin_store

# مجوز پایه
find . -type d -exec chmod 755 {} \;
find . -type f -exec chmod 644 {} \;

# پوشه‌های نوشتنی
chmod -R u+rwX instance storage backups
find instance storage backups -type d -exec chmod 755 {} \;

# فایل‌های حساس (در صورت وجود)
chmod 600 instance/secret_key.txt instance/install_key.txt instance/.env 2>/dev/null
chmod 600 .env 2>/dev/null
```

بررسی نوشتن:

```bash
touch instance/.write_test && rm instance/.write_test
touch storage/public/.write_test && rm storage/public/.write_test
touch backups/.write_test && rm backups/.write_test
```

اگر خطای Permission denied گرفتید، مالکیت را با **File Manager → Permissions** یا پشتیبانی هاست اصلاح کنید.

---

## ۶) Restart و بررسی اولیه

```bash
mkdir -p /home/USER/armin_store/tmp
touch /home/USER/armin_store/tmp/restart.txt
```

سپس در مرورگر:

- `https://shop.example.com/health` → باید `{"status":"ok","installed":false,...}` برگرداند.
- `https://shop.example.com/setup/` → صفحه‌ی نصب.

اگر 503 گرفتید، لاگ‌ها را ببینید (بخش ۱۰). اگر 500 گرفتید، معمولاً وابستگی نصب‌نشده یا مجوز پوشه است.

---

## ۷) اجرای Setup Wizard

1. کلید نصب را بردارید:
   ```bash
   cat /home/USER/armin_store/instance/install_key.txt
   ```
   (اگر `SETUP_KEY` تنظیم کرده‌اید، همان را وارد کنید.)
2. در `/setup/` موارد زیر را پر کنید:
   - کلید نصب
   - نام فروشگاه و شعار
   - نام و موبایل مدیر کل
   - رمز عبور قوی (حداقل ۸ کاراکتر، ترکیب حروف/اعداد/نماد)
   - اختیاری: **نصب داده نمونه** (۶ محصول برای آشنایی) و **اجبار HTTPS**
3. **نصب** را بزنید. برنامه جداول، تنظیمات، نقش‌ها و حساب مدیر را می‌سازد.
4. به `/auth/login` هدایت می‌شوید؛ با موبایل و رمز مدیر وارد شوید.
5. وارد `/admin/` شوید.

پس از موفقیت:

```bash
rm -f /home/USER/armin_store/instance/install_key.txt
```

صفحه‌ی نصب با وجود `instance/installed.lock` و تنظیم `setup_completed` بسته می‌شود. برای نصب مجدد، فقط روی نسخه‌ی تازه (یا پس از بکاپ و حذف آگاهانه‌ی دیتابیس) عمل کنید.

---

## ۸) چک‌لیست پس از نصب

- [ ] **تنظیمات → اطلاعات فروشگاه**: نام، لوگو، تلفن، ایمیل، شبکه‌های اجتماعی.
- [ ] **تنظیمات → واحد پول**: تومان/ریال و ارقام فارسی.
- [ ] **تنظیمات → امنیت**: `force_https` روشن باشد.
- [ ] **روش ارسال**: حداقل یک روش ارسال فعال با هزینه‌ی استان/شهر بسازید (بدون آن تسویه‌ی سفارش کامل نمی‌شود).
- [ ] **دسته‌بندی و محصول**: نمونه‌ها را ویرایش یا غیرفعال کنید؛ محصول واقعی بسازید.
- [ ] **پرداخت**: ابتدا `bitpay_test_mode` را روشن و یک سفارش آزمایشی کامل ثبت کنید؛ سپس کلید واقعی را وارد و حالت آزمایشی را خاموش کنید.
- [ ] **پیامک**: کلید و الگوها را وارد و «ارسال آزمایشی» بگیرید.
- [ ] **مالیات/کرایه/کوپن**: در صورت نیاز فعال کنید.
- [ ] **بکاپ**: یک بکاپ دستی بگیرید و دانلود آن را بررسی کنید.
- [ ] **مرورگرها**: خرید مهمان و خرید کاربر، بازیابی رمز، تیکت و پیگیری سفارش را روی موبایل تست کنید.
- [ ] **حذف داده نمونه**: اگر بدون داده نمونه نصب کرده‌اید، این مورد لازم نیست؛ در غیر این صورت محصولات/دسته‌های نمونه را حذف یا غیرفعال کنید.

---

## ۹) HTTPS و دامنه

1. در cPanel → **SSL/TLS Status** گزینه‌ی **Run AutoSSL** را اجرا کنید.
2. در **Domains** مطمئن شوید دامنه به همین هاست اشاره می‌کند و SSL فعال است.
3. در تنظیمات فروشگاه `force_https` را روشن بگذارید (پیش‌فرض نصب هم روشن است).
4. یک بار `https://shop.example.com/` را باز کنید؛ اگر به `http` ریدایرکت شد، `SESSION_COOKIE_SECURE` و `PREFERRED_URL_SCHEME` را بررسی کنید.

در صورت استفاده از **Cloudflare**، حالت SSL را روی **Full (strict)** بگذارید و مطمئن شوید هدرهای پروکسی به درستی می‌رسند (`TRUST_PROXY_HEADERS=1`).

---

## ۱۰) عیب‌یابی

### لاگ‌ها

```bash
cd /home/USER/armin_store
ls -la instance/logs/
tail -n 100 instance/logs/app.log
tail -n 100 stderr.log 2>/dev/null
```

- `instance/logs/app.log`: خطاهای برنامه (با چرخش خودکار).
- `stderr.log` در ریشه‌ی برنامه: خطاهای Passenger/بارگذاری ماژول.
- در cPanel → **Errors** یا `~/logs/` هم لاگ دامنه را ببینید.

### 503 Service Unavailable

- Application root و startup file درست است؟
- محیط مجازی فعال و وابستگی‌ها نصب شده‌اند؟
- `passenger_wsgi.py` در ریشه‌ی Application root است؟
- Restart کرده‌اید؟
- نسخه‌ی پایتون برنامه با نسخه‌ی محیط مجازی یکی است؟

### 500 Internal Server Error

- `pip install -r requirements.txt` کامل اجرا شده؟
- مجوز نوشتن `instance/`، `storage/` و `backups/` وجود دارد؟
- `SECRET_KEY` یا `instance/` قابل خواندن است؟
- خطای دقیق در `instance/logs/app.log` چیست؟

### «Database is locked»

SQLite برای ترافیک هم‌زمان بالا مناسب نیست. برنامه WAL و `busy_timeout=20000` را فعال می‌کند، اما اگر فروشگاه پرترافیک است:

- عملیات سنگین/گزارش‌های هم‌زمان را کاهش دهید.
- بکاپ را در ساعات کم‌ترافیک بگیرید.
- در صورت نیاز به چند کاربر هم‌زمان زیاد، مهاجرت به PostgreSQL را در نظر بگیرید (کد از SQLAlchemy استفاده می‌کند؛ `DATABASE_URL` و درایور مربوطه لازم است).

### ورود می‌کنم ولی بلافاصله خارج می‌شوم

- روی HTTP، کوکی `Secure` ارسال نمی‌شود. یا HTTPS را فعال کنید یا موقتاً `SESSION_COOKIE_SECURE=0` بگذارید (فقط برای تست).
- ساعت سرور و منطقه‌ی زمانی را بررسی کنید.
- افزونه/فایروال کوکی‌ها را حذف نکند.

### خطای 400 Bad Request / CSRF

- فرم را دوباره بارگذاری کنید؛ توکن CSRF منقضی شده است.
- کوکی‌ها و نشست را در مرورگر مسدود نکنید.
- اگر پشت پروکسی معکوس با بازنویسی دامنه هستید، `TRUST_PROXY_HEADERS=1` و `PREFERRED_URL_SCHEME` را تنظیم کنید.

### تصویر یا فایل آپلودی نمایش داده نمی‌شود

```bash
ls -la storage/public/products/ | head
```

- مجوز پوشه‌ها `755` و فایل‌ها `644` باشد.
- مسیر ذخیره‌شده در دیتابیس با فایل واقعی بخواند.
- ماژول `Pillow` نصب باشد (برای اعتبارسنجی/بهینه‌سازی تصویر).
- محدودیت آپلود هاست (`upload_max_filesize`، `post_max_size` در PHP) روی Flask اثر ندارد؛ `MAX_CONTENT_LENGTH_MB` و سقف Passenger را بررسی کنید.

### صفحه نصب باز نمی‌شود

- وجود `instance/installed.lock` یعنی نصب قبلاً انجام شده است. آن را فقط در صورت نیاز واقعی به نصب مجدد حذف کنید (با بکاپ).
- اگر `FORCE_INSTALLED` در محیط تنظیم شده باشد، نصب رد می‌شود؛ آن را حذف کنید.

### پیامک/پرداخت خطا می‌دهد

- دسترسی خروجی سرور به `api.sms.ir` / `bitpay.ir` را بررسی کنید (فایروال هاست).
- کلید و مسیرها را با مستندات روز درگاه/سرویس مقایسه کنید؛ همه‌ی مسیرها در تنظیمات قابل ویرایش‌اند.
- ابتدا با حالت آزمایشی پرداخت و «ارسال آزمایشی» پیامک شروع کنید.
- آدرس callback باید عمومی، HTTPS و بدون احراز هویت باشد.

---

## ۱۱) به‌روزرسانی نسخه

1. بکاپ کامل: دیتابیس، `storage/` و `backups/` (از پنل یا cPanel).
2. حالت تعمیرات را در تنظیمات روشن کنید (اختیاری).
3. فایل‌های نسخه‌ی جدید را آپلود و جایگزین کنید؛ **`instance/`، `storage/`، `backups/` و `.env` را حذف نکنید**.
4. وابستگی‌ها:
   ```bash
   source /home/USER/virtualenv/armin_store/3.11/bin/activate
   cd /home/USER/armin_store
   pip install -r requirements.txt
   ```
5. مهاجرت ساختار دیتابیس (در صورت وجود تغییر):
   ```bash
   FLASK_APP=passenger_wsgi.py flask init-db
   ```
   این دستور ساخت جداول جدید/ایندکس‌ها را اعمال می‌کند و داده‌های موجود را حفظ می‌کند. خروجی `Applied migrations` را بررسی کنید.
6. Restart:
   ```bash
   mkdir -p tmp && touch tmp/restart.txt
   ```
7. `/health`، صفحه اصلی، ورود مدیر و یک سفارش آزمایشی را بررسی کنید.
8. حالت تعمیرات را خاموش کنید.

---

## ۱۲) نکات امنیتی و بهینه‌سازی

- کد را خارج از `public_html` نگه دارید؛ فقط دامنه از طریق Passenger به برنامه وصل شود.
- رمز مدیر و کارمندان قوی باشد؛ برای هر کارمند نقش حداقلی بسازید.
- `instance/install_key.txt` را بعد از نصب حذف کنید.
- فایل‌های حساس (`SECRET_KEY`، `.env`، دیتابیس، بکاپ‌ها) نباید از وب قابل دانلود باشند. اگر `storage/` داخل دامنه است، مطمئن شوید فایل‌های `private/` از طریق وب سرو نمی‌شوند (برنامه فقط با احراز هویت آن‌ها را می‌فرستد).
- در cPanel گزینه‌ی **Directory Listing** را غیرفعال کنید.
- بکاپ‌ها را دوره‌ای به فضای ابری/کامپیوتر شخصی منتقل کنید؛ وجود بکاپ روی همان هاست کافی نیست.
- وابستگی‌ها را به‌روز نگه دارید و پس از هر به‌روزرسانی، تست‌ها را اجرا کنید:
  ```bash
  cd /home/USER/armin_store
  pytest -q
  ```
  (تست‌ها به دیتابیس موقت نیاز دارند و به داده‌ی واقعی دست نمی‌زنند؛ اجرای آن‌ها روی هاست تولیدی توصیه نمی‌شود.)
- برای ترافیک بالا، SQLite را با PostgreSQL جایگزین کنید و Passenger را روی چند پردازش/سرور مقیاس دهید. فایل‌های آپلودی را در آن صورت به فضای اشتراکی منتقل کنید.

---

## ۱۳) پرسش‌های متداول

**آیا به cron نیاز دارم؟**
خیر. آزادسازی رزروهای منقضی و محاسبات گزارش به‌صورت تنبل و در زمان درخواست انجام می‌شود. برای پاکسازی/بکاپ خودکار می‌توانید در صورت تمایل از cron هاست استفاده کنید، اما اجباری نیست.

**چند فروشگاه می‌توانم روی یک هاست داشته باشم؟**
هر نسخه با `INSTANCE_DIR`، `STORAGE_DIR`، `BACKUP_DIR` و `DATABASE_PATH` جداگانه و یک Python App مستقل اجرا می‌شود. مسیرها را برای هر نسخه جدا تنظیم کنید.

**داده نمونه را چطور حذف کنم؟**
اگر بدون تیک «نصب داده نمونه» نصب کنید، هیچ داده‌ی نمونه‌ای ساخته نمی‌شود. در غیر این صورت محصولات و دسته‌های نمونه را از پنل حذف/غیرفعال کنید.

**آیا می‌توانم دیتابیس را به PostgreSQL منتقل کنم؟**
معماری بر SQLAlchemy است و اکثر کدها مستقل از دیتابیس‌اند؛ اما مهاجرت نیاز به نصب درایور، تنظیم `DATABASE_URL`، بازبینی چند کوئری SQLite-محور (مثل مصرف اتمیک کوپن) و تست کامل دارد. برای فروشگاه کوچک/متوسط SQLite انتخاب ساده و کم‌هزینه‌ای است.
