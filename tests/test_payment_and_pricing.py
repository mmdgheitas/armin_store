from __future__ import annotations

from app.db import create_connection
from app.services.bitpay import BitpayGateway, GatewayVerifyResult, PaymentGateway
from app.services.cart import calculate_shipping_rial, validate_and_calculate_coupon
from app.services.orders import (
    create_checkout_order,
    initiate_gateway_payment,
    process_payment_callback,
)


class _StubMismatchGateway(PaymentGateway):
    name = "bitpay"

    def send(self, **kwargs):
        raise NotImplementedError

    def verify(self, *, id_get: str, trans_id: str) -> GatewayVerifyResult:
        return GatewayVerifyResult(
            success=True,
            amount_rial=1000,  # tampered/mismatched amount
            factor_id="WRONG-ORDER",
            trans_id=trans_id,
        )


def test_payment_callback_idempotency_and_amount_mismatch(app):
    db_path = app.config["DATABASE_PATH"]
    conn = create_connection(db_path)
    vrow = conn.execute("SELECT id FROM product_variants WHERE sku = 'ARM-RICE-TH-1K'").fetchone()
    vid = int(vrow["id"])

    order = create_checkout_order(
        conn,
        user_id=None,
        lines_input=[(vid, 2)],
        customer_name="رضا تهرانی",
        customer_mobile="09125556677",
        customer_email="",
        shipping_province="تهران",
        shipping_city="تهران",
        shipping_address="سعادت‌آباد",
        shipping_postal_code="1999999999",
        payment_method="bitpay",
        clear_user_cart=False,
    )
    oid = int(order["id"])

    with app.app_context():
        gw = BitpayGateway(api_key="", mode="mock", mock_pay_base_url="http://localhost:5000")
        ok, redirect_url, _ = initiate_gateway_payment(
            conn, order_id=oid, gateway=gw, callback_url="http://localhost:5000/payment/callback"
        )
        assert ok is True
        id_get = redirect_url.rsplit("/", 1)[-1]

        # First callback succeeds
        ok1, ord1, _ = process_payment_callback(conn, gateway=gw, id_get=id_get, trans_id="99887766")
        assert ok1 is True
        assert ord1["payment_status"] == "paid"

        sold_after_first = conn.execute(
            "SELECT total_sold FROM products WHERE id = (SELECT product_id FROM product_variants WHERE id = ?)",
            (vid,),
        ).fetchone()["total_sold"]
        sms_count_first = conn.execute("SELECT COUNT(*) FROM sms_logs").fetchone()[0]

        # Second duplicate callback must be idempotent
        ok2, ord2, _ = process_payment_callback(conn, gateway=gw, id_get=id_get, trans_id="99887766")
        assert ok2 is True
        assert ord2["payment_status"] == "paid"
        sold_after_second = conn.execute(
            "SELECT total_sold FROM products WHERE id = (SELECT product_id FROM product_variants WHERE id = ?)",
            (vid,),
        ).fetchone()["total_sold"]
        sms_count_second = conn.execute("SELECT COUNT(*) FROM sms_logs").fetchone()[0]
        assert sold_after_second == sold_after_first
        assert sms_count_second == sms_count_first

    # Amount mismatch test on a separate order
    order_m = create_checkout_order(
        conn,
        user_id=None,
        lines_input=[(vid, 1)],
        customer_name="نیما",
        customer_mobile="09126667788",
        customer_email="",
        shipping_province="تهران",
        shipping_city="تهران",
        shipping_address="ونک",
        shipping_postal_code="1988888888",
        payment_method="bitpay",
        clear_user_cart=False,
    )
    conn.execute(
        "INSERT INTO payments (order_id, gateway, id_get, amount_rial, status) VALUES (?, 'bitpay', '777777', ?, 'redirected')",
        (order_m["id"], order_m["total_rial"]),
    )
    with app.app_context():
        ok_m, ord_m, msg_m = process_payment_callback(
            conn, gateway=_StubMismatchGateway(), id_get="777777", trans_id="12345"
        )
        assert ok_m is False
        assert "مطابقت" in msg_m
        assert ord_m["order_status"] == "failed"
    conn.close()


def test_coupon_and_shipping_calculations(app):
    db_path = app.config["DATABASE_PATH"]
    conn = create_connection(db_path)

    # Shipping below threshold (15,000,000 Rial) charges 650,000 Rial; above threshold is free
    fee_low, enabled, _ = calculate_shipping_rial(conn, 5000000)
    assert enabled is True
    assert fee_low == 650000
    fee_free, _, _ = calculate_shipping_rial(conn, 15000000)
    assert fee_free == 0

    # WELCOME10: 10% capped at 3,000,000 Rial, min 1,000,000 Rial
    disc0, _, err0 = validate_and_calculate_coupon(conn, code="WELCOME10", subtotal_rial=500000)
    assert disc0 == 0 and err0 != ""

    disc1, _, err1 = validate_and_calculate_coupon(conn, code="WELCOME10", subtotal_rial=5000000)
    assert err1 == "" and disc1 == 500000

    disc_cap, _, err_cap = validate_and_calculate_coupon(conn, code="WELCOME10", subtotal_rial=50000000)
    assert err_cap == "" and disc_cap == 3000000
    conn.close()
