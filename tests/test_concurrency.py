"""تست‌های همزمانی روی SQLite واقعی (فایل) — بدون Mock در منطق کسب‌وکار.

سناریوها: آخرین موجودی، Callback تکراری همزمان، ظرفیت کوپن و اصلاح همزمان انبار.
"""
from __future__ import annotations

import threading

import pytest

from app import create_app
from app.extensions import db as _db
from app.models import (
    Coupon,
    InventoryMovement,
    InventoryReservation,
    Order,
    Payment,
    ProductVariant,
    ShippingMethod,
)
from app.services import settings_service
from app.utils.dates import utcnow
from tests.conftest import ADDRESS


def _thread_app(app):
    cfg = {
        "SQLALCHEMY_DATABASE_URI": app.config["SQLALCHEMY_DATABASE_URI"],
        "DATABASE_PATH": app.config["DATABASE_PATH"],
        "INSTANCE_DIR": app.config["INSTANCE_DIR"],
        "STORAGE_DIR": app.config["STORAGE_DIR"],
        "UPLOAD_PUBLIC_DIR": app.config["UPLOAD_PUBLIC_DIR"],
        "UPLOAD_PRIVATE_DIR": app.config["UPLOAD_PRIVATE_DIR"],
        "BACKUP_DIR": app.config["BACKUP_DIR"],
        "FORCE_INSTALLED": True,
        "WTF_CSRF_ENABLED": False,
        "TESTING": True,
        "SERVER_NAME": "localhost",
    }
    return create_app("testing", cfg)


def _run_threads(app, workers):
    barrier = threading.Barrier(len(workers))
    results: list = [None] * len(workers)
    errors: list = [None] * len(workers)

    def runner(index, func):
        try:
            thread_app = _thread_app(app)
            client = thread_app.test_client()
            with thread_app.app_context():
                required = func.__globals__.get("REQUIRED_IDS")
            barrier.wait(timeout=30)
            results[index] = func(client)
        except Exception as exc:  # pragma: no cover - برای گزارش خطای تست
            errors[index] = exc

    threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(errors), [str(e) for e in errors if e]
    return results


def _buyer(variant_id, shipping_id, coupon=None):
    def worker(client):
        client.post("/api/cart/add", json={"variant_id": variant_id, "quantity": 1})
        if coupon:
            client.post("/api/cart/coupon", json={"code": coupon})
        response = client.post(
            "/checkout/",
            data={**ADDRESS, "shipping_method_id": str(shipping_id), "accept_terms": "1"},
        )
        return response.status_code, response.headers.get("Location", "")

    return worker


@pytest.fixture()
def concurrent_product(app, category, shipping):
    from app.models import Product

    with app.app_context():
        product = Product(
            name="کالای همزمانی", slug="concurrent-product", sku="CONC-1", category_id=category,
            product_type="physical", base_price=100000, base_cost=60000, is_active=True,
            created_at=utcnow(), updated_at=utcnow(),
        )
        _db.session.add(product)
        _db.session.flush()
        variant = ProductVariant(
            product_id=product.id, sku="CONC-V1", price=100000, cost_price=60000,
            stock_quantity=1, is_default=True, is_active=True, attributes_json={},
            created_at=utcnow(), updated_at=utcnow(),
        )
        _db.session.add(variant)
        _db.session.commit()
        return product.id, variant.id


def test_only_one_buyer_gets_last_item(app, concurrent_product, shipping):
    _product_id, variant_id = concurrent_product
    results = _run_threads(app, [_buyer(variant_id, shipping), _buyer(variant_id, shipping)])
    codes = [status for status, _ in results]
    assert sorted(codes) == [200, 302], f"نتیجه غیرمنتظره: {results}"
    with app.app_context():
        variant = _db.session.get(ProductVariant, variant_id)
        assert variant.stock_quantity == 1
        active = _db.session.query(InventoryReservation).filter(
            InventoryReservation.variant_id == variant_id, InventoryReservation.status == "active"
        ).count()
        assert active == 1
        assert _db.session.query(Order).count() == 1


def test_duplicate_callbacks_do_not_double_charge(app, concurrent_product, shipping):
    _product_id, variant_id = concurrent_product
    with app.app_context():
        variant = _db.session.get(ProductVariant, variant_id)
        variant.stock_quantity = 5
        _db.session.commit()

    client = app.test_client()
    client.post("/api/cart/add", json={"variant_id": variant_id, "quantity": 2})
    checkout_response = client.post(
        "/checkout/", data={**ADDRESS, "shipping_method_id": str(shipping), "accept_terms": "1"}
    )
    assert checkout_response.status_code == 302
    gateway = client.get(checkout_response.headers["Location"])
    token = gateway.headers["Location"].rstrip("/").split("/")[-1]

    def callback(thread_client):
        response = thread_client.post(f"/payment/test/{token}/complete", data={"action": "success"})
        return response.status_code

    _run_threads(app, [callback, callback])
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        assert order.payment_status == "paid"
        assert _db.session.query(Payment).filter(Payment.order_id == order.id).count() == 1
        assert _db.session.get(ProductVariant, variant_id).stock_quantity == 3
        sales = _db.session.query(InventoryMovement).filter(
            InventoryMovement.movement_type == "sale", InventoryMovement.reference_id == order.id
        ).count()
        assert sales == 1


def test_coupon_capacity_is_not_exceeded(app, concurrent_product, shipping):
    _product_id, variant_id = concurrent_product
    with app.app_context():
        variant = _db.session.get(ProductVariant, variant_id)
        variant.stock_quantity = 10
        coupon = Coupon(
            code="ONCEONLY", discount_type="fixed", discount_value=10000, minimum_order=0,
            maximum_discount=0, usage_limit=1, per_user_limit=1, is_active=True, applies_to="all",
            created_at=utcnow(), updated_at=utcnow(),
        )
        _db.session.add(coupon)
        _db.session.commit()

    results = _run_threads(app, [_buyer(variant_id, shipping, "ONCEONLY"), _buyer(variant_id, shipping, "ONCEONLY")])
    assert any(status == 302 for status, _ in results)
    with app.app_context():
        coupon = _db.session.query(Coupon).filter(Coupon.code == "ONCEONLY").one()
        assert coupon.used_count == 1
        orders = _db.session.query(Order).all()
        discounted = [order for order in orders if order.discount_amount > 0]
        assert len(discounted) == 1
        assert sum(order.discount_amount for order in orders) == 10000


def test_concurrent_admin_stock_edits(app, concurrent_product, admin):
    _product_id, variant_id = concurrent_product

    def editor(value):
        def worker(client):
            client.post("/auth/login", data={"mobile": "09120000000", "password": "Admin@12345"})
            response = client.post(
                "/admin/inventory/adjust",
                data={
                    "variant_id": str(variant_id), "movement_type": "adjust", "quantity": str(value),
                    "absolute": "1", "reason": "اصلاح همزمان انبار",
                },
            )
            return response.status_code

        return worker

    results = _run_threads(app, [editor(5), editor(7)])
    assert results == [302, 302]
    with app.app_context():
        variant = _db.session.get(ProductVariant, variant_id)
        assert variant.stock_quantity in {5, 7}
        movements = _db.session.query(InventoryMovement).filter(
            InventoryMovement.variant_id == variant_id, InventoryMovement.reason == "اصلاح همزمان انبار"
        ).all()
        assert len(movements) == 2
        assert all(m.new_stock >= 0 for m in movements)
