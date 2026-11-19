from __future__ import annotations

import datetime as dt
import threading
import time

import pytest

from app.db import create_connection
from app.services.inventory import (
    InsufficientStockError,
    adjust_variant_stock,
    expire_unpaid_orders,
    restock_order_exactly_once,
)
from app.services.orders import (
    OrderError,
    create_checkout_order,
    edit_order_admin,
    transition_order_status,
)


def test_concurrent_purchase_of_last_unit(app):
    """Two simultaneous checkout transactions competing for the last 1 unit: strictly one succeeds."""
    db_path = app.config["DATABASE_PATH"]
    setup_conn = create_connection(db_path)
    vrow = setup_conn.execute(
        "SELECT id, stock_quantity FROM product_variants WHERE sku = 'ARM-TEA-GRN-250'"
    ).fetchone()
    vid = int(vrow["id"])
    # Set stock strictly to 1
    setup_conn.execute("BEGIN IMMEDIATE")
    adjust_variant_stock(
        setup_conn, variant_id=vid, delta=1 - int(vrow["stock_quantity"]), reason="test_setup"
    )
    setup_conn.execute("COMMIT")
    setup_conn.close()

    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def attempt_buy(mobile: str):
        conn = create_connection(db_path)
        try:
            barrier.wait(timeout=5)
            order = create_checkout_order(
                conn,
                user_id=None,
                lines_input=[(vid, 1)],
                customer_name="خریدار آزمایشی",
                customer_mobile=mobile,
                customer_email="",
                shipping_province="تهران",
                shipping_city="تهران",
                shipping_address="خیابان ولیعصر پلاک ۱۰",
                shipping_postal_code="1234567890",
                payment_method="bitpay",
                clear_user_cart=False,
            )
            with lock:
                results.append(f"ok:{order['order_number']}")
        except InsufficientStockError:
            with lock:
                results.append("insufficient")
        finally:
            conn.close()

    t1 = threading.Thread(target=attempt_buy, args=("09120000001",))
    t2 = threading.Thread(target=attempt_buy, args=("09120000002",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 2
    assert sum(1 for r in results if r.startswith("ok:")) == 1
    assert sum(1 for r in results if r == "insufficient") == 1

    check_conn = create_connection(db_path)
    final_stock = check_conn.execute(
        "SELECT stock_quantity FROM product_variants WHERE id = ?", (vid,)
    ).fetchone()["stock_quantity"]
    check_conn.close()
    assert final_stock == 0


def test_restock_exactly_once_on_repeated_cancel_and_expire(app):
    db_path = app.config["DATABASE_PATH"]
    conn = create_connection(db_path)
    vrow = conn.execute(
        "SELECT id, stock_quantity FROM product_variants WHERE sku = 'ARM-RICE-TH-1K'"
    ).fetchone()
    vid = int(vrow["id"])
    initial_stock = int(vrow["stock_quantity"])

    order = create_checkout_order(
        conn,
        user_id=None,
        lines_input=[(vid, 3)],
        customer_name="مریم احمدی",
        customer_mobile="09123334455",
        customer_email="",
        shipping_province="تهران",
        shipping_city="تهران",
        shipping_address="خیابان انقلاب پلاک ۲۲",
        shipping_postal_code="1111111111",
        payment_method="bitpay",
        clear_user_cart=False,
    )
    oid = int(order["id"])
    after_reserve = conn.execute(
        "SELECT stock_quantity FROM product_variants WHERE id = ?", (vid,)
    ).fetchone()["stock_quantity"]
    assert after_reserve == initial_stock - 3

    # Cancel once
    ok1, _ = transition_order_status(
        conn, order_id=oid, new_status="cancelled", actor_user_id=None, note="لغو اول"
    )
    assert ok1 is True
    # Repeated restock attempt must be a no-op
    did_restock_again = restock_order_exactly_once(conn, order_id=oid, reason="duplicate_attempt")
    assert did_restock_again is False

    # Also test expire_unpaid_orders on an expired order
    order2 = create_checkout_order(
        conn,
        user_id=None,
        lines_input=[(vid, 2)],
        customer_name="حسین رضایی",
        customer_mobile="09123334456",
        customer_email="",
        shipping_province="تهران",
        shipping_city="تهران",
        shipping_address="خیابان آزادی پلاک ۵",
        shipping_postal_code="2222222222",
        payment_method="bitpay",
        clear_user_cart=False,
    )
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("UPDATE orders SET expires_at = ? WHERE id = ?", (past, order2["id"]))
    expired_count = expire_unpaid_orders(conn)
    assert expired_count == 1
    assert expire_unpaid_orders(conn) == 0  # running again does not double-restock

    final_stock = conn.execute(
        "SELECT stock_quantity FROM product_variants WHERE id = ?", (vid,)
    ).fetchone()["stock_quantity"]
    conn.close()
    assert final_stock == initial_stock


def test_admin_order_edit_adjusts_stock_and_validates_transitions(app):
    db_path = app.config["DATABASE_PATH"]
    conn = create_connection(db_path)
    v1 = conn.execute("SELECT id, stock_quantity FROM product_variants WHERE sku = 'ARM-RICE-TH-1K'").fetchone()
    v2 = conn.execute("SELECT id, stock_quantity FROM product_variants WHERE sku = 'ARM-SPC-TUR-250'").fetchone()
    v1_id, v1_start = int(v1["id"]), int(v1["stock_quantity"])
    v2_id, v2_start = int(v2["id"]), int(v2["stock_quantity"])

    order = create_checkout_order(
        conn,
        user_id=None,
        lines_input=[(v1_id, 4)],
        customer_name="سارا کریمی",
        customer_mobile="09124445566",
        customer_email="",
        shipping_province="اصفهان",
        shipping_city="اصفهان",
        shipping_address="چهارباغ پلاک ۸",
        shipping_postal_code="8111111111",
        payment_method="bitpay",
        clear_user_cart=False,
    )
    oid = int(order["id"])

    # Edit order: reduce v1 from 4 -> 1 (+3 restocked) and add v2 qty 5 (-5 deducted)
    edit_order_admin(
        conn,
        order_id=oid,
        admin_id=1,
        shipping_province="اصفهان",
        shipping_city="اصفهان",
        shipping_address="چهارباغ پلاک ۸",
        shipping_postal_code="8111111111",
        shipping_fee_rial=0,
        discount_rial=0,
        new_items_input=[(v1_id, 1), (v2_id, 5)],
        customer_note="",
        admin_note="ویرایش اقلام",
    )

    v1_after = conn.execute("SELECT stock_quantity FROM product_variants WHERE id = ?", (v1_id,)).fetchone()["stock_quantity"]
    v2_after = conn.execute("SELECT stock_quantity FROM product_variants WHERE id = ?", (v2_id,)).fetchone()["stock_quantity"]
    assert v1_after == v1_start - 1
    assert v2_after == v2_start - 5

    # Insufficient stock on edit must fail & roll back cleanly
    with pytest.raises(InsufficientStockError):
        edit_order_admin(
            conn,
            order_id=oid,
            admin_id=1,
            shipping_province="اصفهان",
            shipping_city="اصفهان",
            shipping_address="چهارباغ پلاک ۸",
            shipping_postal_code="8111111111",
            shipping_fee_rial=0,
            discount_rial=0,
            new_items_input=[(v1_id, 99999)],
            customer_note="",
            admin_note="",
        )

    # Order state machine: pending_payment -> delivered directly must fail
    ok_bad, _ = transition_order_status(conn, order_id=oid, new_status="delivered", actor_user_id=1)
    assert ok_bad is False
    ok_good, _ = transition_order_status(conn, order_id=oid, new_status="paid", actor_user_id=1)
    assert ok_good is True
    conn.close()
