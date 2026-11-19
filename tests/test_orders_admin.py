"""تست‌های مدیریت سفارش، پرداخت، مشتری و گزارش‌های مالی."""
from __future__ import annotations

from io import BytesIO

import pytest

from app.extensions import db as _db
from app.models import Expense, Order, OrderItem, Payment, ProductVariant, User
from app.utils.dates import utcnow
from tests.conftest import add_to_cart, checkout, start_and_pay


def _create_manual_order(app, admin_client, variant_id, *, paid=True, quantity=2):
    data = {
        "variant_id[]": [str(variant_id)],
        "quantity[]": [str(quantity)],
        "customer_name": "مشتری تلفنی",
        "mobile": "09121112244",
        "email": "phone@example.com",
        "province": "تهران",
        "city": "تهران",
        "address": "خیابان تلفنی پلاک ۵",
        "postal_code": "1234567890",
        "note": "سفارش تلفنی",
        "shipping_cost": "40000",
        "payment_mode": "offline" if paid else "unpaid",
        "reference": "REF-100",
    }
    response = admin_client.post("/admin/orders/create", data=data)
    assert response.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        return order.order_number, order.id


def test_manual_order_with_offline_payment(app, admin_client, product):
    order_number, order_id = _create_manual_order(app, admin_client, product["variant_id"])
    with app.app_context():
        order = _db.session.get(Order, order_id)
        assert order.payment_status == "paid"
        assert order.status == "paid"
        assert order.total_amount == 2 * 200000 + 40000
        assert order.items[0].unit_cost_snapshot == 120000
        assert _db.session.get(ProductVariant, product["variant_id"]).stock_quantity == 8
        payment = _db.session.query(Payment).filter(Payment.order_id == order.id).one()
        assert payment.gateway == "manual" and payment.status == "successful"

    assert admin_client.get(f"/admin/orders/{order_number}").status_code == 200
    assert admin_client.get(f"/admin/orders/{order_number}/invoice").status_code == 200


def test_order_status_tracking_note_and_return(app, admin_client, product):
    order_number, order_id = _create_manual_order(app, admin_client, product["variant_id"], quantity=1)
    admin_client.post(f"/admin/orders/{order_number}/status", data={"status": "processing", "note": "آماده‌سازی"})
    with app.app_context():
        assert _db.session.get(Order, order_id).status == "processing"

    admin_client.post(f"/admin/orders/{order_number}/tracking", data={"tracking_code": "TRK-9"})
    admin_client.post(f"/admin/orders/{order_number}/shipping", data={"shipping_status": "shipped", "tracking_code": "TRK-9"})
    with app.app_context():
        order = _db.session.get(Order, order_id)
        assert order.tracking_code == "TRK-9"
        assert order.shipping_status == "shipped"

    admin_client.post(f"/admin/orders/{order_number}/note", data={"admin_note": "یادداشت داخلی"})
    with app.app_context():
        assert _db.session.get(Order, order_id).admin_note == "یادداشت داخلی"

    for status in ("ready_to_ship", "shipped", "delivered"):
        admin_client.post(f"/admin/orders/{order_number}/status", data={"status": status})
    with app.app_context():
        assert _db.session.get(Order, order_id).status == "delivered"
    admin_client.post(f"/admin/orders/{order_number}/return", data={"reason": "مرجوعی مشتری", "restock": "1"})
    with app.app_context():
        order = _db.session.get(Order, order_id)
        assert order.status == "returned"
        assert _db.session.get(ProductVariant, product["variant_id"]).stock_quantity == 10


def test_refund_and_cancel_rules(app, admin_client, product):
    order_number, order_id = _create_manual_order(app, admin_client, product["variant_id"], quantity=1)
    admin_client.post(f"/admin/orders/{order_number}/refund", data={"amount": "50000", "note": "بازپرداخت جزئی"})
    with app.app_context():
        order = _db.session.get(Order, order_id)
        assert order.refund_amount == 50000

    admin_client.post(f"/admin/orders/{order_number}/cancel", data={"reason": "انصراف"})
    with app.app_context():
        order = _db.session.get(Order, order_id)
        assert order.status == "cancelled"
        assert _db.session.get(ProductVariant, product["variant_id"]).stock_quantity == 10


def test_admin_orders_list_payments_and_export(app, admin_client, product):
    order_number, order_id = _create_manual_order(app, admin_client, product["variant_id"], quantity=1)
    assert admin_client.get("/admin/orders").status_code == 200
    assert admin_client.get("/admin/orders?status=paid").status_code == 200
    assert admin_client.get("/admin/orders?q=" + order_number).status_code == 200
    assert admin_client.get("/admin/orders/export/csv").status_code == 200
    assert admin_client.get("/admin/orders/export/excel").status_code == 200
    assert admin_client.get("/admin/orders/export/pdf").status_code == 200
    assert admin_client.get("/admin/payments").status_code == 200
    with app.app_context():
        payment_id = _db.session.query(Payment).filter(Payment.order_id == order_id).one().id
    detail = admin_client.get(f"/admin/payments/{payment_id}")
    assert detail.status_code == 200
    assert "مبلغ" in detail.get_data(as_text=True)


def test_reports_and_expenses(app, admin_client, product):
    _create_manual_order(app, admin_client, product["variant_id"], quantity=2)
    created = admin_client.post(
        "/admin/expenses/create",
        data={"title": "اجاره انبار", "category": "اجاره", "amount": "500000", "expense_date": "2026-01-01", "description": "ماه اول"},
    )
    assert created.status_code == 302
    with app.app_context():
        expense = _db.session.query(Expense).filter(Expense.title == "اجاره انبار").one()
        expense_id = expense.id

    for url in ("/admin/reports", "/admin/reports/profit", "/admin/reports/inventory", "/admin/expenses"):
        assert admin_client.get(url).status_code == 200

    from app.utils.dates import utcnow as _now

    today = _now().date().isoformat()
    for kind in ("sales", "profit", "inventory", "products", "expenses"):
        for fmt in ("excel", "csv", "pdf"):
            response = admin_client.get(f"/admin/reports/export/{kind}/{fmt}?from={today}&to={today}")
            assert response.status_code == 200, f"{kind}/{fmt}"
            assert response.data

    admin_client.post(f"/admin/expenses/{expense_id}/delete")
    with app.app_context():
        assert _db.session.get(Expense, expense_id) is None


def test_customer_management(app, admin_client):
    created = admin_client.post(
        "/admin/customers/create",
        data={"full_name": "مشتری جدید", "mobile": "09125556677", "email": "new@example.com",
              "password": "Customer@123", "is_verified": "1"},
    )
    assert created.status_code == 302
    with app.app_context():
        user = _db.session.query(User).filter(User.mobile == "09125556677").one()
        user_id = user.id
    assert admin_client.get(f"/admin/customers/{user_id}").status_code == 200
    admin_client.post(f"/admin/customers/{user_id}/toggle")
    with app.app_context():
        assert _db.session.get(User, user_id).is_active is False
    admin_client.post(f"/admin/customers/{user_id}/reset-password", data={"new_password": "Another@12345"})
    with app.app_context():
        assert _db.session.get(User, user_id).must_change_password is True
    assert admin_client.get("/admin/customers").status_code == 200
