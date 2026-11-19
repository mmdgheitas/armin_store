from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from typing import Any

from app.db import transaction
from app.repositories.orders import (
    get_coupon_by_code,
    get_order_by_id,
    get_order_by_number,
)
from app.repositories.settings import get_int_setting, get_setting
from app.services.bitpay import PaymentGateway
from app.services.cart import (
    calculate_shipping_rial,
    clear_cart,
    validate_and_calculate_coupon,
)
from app.services.inventory import (
    InsufficientStockError,
    adjust_variant_stock,
    reserve_order_stock,
    restock_order_exactly_once,
)
from app.services.smsir import send_admin_alert_sms, send_event_sms
from app.utils.persian import (
    format_toman,
    generate_order_number,
    now_utc_iso,
)

logger = logging.getLogger("armin_store.orders")

ORDER_STATUS_LABELS: dict[str, str] = {
    "pending_payment": "در انتظار پرداخت",
    "paid": "پرداخت شده",
    "processing": "در حال آماده‌سازی",
    "shipped": "ارسال شده",
    "delivered": "تحویل شده",
    "cancelled": "لغو شده",
    "refunded": "مسترد شده",
    "failed": "ناموفق / منقضی",
    "returned": "مرجوع شده",
}

PAYMENT_STATUS_LABELS: dict[str, str] = {
    "pending": "در انتظار پرداخت",
    "paid": "پرداخت موفق",
    "failed": "ناموفق",
    "refunded": "مسترد شده",
}

PAYMENT_METHOD_LABELS: dict[str, str] = {
    "bitpay": "درگاه پرداخت آنلاین (بیت‌پی)",
    "cod": "پرداخت در محل",
    "offline": "پرداخت کارت به کارت / حضوری",
    "pay_link": "لینک پرداخت پیامکی",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending_payment": {"paid", "processing", "cancelled", "failed"},
    "paid": {"processing", "shipped", "cancelled", "refunded"},
    "processing": {"shipped", "delivered", "cancelled", "refunded"},
    "shipped": {"delivered", "returned", "refunded"},
    "delivered": {"returned", "refunded"},
    "cancelled": set(),
    "refunded": set(),
    "failed": {"pending_payment", "paid"},
    "returned": {"refunded"},
}

LOCKED_FOR_EDIT_STATUSES = {"delivered", "cancelled", "refunded", "returned"}


class OrderError(Exception):
    pass


def _unique_order_number(conn: sqlite3.Connection) -> str:
    prefix = get_setting(conn, "order_number_prefix", "ARM")
    for _ in range(10):
        cand = generate_order_number(prefix)
        if not conn.execute(
            "SELECT 1 FROM orders WHERE order_number = ?", (cand,)
        ).fetchone():
            return cand
    return generate_order_number(prefix) + "X"


def create_checkout_order(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    lines_input: list[tuple[int, int]],
    customer_name: str,
    customer_mobile: str,
    customer_email: str,
    shipping_province: str,
    shipping_city: str,
    shipping_address: str,
    shipping_postal_code: str,
    coupon_code: str = "",
    payment_method: str = "bitpay",
    customer_note: str = "",
    clear_user_cart: bool = True,
) -> dict[str, Any]:
    if not lines_input:
        raise OrderError("سبد خرید شما خالی است.")

    if payment_method not in {"bitpay", "cod"}:
        raise OrderError("روش پرداخت انتخابی معتبر نیست.")

    timeout_min = max(5, get_int_setting(conn, "reservation_timeout_minutes", 20))
    now_dt = dt.datetime.now(dt.timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    expires_at = (
        (now_dt + dt.timedelta(minutes=timeout_min)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if payment_method == "bitpay"
        else None
    )

    with transaction(conn, immediate=True):
        # Load authoritative variant & product data inside IMMEDIATE tx
        resolved_lines: list[dict[str, Any]] = []
        subtotal_rial = 0
        for variant_id, qty in lines_input:
            if qty <= 0:
                raise OrderError("تعداد کالا باید بیشتر از صفر باشد.")
            vrow = conn.execute(
                """
                SELECT v.*, p.id AS p_id, p.title AS p_title, p.status AS p_status
                FROM product_variants v
                JOIN products p ON p.id = v.product_id
                WHERE v.id = ?
                """,
                (variant_id,),
            ).fetchone()
            if not vrow or vrow["p_status"] != "published" or not vrow["is_active"]:
                raise OrderError("یکی از کالاهای انتخابی دیگر برای فروش فعال نیست.")
            unit_price = int(vrow["price_rial"])
            line_total = unit_price * int(qty)
            subtotal_rial += line_total
            resolved_lines.append(
                {
                    "product_id": int(vrow["p_id"]),
                    "variant_id": int(vrow["id"]),
                    "product_title": vrow["p_title"],
                    "variant_label": vrow["label"],
                    "sku": vrow["sku"],
                    "unit_price_rial": unit_price,
                    "quantity": int(qty),
                    "line_total_rial": line_total,
                }
            )

        shipping_fee_rial, _, _ = calculate_shipping_rial(conn, subtotal_rial)

        discount_rial = 0
        applied_coupon = ""
        coupon_row = None
        if coupon_code:
            disc, _, err = validate_and_calculate_coupon(
                conn,
                code=coupon_code,
                subtotal_rial=subtotal_rial,
                user_id=user_id,
                mobile=customer_mobile,
            )
            if err:
                raise OrderError(err)
            discount_rial = disc
            applied_coupon = coupon_code.strip().upper()
            coupon_row = get_coupon_by_code(conn, applied_coupon)

        total_rial = max(0, subtotal_rial - discount_rial + shipping_fee_rial)
        order_number = _unique_order_number(conn)

        initial_status = "pending_payment" if payment_method == "bitpay" else "processing"
        cur = conn.execute(
            """
            INSERT INTO orders (
                order_number, user_id, customer_name, customer_mobile, customer_email,
                shipping_province, shipping_city, shipping_address, shipping_postal_code,
                subtotal_rial, shipping_fee_rial, discount_rial, total_rial, coupon_code,
                payment_method, payment_status, order_status, customer_note,
                stock_reserved, stock_restocked, expires_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, 0, ?, ?, ?)
            """,
            (
                order_number,
                user_id,
                customer_name.strip(),
                customer_mobile.strip(),
                customer_email.strip(),
                shipping_province.strip(),
                shipping_city.strip(),
                shipping_address.strip(),
                shipping_postal_code.strip(),
                subtotal_rial,
                shipping_fee_rial,
                discount_rial,
                total_rial,
                applied_coupon or None,
                payment_method,
                initial_status,
                customer_note.strip(),
                expires_at,
                now_iso,
                now_iso,
            ),
        )
        order_id = int(cur.lastrowid)

        for ln in resolved_lines:
            conn.execute(
                """
                INSERT INTO order_items
                    (order_id, product_id, variant_id, product_title, variant_label, sku, unit_price_rial, quantity, line_total_rial, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    ln["product_id"],
                    ln["variant_id"],
                    ln["product_title"],
                    ln["variant_label"],
                    ln["sku"],
                    ln["unit_price_rial"],
                    ln["quantity"],
                    ln["line_total_rial"],
                    now_iso,
                ),
            )

        reserve_order_stock(
            conn,
            order_id=order_id,
            lines=[(ln["variant_id"], ln["quantity"]) for ln in resolved_lines],
        )

        if coupon_row:
            conn.execute(
                "UPDATE coupons SET used_count = used_count + 1, updated_at = ? WHERE id = ?",
                (now_iso, coupon_row["id"]),
            )
            conn.execute(
                """
                INSERT INTO coupon_usages (coupon_id, user_id, mobile, order_id, discount_rial, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (coupon_row["id"], user_id, customer_mobile, order_id, discount_rial, now_iso),
            )

        conn.execute(
            """
            INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
            VALUES (?, NULL, ?, ?, ?, ?)
            """,
            (order_id, initial_status, "ثبت سفارش توسط مشتری", user_id, now_iso),
        )

    if clear_user_cart:
        clear_cart(conn, user_id=user_id)

    return dict(get_order_by_id(conn, order_id))


def initiate_gateway_payment(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    gateway: PaymentGateway,
    callback_url: str,
) -> tuple[bool, str, str]:
    """Initiates payment with gateway and stores id_get. Returns (ok, redirect_url_or_empty, error_msg)."""
    order = get_order_by_id(conn, order_id)
    if not order:
        return False, "", "سفارش یافت نشد."
    if order["payment_status"] == "paid":
        return False, "", "این سفارش قبلاً پرداخت شده است."
    if order["order_status"] in {"cancelled", "refunded", "delivered"}:
        return False, "", "امکان پرداخت برای این وضعیت سفارش وجود ندارد."

    # If expired/failed, verify if we need to re-reserve? Usually user retries from failed/pending
    if order["order_status"] == "failed" and order["stock_restocked"]:
        try:
            with transaction(conn, immediate=True):
                items = conn.execute(
                    "SELECT variant_id, quantity FROM order_items WHERE order_id = ?",
                    (order_id,),
                ).fetchall()
                reserve_order_stock(
                    conn,
                    order_id=order_id,
                    lines=[(int(it["variant_id"]), int(it["quantity"])) for it in items],
                )
                timeout_min = max(5, get_int_setting(conn, "reservation_timeout_minutes", 20))
                new_exp = (
                    dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=timeout_min)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                conn.execute(
                    "UPDATE orders SET order_status = 'pending_payment', payment_status = 'pending', expires_at = ?, updated_at = ? WHERE id = ?",
                    (new_exp, now_utc_iso(), order_id),
                )
        except InsufficientStockError as exc:
            return False, "", str(exc)

    res = gateway.send(
        amount_rial=int(order["total_rial"]),
        callback_url=callback_url,
        factor_id=order["order_number"],
        description=f"پرداخت سفارش {order['order_number']} - فروشگاه آرمین",
        customer_name=order["customer_name"],
        customer_email=order["customer_email"],
    )

    ts = now_utc_iso()
    conn.execute(
        """
        INSERT INTO payments
            (order_id, gateway, id_get, amount_rial, status, factor_id, error_code, error_message, raw_request, raw_response, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            gateway.name,
            res.id_get or None,
            int(order["total_rial"]),
            "redirected" if res.success else "failed",
            order["order_number"],
            res.error_code or None,
            res.error_message or None,
            res.raw_request,
            res.raw_response,
            ts,
            ts,
        ),
    )

    if not res.success:
        return False, "", res.error_message or "خطا در اتصال به درگاه پرداخت."
    return True, res.redirect_url, ""


def process_payment_callback(
    conn: sqlite3.Connection,
    *,
    gateway: PaymentGateway,
    id_get: str,
    trans_id: str,
) -> tuple[bool, dict[str, Any] | None, str]:
    """
    Idempotent server-side payment verification.
    Returns (success, order_dict, message).
    """
    if not id_get:
        return False, None, "اطلاعات بازگشتی از درگاه ناقص است."

    pay_row = conn.execute(
        "SELECT * FROM payments WHERE id_get = ? ORDER BY id DESC LIMIT 1",
        (str(id_get).strip(),),
    ).fetchone()
    if not pay_row:
        return False, None, "تراکنش پرداختی با این شناسه در سیستم یافت نشد."

    order = get_order_by_id(conn, int(pay_row["order_id"]))
    if not order:
        return False, None, "سفارش مرتبط با این تراکنش یافت نشد."

    # Idempotency: if already paid, do not re-verify or re-SMS or re-increment sold counts
    if order["payment_status"] == "paid" and pay_row["status"] == "verified":
        return True, dict(order), "پرداخت این سفارش قبلاً با موفقیت تایید شده است."

    if not trans_id or str(trans_id).strip() in {"0", "-1", ""}:
        return _mark_payment_failed(
            conn,
            order_id=int(order["id"]),
            payment_id=int(pay_row["id"]),
            trans_id=str(trans_id or ""),
            error_code="CANCELLED",
            error_message="پرداخت توسط کاربر لغو شد یا ناموفق بود.",
            raw_response="",
        )

    vres = gateway.verify(id_get=str(id_get).strip(), trans_id=str(trans_id).strip())
    ts = now_utc_iso()

    if not vres.success:
        return _mark_payment_failed(
            conn,
            order_id=int(order["id"]),
            payment_id=int(pay_row["id"]),
            trans_id=str(trans_id),
            error_code=vres.error_code,
            error_message=vres.error_message,
            raw_response=vres.raw_response,
        )

    # Verify amount & factorId when returned by gateway
    if vres.amount_rial and int(vres.amount_rial) != int(order["total_rial"]):
        conn.execute(
            """
            UPDATE payments
            SET status = 'amount_mismatch', trans_id = ?, error_code = 'AMOUNT_MISMATCH',
                error_message = ?, raw_request = ?, raw_response = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                str(trans_id),
                f"مغایرت مبلغ: درگاه={vres.amount_rial}، سفارش={order['total_rial']}",
                vres.raw_request,
                vres.raw_response,
                ts,
                pay_row["id"],
            ),
        )
        return _mark_payment_failed(
            conn,
            order_id=int(order["id"]),
            payment_id=int(pay_row["id"]),
            trans_id=str(trans_id),
            error_code="AMOUNT_MISMATCH",
            error_message="مبلغ پرداختی با مبلغ سفارش مطابقت ندارد.",
            raw_response=vres.raw_response,
            keep_payment_status=True,
        )

    if vres.factor_id and vres.factor_id != order["order_number"]:
        return _mark_payment_failed(
            conn,
            order_id=int(order["id"]),
            payment_id=int(pay_row["id"]),
            trans_id=str(trans_id),
            error_code="FACTOR_MISMATCH",
            error_message="شماره سفارش بازگشتی از درگاه مطابقت ندارد.",
            raw_response=vres.raw_response,
        )

    just_marked_paid = False
    with transaction(conn, immediate=True):
        # Atomic guard against concurrent duplicate callbacks
        cur = conn.execute(
            """
            UPDATE orders
            SET payment_status = 'paid',
                order_status = CASE WHEN order_status IN ('pending_payment', 'failed') THEN 'paid' ELSE order_status END,
                paid_at = COALESCE(paid_at, ?),
                updated_at = ?
            WHERE id = ? AND payment_status != 'paid'
            """,
            (ts, ts, order["id"]),
        )
        if cur.rowcount == 1:
            just_marked_paid = True
            # If stock had been restocked earlier (e.g. edge case), re-reserve
            fresh = get_order_by_id(conn, int(order["id"]))
            if fresh and fresh["stock_restocked"]:
                items = conn.execute(
                    "SELECT variant_id, quantity FROM order_items WHERE order_id = ?",
                    (order["id"],),
                ).fetchall()
                reserve_order_stock(
                    conn,
                    order_id=int(order["id"]),
                    lines=[(int(it["variant_id"]), int(it["quantity"])) for it in items],
                )

            conn.execute(
                """
                UPDATE payments
                SET status = 'verified', trans_id = ?, card_num = ?,
                    raw_request = ?, raw_response = ?, verified_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(trans_id),
                    vres.card_num,
                    vres.raw_request,
                    vres.raw_response,
                    ts,
                    ts,
                    pay_row["id"],
                ),
            )
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
                VALUES (?, ?, 'paid', ?, NULL, ?)
                """,
                (
                    order["id"],
                    order["order_status"],
                    f"تایید پرداخت آنلاین (کد پیگیری: {trans_id})",
                    ts,
                ),
            )
            # Increment total_sold on products
            items = conn.execute(
                "SELECT product_id, quantity FROM order_items WHERE order_id = ?",
                (order["id"],),
            ).fetchall()
            for it in items:
                conn.execute(
                    "UPDATE products SET total_sold = total_sold + ? WHERE id = ?",
                    (int(it["quantity"]), int(it["product_id"])),
                )

    if just_marked_paid:
        try:
            send_event_sms(
                conn,
                toggle_key="sms_event_order_paid_customer",
                template_setting_key="smsir_order_paid_template_id",
                recipient=order["customer_mobile"],
                event_type="order_paid_customer",
                params={"ORDER": order["order_number"], "NAME": order["customer_name"][:25]},
                fallback_text=f"سفارش {order['order_number']} با موفقیت ثبت و پرداخت شد. فروشگاه آرمین",
            )
            send_admin_alert_sms(
                conn,
                toggle_key="sms_event_new_order_admin",
                template_setting_key="smsir_admin_order_template_id",
                event_type="new_paid_order_admin",
                params={
                    "ORDER": order["order_number"],
                    "AMOUNT": str(int(order["total_rial"]) // 10),
                },
                fallback_text=f"سفارش جدید پرداخت شد: {order['order_number']} - مبلغ {format_toman(order['total_rial'])}",
            )
        except Exception as exc:
            logger.warning("Order paid SMS dispatch failed: %s", exc)

    updated_order = get_order_by_id(conn, int(order["id"]))
    return True, dict(updated_order), "پرداخت با موفقیت انجام و تایید شد."


def _mark_payment_failed(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    payment_id: int,
    trans_id: str,
    error_code: str,
    error_message: str,
    raw_response: str,
    keep_payment_status: bool = False,
) -> tuple[bool, dict[str, Any] | None, str]:
    ts = now_utc_iso()
    with transaction(conn, immediate=True):
        if not keep_payment_status:
            conn.execute(
                """
                UPDATE payments
                SET status = 'failed', trans_id = COALESCE(NULLIF(?, ''), trans_id),
                    error_code = ?, error_message = ?, raw_response = ?, updated_at = ?
                WHERE id = ? AND status != 'verified'
                """,
                (trans_id, error_code, error_message, raw_response, ts, payment_id),
            )
        cur = conn.execute(
            """
            UPDATE orders
            SET order_status = 'failed', payment_status = 'failed', updated_at = ?
            WHERE id = ? AND payment_status != 'paid' AND order_status = 'pending_payment'
            """,
            (ts, order_id),
        )
        if cur.rowcount == 1:
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
                VALUES (?, 'pending_payment', 'failed', ?, NULL, ?)
                """,
                (order_id, error_message, ts),
            )
            restock_order_exactly_once(
                conn,
                order_id=order_id,
                reason="payment_failed_restock",
                note="بازگشت موجودی به دلیل عدم موفقیت پرداخت",
            )
    order = get_order_by_id(conn, order_id)
    return False, dict(order) if order else None, error_message


def transition_order_status(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    new_status: str,
    actor_user_id: int | None,
    note: str = "",
    tracking_code: str | None = None,
    carrier_name: str | None = None,
    restock_on_refund_or_return: bool = True,
    refund_amount_rial: int | None = None,
    refund_reason: str = "",
) -> tuple[bool, str]:
    order = get_order_by_id(conn, order_id)
    if not order:
        return False, "سفارش یافت نشد."

    old_status = order["order_status"]
    if new_status != old_status:
        allowed = ALLOWED_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            return (
                False,
                f"تغییر وضعیت از «{ORDER_STATUS_LABELS.get(old_status, old_status)}» به «{ORDER_STATUS_LABELS.get(new_status, new_status)}» مجاز نیست.",
            )

    ts = now_utc_iso()
    with transaction(conn, immediate=True):
        fields = ["order_status = ?", "updated_at = ?"]
        params: list[Any] = [new_status, ts]

        if tracking_code is not None:
            fields.append("tracking_code = ?")
            params.append(tracking_code.strip())
        if carrier_name is not None:
            fields.append("carrier_name = ?")
            params.append(carrier_name.strip())

        if new_status == "paid":
            fields.append("payment_status = 'paid'")
            fields.append("paid_at = COALESCE(paid_at, ?)")
            params.append(ts)
        elif new_status == "refunded":
            fields.append("payment_status = 'refunded'")
            if refund_amount_rial is not None:
                fields.append("refund_amount_rial = ?")
                params.append(max(0, int(refund_amount_rial)))
            if refund_reason:
                fields.append("refund_reason = ?")
                params.append(refund_reason.strip())

        params.append(order_id)
        conn.execute(f"UPDATE orders SET {', '.join(fields)} WHERE id = ?", params)

        if new_status != old_status or note:
            conn.execute(
                """
                INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, old_status, new_status, note.strip(), actor_user_id, ts),
            )

        if new_status in {"cancelled", "failed"}:
            restock_order_exactly_once(
                conn,
                order_id=order_id,
                reason=f"order_{new_status}_restock",
                admin_id=actor_user_id,
                note=note or f"بازگشت موجودی ({ORDER_STATUS_LABELS.get(new_status, new_status)})",
            )
        elif new_status in {"refunded", "returned"} and restock_on_refund_or_return:
            restock_order_exactly_once(
                conn,
                order_id=order_id,
                reason=f"order_{new_status}_restock",
                admin_id=actor_user_id,
                note=note or f"بازگشت موجودی ({ORDER_STATUS_LABELS.get(new_status, new_status)})",
            )

    # Trigger customer SMS on status transitions
    if new_status != old_status and new_status in {"processing", "shipped", "delivered", "cancelled"}:
        try:
            final_order = get_order_by_id(conn, order_id)
            t_code = (final_order["tracking_code"] if final_order else "") or "—"
            send_event_sms(
                conn,
                toggle_key="sms_event_order_status_customer",
                template_setting_key="smsir_order_status_template_id",
                recipient=order["customer_mobile"],
                event_type=f"order_status_{new_status}",
                params={
                    "ORDER": order["order_number"],
                    "STATUS": ORDER_STATUS_LABELS.get(new_status, new_status)[:25],
                    "TRACK": t_code[:25],
                },
                fallback_text=(
                    f"وضعیت سفارش {order['order_number']} به «{ORDER_STATUS_LABELS.get(new_status, new_status)}» تغییر یافت."
                    + (f" کد رهگیری: {t_code}" if new_status == "shipped" and t_code != "—" else "")
                ),
            )
        except Exception as exc:
            logger.warning("Status change SMS error: %s", exc)

    return True, "وضعیت سفارش با موفقیت به‌روزرسانی شد."


def create_manual_order_admin(
    conn: sqlite3.Connection,
    *,
    admin_id: int,
    user_id: int | None,
    customer_name: str,
    customer_mobile: str,
    customer_email: str,
    shipping_province: str,
    shipping_city: str,
    shipping_address: str,
    shipping_postal_code: str,
    items_input: list[tuple[int, int]],
    shipping_fee_rial: int,
    discount_rial: int,
    payment_method: str,
    admin_note: str = "",
    customer_note: str = "",
) -> dict[str, Any]:
    if not items_input:
        raise OrderError("حداقل یک قلم کالا باید انتخاب شود.")
    if payment_method not in {"offline", "pay_link", "cod"}:
        raise OrderError("روش پرداخت دستی معتبر نیست.")

    now_iso = now_utc_iso()
    is_paid_offline = payment_method == "offline"
    order_status = "paid" if is_paid_offline else ("processing" if payment_method == "cod" else "pending_payment")
    payment_status = "paid" if is_paid_offline else "pending"

    with transaction(conn, immediate=True):
        resolved: list[dict[str, Any]] = []
        subtotal = 0
        for vid, qty in items_input:
            if qty <= 0:
                raise OrderError("تعداد باید بیشتر از صفر باشد.")
            vrow = conn.execute(
                """
                SELECT v.*, p.id AS p_id, p.title AS p_title
                FROM product_variants v JOIN products p ON p.id = v.product_id
                WHERE v.id = ?
                """,
                (vid,),
            ).fetchone()
            if not vrow:
                raise OrderError(f"تنوع کالا با شناسه {vid} یافت نشد.")
            unit_price = int(vrow["price_rial"])
            line_total = unit_price * int(qty)
            subtotal += line_total
            resolved.append(
                {
                    "product_id": int(vrow["p_id"]),
                    "variant_id": int(vrow["id"]),
                    "product_title": vrow["p_title"],
                    "variant_label": vrow["label"],
                    "sku": vrow["sku"],
                    "unit_price_rial": unit_price,
                    "quantity": int(qty),
                    "line_total_rial": line_total,
                }
            )

        total_rial = max(0, subtotal - max(0, discount_rial) + max(0, shipping_fee_rial))
        order_number = _unique_order_number(conn)

        cur = conn.execute(
            """
            INSERT INTO orders (
                order_number, user_id, customer_name, customer_mobile, customer_email,
                shipping_province, shipping_city, shipping_address, shipping_postal_code,
                subtotal_rial, shipping_fee_rial, discount_rial, total_rial,
                payment_method, payment_status, order_status, customer_note, admin_note,
                stock_reserved, stock_restocked, paid_at, is_manual, created_by_admin_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, 1, ?, ?, ?)
            """,
            (
                order_number,
                user_id,
                customer_name.strip(),
                customer_mobile.strip(),
                customer_email.strip(),
                shipping_province.strip(),
                shipping_city.strip(),
                shipping_address.strip(),
                shipping_postal_code.strip(),
                subtotal,
                max(0, shipping_fee_rial),
                max(0, discount_rial),
                total_rial,
                payment_method,
                payment_status,
                order_status,
                customer_note.strip(),
                admin_note.strip(),
                now_iso if is_paid_offline else None,
                admin_id,
                now_iso,
                now_iso,
            ),
        )
        order_id = int(cur.lastrowid)

        for ln in resolved:
            conn.execute(
                """
                INSERT INTO order_items
                    (order_id, product_id, variant_id, product_title, variant_label, sku, unit_price_rial, quantity, line_total_rial, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    ln["product_id"],
                    ln["variant_id"],
                    ln["product_title"],
                    ln["variant_label"],
                    ln["sku"],
                    ln["unit_price_rial"],
                    ln["quantity"],
                    ln["line_total_rial"],
                    now_iso,
                ),
            )

        reserve_order_stock(
            conn,
            order_id=order_id,
            lines=[(ln["variant_id"], ln["quantity"]) for ln in resolved],
            admin_id=admin_id,
        )
        conn.execute(
            """
            INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
            VALUES (?, NULL, ?, 'ثبت سفارش دستی توسط مدیر', ?, ?)
            """,
            (order_id, order_status, admin_id, now_iso),
        )

    return dict(get_order_by_id(conn, order_id))


def edit_order_admin(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    admin_id: int,
    shipping_province: str,
    shipping_city: str,
    shipping_address: str,
    shipping_postal_code: str,
    shipping_fee_rial: int,
    discount_rial: int,
    new_items_input: list[tuple[int, int]],
    customer_note: str,
    admin_note: str,
) -> dict[str, Any]:
    order = get_order_by_id(conn, order_id)
    if not order:
        raise OrderError("سفارش یافت نشد.")
    if order["order_status"] in LOCKED_FOR_EDIT_STATUSES:
        raise OrderError("سفارش‌های تحویل‌شده، لغوشده، مستردشده یا مرجوعی قابل ویرایش نیستند.")
    if not new_items_input:
        raise OrderError("سفارش باید حداقل یک قلم کالا داشته باشد.")

    # Aggregate requested quantities per variant_id
    desired_map: dict[int, int] = {}
    for vid, qty in new_items_input:
        if qty <= 0:
            raise OrderError("تعداد هر قلم باید حداقل ۱ باشد.")
        desired_map[int(vid)] = desired_map.get(int(vid), 0) + int(qty)

    ts = now_utc_iso()
    with transaction(conn, immediate=True):
        old_items = conn.execute(
            "SELECT * FROM order_items WHERE order_id = ?", (order_id,)
        ).fetchall()
        old_map: dict[int, sqlite3.Row] = {int(r["variant_id"]): r for r in old_items}

        # Apply inventory delta only if stock is currently reserved and not restocked
        adjust_stock = bool(order["stock_reserved"]) and not bool(order["stock_restocked"])

        if adjust_stock:
            all_vids = set(old_map.keys()) | set(desired_map.keys())
            # Restock removals/decreases first, then deduct additions/increases
            for vid in all_vids:
                old_qty = int(old_map[vid]["quantity"]) if vid in old_map else 0
                new_qty = desired_map.get(vid, 0)
                diff = new_qty - old_qty
                if diff < 0:
                    adjust_variant_stock(
                        conn,
                        variant_id=vid,
                        delta=abs(diff),
                        reason="admin_order_edit",
                        order_id=order_id,
                        admin_id=admin_id,
                        note=f"کاهش/حذف قلم در ویرایش سفارش {order['order_number']}",
                    )
            for vid in all_vids:
                old_qty = int(old_map[vid]["quantity"]) if vid in old_map else 0
                new_qty = desired_map.get(vid, 0)
                diff = new_qty - old_qty
                if diff > 0:
                    adjust_variant_stock(
                        conn,
                        variant_id=vid,
                        delta=-diff,
                        reason="admin_order_edit",
                        order_id=order_id,
                        admin_id=admin_id,
                        note=f"افزایش/افزودن قلم در ویرایش سفارش {order['order_number']}",
                    )

        # Rebuild order_items preserving unit_price snapshot for existing items where possible
        conn.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        subtotal = 0
        for vid, qty in desired_map.items():
            if vid in old_map:
                old_r = old_map[vid]
                product_id = int(old_r["product_id"])
                product_title = old_r["product_title"]
                variant_label = old_r["variant_label"]
                sku = old_r["sku"]
                unit_price = int(old_r["unit_price_rial"])
            else:
                vrow = conn.execute(
                    """
                    SELECT v.*, p.id AS p_id, p.title AS p_title
                    FROM product_variants v JOIN products p ON p.id = v.product_id
                    WHERE v.id = ?
                    """,
                    (vid,),
                ).fetchone()
                if not vrow:
                    raise OrderError(f"تنوع کالا با شناسه {vid} یافت نشد.")
                product_id = int(vrow["p_id"])
                product_title = vrow["p_title"]
                variant_label = vrow["label"]
                sku = vrow["sku"]
                unit_price = int(vrow["price_rial"])

            line_total = unit_price * qty
            subtotal += line_total
            conn.execute(
                """
                INSERT INTO order_items
                    (order_id, product_id, variant_id, product_title, variant_label, sku, unit_price_rial, quantity, line_total_rial, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    product_id,
                    vid,
                    product_title,
                    variant_label,
                    sku,
                    unit_price,
                    qty,
                    line_total,
                    ts,
                ),
            )

        total_rial = max(0, subtotal - max(0, discount_rial) + max(0, shipping_fee_rial))
        conn.execute(
            """
            UPDATE orders SET
                shipping_province = ?, shipping_city = ?, shipping_address = ?, shipping_postal_code = ?,
                subtotal_rial = ?, shipping_fee_rial = ?, discount_rial = ?, total_rial = ?,
                customer_note = ?, admin_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                shipping_province.strip(),
                shipping_city.strip(),
                shipping_address.strip(),
                shipping_postal_code.strip(),
                subtotal,
                max(0, shipping_fee_rial),
                max(0, discount_rial),
                total_rial,
                customer_note.strip(),
                admin_note.strip(),
                ts,
                order_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
            VALUES (?, ?, ?, 'ویرایش اقلام/مبالغ/آدرس سفارش توسط مدیر', ?, ?)
            """,
            (order_id, order["order_status"], order["order_status"], admin_id, ts),
        )

    return dict(get_order_by_id(conn, order_id))


def delete_order_super_admin(
    conn: sqlite3.Connection, *, order_id: int, admin_id: int
) -> bool:
    order = get_order_by_id(conn, order_id)
    if not order:
        return False
    with transaction(conn, immediate=True):
        restock_order_exactly_once(
            conn,
            order_id=order_id,
            reason="order_delete_restock",
            admin_id=admin_id,
            note=f"بازگشت موجودی پیش از حذف سفارش آزمایشی {order['order_number']}",
        )
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    return True
