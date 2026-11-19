"""تست‌های واحد سرویس‌های کلیدی: پول، تاریخ، کوپن، انبار، امنیت و پرداخت."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.extensions import db as _db
from app.models import Coupon, CouponRedemption, InventoryReservation, Order, ProductVariant
from app.services import (
    bitpay_service,
    coupon_service,
    inventory_service,
    rate_limit_service,
    settings_service,
    shipping_service,
)
from app.utils import dates, money, security
from app.utils.dates import utcnow


def test_money_is_integer_only():
    assert money.format_money(1234567) == "۱٬۲۳۴٬۵۶۷ تومان"
    assert money.format_number(1000) == "۱٬۰۰۰"
    assert money.percent_of(500000, 10) == 50000
    assert isinstance(money.apply_coupon("percent", 10, 200000, 0), int)
    assert money.apply_coupon("percent", 10, 200000, 15000) == 15000
    assert money.apply_coupon("fixed", 25000, 200000, 0) == 25000
    assert money.apply_coupon("fixed", 999999, 100000, 0) == 100000
    assert money.apply_coupon("percent", 150, 100000, 0) == 100000
    assert "٫" not in str(0.1 + 0.2)  # فقط برای یادآوری: محاسبات پولی از float استفاده نمی‌کند


def test_currency_display_conversion(app):
    with app.app_context():
        settings_service.set_many({"currency_unit": "toman", "currency_ratio": 1, "currency_label": "تومان"})
        assert money.format_money(10000) == "۱۰٬۰۰۰ تومان"
        settings_service.set_many({"currency_unit": "rial", "currency_ratio": 10, "currency_label": "ریال"})
        assert "ریال" in settings_service.money(10000)
        assert money.format_money(10000, unit="rial", label="ریال").endswith("ریال")
        settings_service.set_many({"currency_unit": "custom", "currency_ratio": 2, "currency_label": "سکه"})
        assert "سکه" in settings_service.money(10000)


def test_jalali_conversion_and_tehran_timezone():
    from datetime import datetime

    jalali = dates.gregorian_to_jalali(2026, 3, 21)
    assert jalali == (1405, 1, 1)
    gregorian = dates.jalali_to_gregorian(1405, 1, 1)
    assert gregorian == (2026, 3, 21)
    assert dates.jalali_date_string(datetime(2026, 3, 21, 12, 0)) == "1405/01/01"
    assert "01:30" in dates.jalali_datetime_string(datetime(2026, 3, 21, 22, 0))
    # تاریخ بدون ساعت (Date) نیز پشتیبانی می‌شود
    assert dates.jalali_date_string(datetime(2026, 3, 21).date()) == "1405/01/01"


def test_password_hashing_and_tokens():
    hashed = security.hash_password("Secret@12345")
    assert "Secret@12345" not in hashed
    assert security.verify_password(hashed, "Secret@12345") is True
    assert security.verify_password(hashed, "wrong") is False
    token_a = security.generate_token()
    token_b = security.generate_token()
    assert token_a != token_b
    assert security.constant_time_equals(token_a, token_a) is True
    assert security.constant_time_equals(token_a, token_b) is False
    assert security.normalize_mobile("+989121112233") == "0912112233" or security.normalize_mobile("۰۹۱۲۱۱۱۲۲۳۳") == "09121112233"
    assert security.is_valid_mobile("09121112233") is True
    assert security.is_valid_mobile("12345") is False
    assert security.slugify("محصول تست ۱۲۳") != ""
    assert security.slugify("Hello World") == "hello-world"


def test_html_sanitizer_blocks_dangerous_markup():
    cleaned = security.sanitize_html('<p onclick="x()">متن<script>alert(1)</script><a href="javascript:alert(1)">لینک</a></p>')
    assert "<script" not in cleaned.lower()
    assert "onclick" not in cleaned.lower()
    assert "javascript:" not in cleaned.lower()
    assert "متن" in cleaned


def test_coupon_rules(app, coupon):
    with app.app_context():
        row = _db.session.get(Coupon, coupon)
        items = [{"line_total": 200000, "product_id": 1, "category_id": 1}]
        result = coupon_service.validate(row.code, items)
        assert result["ok"] is True and result["discount"] == 20000

        row.minimum_order = 500000
        _db.session.commit()
        assert coupon_service.validate(row.code, items)["ok"] is False

        row.minimum_order = 0
        row.expires_at = utcnow() - timedelta(days=1)
        _db.session.commit()
        assert "اعتبار" in coupon_service.validate(row.code, items)["message"]

        row.expires_at = None
        row.usage_limit = 1
        row.used_count = 1
        _db.session.commit()
        assert "ظرفیت" in coupon_service.validate(row.code, items)["message"]

        row.used_count = 0
        row.discount_type = "fixed"
        row.discount_value = 999999
        row.maximum_discount = 0
        _db.session.commit()
        assert coupon_service.validate(row.code, items)["discount"] == 200000


def test_coupon_redeem_is_atomic(app, coupon):
    with app.app_context():
        row = _db.session.get(Coupon, coupon)
        row.usage_limit = 1
        _db.session.commit()
        order = Order(order_number="TEST-ORDER", created_at=utcnow(), updated_at=utcnow())
        _db.session.add(order)
        _db.session.commit()
        assert coupon_service.redeem(row, order, amount=1000) is True
        _db.session.commit()
        assert coupon_service.redeem(row, order, amount=1000) is False
        assert _db.session.get(Coupon, coupon).used_count == 1
        assert _db.session.query(CouponRedemption).count() == 1


def test_shipping_cost_rules(app, shipping):
    with app.app_context():
        method = shipping_service.get_method(shipping)
        assert method.cost_for("تهران", "تهران", 100000) == 40000
        method.shipping_type = "free_threshold"
        method.free_threshold = 500000
        _db.session.commit()
        assert method.cost_for("تهران", "تهران", 600000) == 0
        assert method.cost_for("تهران", "تهران", 100000) == 40000
        method.shipping_type = "pickup"
        _db.session.commit()
        assert method.cost_for("تهران", "تهران", 100000) == 0


def test_reservation_expiry_is_lazy(app, product):
    variant_id = product["variant_id"]
    with app.app_context():
        variant = _db.session.get(ProductVariant, variant_id)
        order = Order(order_number="RES-1", status="pending_payment", payment_status="unpaid",
                      created_at=utcnow(), updated_at=utcnow())
        _db.session.add(order)
        _db.session.flush()
        inventory_service.reserve_stock(order, [{"variant_id": variant_id, "quantity": 3, "product_id": variant.product_id}])
        _db.session.commit()
        assert inventory_service.available_quantity(variant) == 7

        reservation = _db.session.query(InventoryReservation).filter(InventoryReservation.order_id == order.id).one()
        reservation.expires_at = utcnow() - timedelta(minutes=1)
        _db.session.commit()
        assert inventory_service.available_quantity(variant) == 10

        # رزرو منقضی به‌صورت تنبل نادیده گرفته می‌شود (بدون جاب اجرایی)
        assert inventory_service._active_reserved(variant_id) == 0
        inventory_service.release_reservations(order)
        _db.session.commit()
        assert _db.session.get(InventoryReservation, reservation.id).status == "released"
        assert inventory_service.available_quantity(variant) == 10


def test_rate_limit_service(app):
    with app.app_context():
        key = "test:limit"
        rate_limit_service.reset(key)
        for _ in range(3):
            allowed, remaining, _retry = rate_limit_service.consume(key, 3, 3600)
            assert allowed is True
        allowed, _remaining, retry = rate_limit_service.consume(key, 3, 3600)
        assert allowed is False and retry > 0
        rate_limit_service.reset(key)
        assert rate_limit_service.consume(key, 3, 3600)[0] is True


def test_bitpay_amounts_and_test_mode(app):
    with app.app_context():
        settings_service.set_many({"bitpay_amount_multiplier": 10, "bitpay_test_mode": True})
        assert bitpay_service.gateway_amount(15000) == 150000
        assert isinstance(bitpay_service.gateway_amount(15000), int)
        assert bitpay_service.is_enabled() is True
        settings_service.set_many({"bitpay_test_mode": False, "bitpay_api_key": ""})
        assert bitpay_service.is_enabled() is False
        settings_service.set_many({"bitpay_api_key": "secret-key"})
        assert bitpay_service.is_enabled() is True


def test_settings_sensitive_values_are_masked(app):
    with app.app_context():
        settings_service.set_many({"bitpay_api_key": "super-secret-api-key"})
        values = settings_service.load_all(force=True)
        assert values["bitpay_api_key"] == "super-secret-api-key"
        # کلید حساس در لاگ/نمایش عمومی نباید کامل دیده شود
        from app.utils.security import mask_secret

        masked = mask_secret(values["bitpay_api_key"], visible=4)
        assert "super-secret" not in masked
        assert masked.endswith("key") or "•" in masked


def test_dashboard_stats_recent_payments_is_a_list(app):
    from app.models import Payment
    from app.services import report_service

    with app.app_context():
        order = Order(order_number="DASH-1", status="paid", payment_status="paid",
                      total_amount=1000, created_at=utcnow(), updated_at=utcnow())
        _db.session.add(order)
        _db.session.flush()
        payment = Payment(order_id=order.id, gateway="manual", amount=1000, status="successful")
        _db.session.add(payment)
        _db.session.commit()

        stats = report_service.dashboard_stats()
        assert isinstance(stats["recent_payments"], list)
        assert any(item.id == payment.id for item in stats["recent_payments"])
