"""تست‌های سرتاسری: نصب، صفحات فروشگاه، تسویه‌حساب مهمان و تحویل دیجیتال."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from app import create_app
from app.database import migrator, seed
from app.extensions import db as _db
from app.models import (
    DigitalAsset,
    InventoryMovement,
    InventoryReservation,
    Order,
    OrderItem,
    Payment,
    ProductVariant,
    Setting,
)
from app.services import digital_delivery_service, settings_service
from app.utils.dates import utcnow
from tests.conftest import add_to_cart, checkout, start_and_pay


def test_setup_wizard_installs_and_disables_itself(tmp_path):
    db_path = tmp_path / "setup.db"
    app = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "DATABASE_PATH": str(db_path),
            "INSTANCE_DIR": str(tmp_path / "instance"),
            "STORAGE_DIR": str(tmp_path / "storage"),
            "UPLOAD_PUBLIC_DIR": str(tmp_path / "storage" / "public"),
            "UPLOAD_PRIVATE_DIR": str(tmp_path / "storage" / "private"),
            "BACKUP_DIR": str(tmp_path / "backups"),
            "FORCE_INSTALLED": False,
            "WTF_CSRF_ENABLED": False,
            "TESTING": True,
            "SERVER_NAME": "localhost",
        },
    )
    (tmp_path / "instance").mkdir(parents=True, exist_ok=True)
    client = app.test_client()

    assert client.get("/").status_code == 302
    assert client.get("/setup/").status_code == 200
    key = (tmp_path / "instance" / "install_key.txt").read_text(encoding="utf-8").strip()

    bad = client.post("/setup/install", data={"install_key": "wrong", "store_name": "فروشگاه"})
    assert bad.status_code == 200

    response = client.post(
        "/setup/install",
        data={
            "install_key": key,
            "store_name": "فروشگاه آزمون",
            "store_slogan": "سریع و مطمئن",
            "currency_label": "تومان",
            "currency_unit": "toman",
            "admin_name": "مدیر نصب",
            "admin_mobile": "09129998877",
            "admin_password": "Strong@12345",
            "admin_password_confirm": "Strong@12345",
            "contact_email": "info@example.com",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")

    with app.app_context():
        assert settings_service.get_str("store_name") == "فروشگاه آزمون"
        assert _db.session.query(Order).count() == 0

    # نصب دوباره ممنوع است
    assert client.get("/setup/").status_code == 302
    assert client.post("/auth/login", data={"mobile": "09129998877", "password": "Strong@12345"}).status_code == 302


def test_public_pages_render(app, client, product, category):
    urls = [
        "/",
        "/products",
        f"/product/{product['slug']}",
        f"/category/{_category_slug(app)}",
        "/search?q=محصول",
        "/compare",
        "/blog",
        "/contact",
        "/track-order",
        "/cart/",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert "text/html" in response.headers["Content-Type"]


def _category_slug(app):
    from app.models import Category

    with app.app_context():
        return _db.session.query(Category).first().slug


def test_guest_checkout_with_coupon_and_shipping(app, client, product, shipping, coupon):
    client.post("/cart/coupon", data={"code": "TEST10"})  # سبد خالی: باید رد شود
    add_to_cart(client, product["variant_id"], 2)
    applied = client.post("/cart/coupon", data={"code": "TEST10"})
    assert applied.status_code == 302

    cart_page = client.get("/cart/")
    assert cart_page.status_code == 200
    assert "TEST10" in cart_page.get_data(as_text=True)

    response = checkout(client, shipping_method_id=shipping)
    assert response.status_code == 302 and "/payment/start/" in response.headers["Location"]

    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        order_number = order.order_number
        assert order.total_amount == 400000 - 40000 + 40000  # همه مبالغ عدد صحیح
        assert order.discount_amount == 40000
        assert order.shipping_cost == 40000
        assert isinstance(order.total_amount, int)

    paid, _token = start_and_pay(client, response)
    assert paid.status_code == 302
    assert "state=success" in paid.headers["Location"]

    result = client.get(paid.headers["Location"])
    assert result.status_code == 200

    with app.app_context():
        order = _db.session.query(Order).filter(Order.order_number == order_number).one()
        assert order.payment_status == "paid"
        assert order.status == "paid"
        variant = _db.session.get(ProductVariant, product["variant_id"])
        assert variant.stock_quantity == 8
        reservation = _db.session.query(InventoryReservation).filter(InventoryReservation.order_id == order.id).one()
        assert reservation.status == "consumed"
        assert _db.session.query(InventoryMovement).filter(
            InventoryMovement.movement_type == "sale", InventoryMovement.reference_id == order.id
        ).count() == 1
        payment = _db.session.query(Payment).filter(Payment.order_id == order.id).one()
        assert payment.status == "successful" and payment.amount == order.total_amount

    # پیگیری مهمان با شماره سفارش و موبایل
    tracked = client.post("/track-order", data={"order_number": order_number, "mobile": "09121112233"})
    assert tracked.status_code == 302
    assert client.get(tracked.headers["Location"]).status_code == 200


def test_duplicate_payment_callback_is_idempotent(app, client, product, shipping):
    add_to_cart(client, product["variant_id"], 1)
    response = checkout(client, shipping_method_id=shipping)
    paid, token = start_and_pay(client, response)
    assert paid.status_code == 302

    second = client.post(f"/payment/test/{token}/complete", data={"action": "success"})
    assert second.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        assert len(order.payments) == 1
        variant = _db.session.get(ProductVariant, product["variant_id"])
        assert variant.stock_quantity == 9
        assert _db.session.query(InventoryMovement).filter(InventoryMovement.movement_type == "sale").count() == 1


def test_failed_payment_releases_reservation(app, client, product, shipping):
    add_to_cart(client, product["variant_id"], 1)
    response = checkout(client, shipping_method_id=shipping)
    paid, _token = start_and_pay(client, response, action="fail")
    assert paid.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        assert order.payment_status == "failed"
        assert _db.session.query(InventoryReservation).filter(
            InventoryReservation.order_id == order.id, InventoryReservation.status == "released"
        ).count() == 1
        variant = _db.session.get(ProductVariant, product["variant_id"])
        assert variant.stock_quantity == 10


def _buy_digital(app, client, digital_product, shipping):
    add_to_cart(client, digital_product["variant_id"], 1)
    response = checkout(client, shipping_method_id=shipping)
    paid, _token = start_and_pay(client, response)
    assert paid.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        item = order.items[0]
        return order.order_number, item.id


def test_digital_delivery_requires_valid_signature(app, client, digital_product, shipping, mime_item=None):
    with app.app_context():
        asset = DigitalAsset(
            product_id=digital_product["product_id"],
            delivery_type="file",
            file_path="digital/test.pdf",
            download_limit=2,
            download_expiry_days=0,
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        _db.session.add(asset)
        _db.session.commit()
        path = Path(app.config["UPLOAD_PRIVATE_DIR"]) / "digital/test.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4\n% test")

    # سفارش مهمان: نیاز به حساب برای دانلود؛ ابتدا بدون ورود بررسی خطا
    add_to_cart(client, digital_product["variant_id"], 1)
    response = checkout(client, shipping_method_id=shipping)
    paid, _token = start_and_pay(client, response)
    assert paid.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        order_number, item_id = order.order_number, order.items[0].id

    assert client.get(f"/account/download/{item_id}?sig=wrong").status_code in {302, 401, 403}

    # با حساب کاربری و امضای صحیح
    client.post(
        "/auth/register",
        data={
            "full_name": "خریدار دیجیتال",
            "mobile": "09123334455",
            "password": "Digital@123",
            "password_confirm": "Digital@123",
        },
    )
    with app.app_context():
        order = _db.session.query(Order).filter(Order.order_number == order_number).one()
        from app.models import User

        buyer = _db.session.query(User).filter(User.mobile == "09123334455").one()
        order.user_id = buyer.id
        _db.session.commit()
        url = digital_delivery_service.download_url(order.items[0])

    response = client.get(url)
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")

    with app.app_context():
        order = _db.session.query(Order).filter(Order.order_number == order_number).one()
        token = digital_delivery_service.get_token(order.items[0])
        assert token.download_count == 1
