"""تنظیمات مشترک تست‌ها — بدون هیچ تماس واقعی با درگاه یا سرویس پیامک.

هر تست روی دیتابیس SQLite مستقل و پوشه‌های موقت اجرا می‌شود و پس از پایان
پاک می‌شود؛ هیچ آزمونی روی داده‌های واقعی یا سرویس بیرونی اثر ندارد.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app import create_app
from app.database import migrator, seed
from app.extensions import db as _db
from app.models import Category, Coupon, Product, ProductVariant, ShippingMethod
from app.services import settings_service
from app.utils.dates import utcnow

TEST_SETTINGS = {
    "bitpay_enabled": True,
    "payment_online_enabled": True,
    "bitpay_test_mode": True,
    "bitpay_amount_multiplier": 10,
    "otp_required_for_orders": False,
    "review_require_purchase": True,
    "allow_registration": True,
    "maintenance_mode": False,
    "smtp_enabled": False,
    "sms_enabled": False,
}


def make_app(tmp_path: Path):
    db_path = tmp_path / "test.db"
    config = {
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "DATABASE_PATH": str(db_path),
        "INSTANCE_DIR": str(tmp_path / "instance"),
        "STORAGE_DIR": str(tmp_path / "storage"),
        "UPLOAD_PUBLIC_DIR": str(tmp_path / "storage" / "public"),
        "UPLOAD_PRIVATE_DIR": str(tmp_path / "storage" / "private"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "FORCE_INSTALLED": True,
        "WTF_CSRF_ENABLED": False,
        "TESTING": True,
        "SERVER_NAME": "localhost",
        "MAIL_SUPPRESS_SEND": True,
    }
    app = create_app("testing", config)
    return app


@pytest.fixture()
def app(tmp_path):
    application = make_app(tmp_path)
    (tmp_path / "instance").mkdir(parents=True, exist_ok=True)
    with application.app_context():
        migrator.upgrade()
        seed.seed_all()
        settings_service.set_many(TEST_SETTINGS)
    yield application


@pytest.fixture()
def db(app):
    with app.app_context():
        yield _db


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def admin(app):
    with app.app_context():
        user = seed.create_admin("مدیر آزمون", "09120000000", "Admin@12345")
        return user.id


@pytest.fixture()
def admin_client(app, admin):
    c = app.test_client()
    resp = c.post("/auth/login", data={"mobile": "09120000000", "password": "Admin@12345"})
    assert resp.status_code == 302, "ورود مدیر در تست ناموفق بود"
    return c


@pytest.fixture()
def category(app):
    with app.app_context():
        cat = Category(name="دسته آزمون", slug="test-category", is_active=True, created_at=utcnow(), updated_at=utcnow())
        _db.session.add(cat)
        _db.session.commit()
        return cat.id


def _create_product(app, category_id, *, price=200000, cost=120000, stock=10, digital=False, name="محصول آزمون", sku="TEST-1"):
    with app.app_context():
        product = Product(
            name=name,
            slug=f"slug-{sku.lower()}",
            sku=sku,
            category_id=category_id,
            product_type="digital" if digital else "physical",
            base_price=price,
            base_cost=cost,
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        _db.session.add(product)
        _db.session.flush()
        variant = ProductVariant(
            product_id=product.id,
            sku=f"{sku}-V1",
            price=price,
            cost_price=cost,
            stock_quantity=stock,
            is_default=True,
            is_active=True,
            attributes_json={"رنگ": "مشکی"} if not digital else {},
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        _db.session.add(variant)
        _db.session.commit()
        return {"product_id": product.id, "variant_id": variant.id, "slug": product.slug, "sku": product.sku}


@pytest.fixture()
def product(app, category):
    return _create_product(app, category)


@pytest.fixture()
def digital_product(app, category):
    return _create_product(app, category, price=100000, cost=0, stock=0, digital=True, name="کتاب دیجیتال", sku="DIG-1")


@pytest.fixture()
def shipping(app):
    with app.app_context():
        method = ShippingMethod(
            name="پست آزمون",
            shipping_type="flat",
            base_cost=40000,
            free_threshold=0,
            configuration_json={},
            estimated_days="۲ روز",
            is_active=True,
            sort_order=1,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        _db.session.add(method)
        _db.session.commit()
        return method.id


@pytest.fixture()
def coupon(app):
    with app.app_context():
        row = Coupon(
            code="TEST10",
            discount_type="percent",
            discount_value=10,
            minimum_order=0,
            maximum_discount=0,
            usage_limit=10,
            per_user_limit=2,
            is_active=True,
            applies_to="all",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        _db.session.add(row)
        _db.session.commit()
        return row.id


@pytest.fixture()
def customer(app):
    c = app.test_client()
    response = c.post(
        "/auth/register",
        data={
            "full_name": "مشتری آزمون",
            "mobile": "09121112233",
            "password": "Customer@123",
            "password_confirm": "Customer@123",
            "email": "customer@example.com",
        },
    )
    assert response.status_code == 302
    return c


ADDRESS = {
    "recipient_name": "مشتری آزمون",
    "mobile": "09121112233",
    "province": "تهران",
    "city": "تهران",
    "full_address": "خیابان آزمون، پلاک ۱، واحد ۲",
    "postal_code": "1234567890",
    "national_code": "0012345678",
    "accept_terms": "1",
}


@pytest.fixture()
def address():
    return dict(ADDRESS)


@pytest.fixture()
def png_bytes():
    # PNG یک‌پیکسلی معتبر برای تست آپلود
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


@pytest.fixture()
def mp4_bytes():
    return b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64


def add_to_cart(client, variant_id, quantity=1):
    return client.post("/api/cart/add", json={"variant_id": variant_id, "quantity": quantity})


def checkout(client, address=None, shipping_method_id=None, **extra):
    data = dict(address or ADDRESS)
    if shipping_method_id:
        data["shipping_method_id"] = str(shipping_method_id)
    data.update(extra)
    return client.post("/checkout/", data=data)


def start_and_pay(client, checkout_response, action="success"):
    """از پاسخ ۳۰۲ تسویه‌حساب تا پرداخت کامل در حالت تست."""
    assert checkout_response.status_code == 302, "تسویه‌حساب انجام نشد"
    start = client.get(checkout_response.headers["Location"])
    assert start.status_code == 302
    gateway_url = start.headers["Location"]
    token = gateway_url.rstrip("/").split("/")[-1]
    page = client.get(gateway_url)
    assert page.status_code == 200
    return client.post(f"/payment/test/{token}/complete", data={"action": action}), token
