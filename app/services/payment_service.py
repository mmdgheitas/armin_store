"""
سرویس پرداخت: شروع پرداخت، Callback، اعتبارسنجی سمت سرور و Idempotency.

قواعد قطعی:
  * نمایش صفحه موفقیت به‌تنهایی دلیل موفقیت پرداخت نیست.
  * فقط پاسخ تأییدشده سرور درگاه با مبلغ منطبق، مجوز تکمیل سفارش است.
  * Callback تکراری موجب تکمیل یا کسر موجودی دوباره نمی‌شود.
"""
from __future__ import annotations

import json

from flask import current_app

from app.constants import PAYMENT_STATUS_LABELS
from app.extensions import db
from app.models import Order, OrderStatusHistory, Payment
from app.services import bitpay_service, inventory_service, order_service, settings_service
from app.utils.dates import utcnow


def gateway_available() -> tuple[bool, str]:
    if not settings_service.get_bool("payment_online_enabled", True):
        return False, "پرداخت آنلاین در حال حاضر غیرفعال است."
    if not settings_service.get_bool("bitpay_enabled", False):
        return False, "درگاه پرداخت هنوز پیکربندی نشده است."
    if not bitpay_service.is_enabled():
        return False, "کلید درگاه پرداخت ثبت نشده است."
    return True, ""


def start_payment(order: Order, callback_url: str) -> tuple[bool, str, str | None, Payment | None]:
    """
    آغاز پرداخت: رزرو موجودی، ساخت رکورد پرداخت و دریافت توکن درگاه.
    بازگشتی: (ok, message, redirect_url, payment)
    """
    if order is None:
        return False, "سفارش یافت نشد.", None, None
    if order.payment_status == "paid":
        return False, "این سفارش قبلاً پرداخت شده است.", None, None
    if not order.can_be_paid:
        return False, f"امکان پرداخت برای سفارشی با وضعیت «{order.status_label}» وجود ندارد.", None, None
    if order.total_amount <= 0:
        return False, "مبلغ سفارش نامعتبر است.", None, None

    available, message = gateway_available()
    if not available:
        return False, message, None, None

    # اطمینان از رزرو موجودی پیش از هدایت به درگاه
    if order.has_physical_items and not inventory_service.has_active_reservation(order):
        items = [
            {"variant_id": item.variant_id, "quantity": item.quantity, "product_id": item.product_id}
            for item in order.items
            if item.delivery_type == "physical" and item.variant_id
        ]
        try:
            db.session.commit()
            db.session.execute(db.text("BEGIN IMMEDIATE"))
            inventory_service.reserve_stock(order, items)
            db.session.commit()
        except inventory_service.StockError as exc:
            db.session.rollback()
            return False, exc.message, None, None
        except Exception:
            db.session.rollback()
            current_app.logger.exception("payment reservation failed")
            return False, "آماده‌سازی پرداخت انجام نشد. لطفاً دوباره تلاش کنید.", None, None

    payment = Payment(
        order_id=order.id,
        gateway="bitpay",
        amount=order.total_amount,
        currency=order.currency_code,
        status="initiated",
        is_test=bitpay_service.is_test_mode(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(payment)
    db.session.flush()

    result = bitpay_service.request_token(payment, order, callback_url)
    request_payload = {
        "gateway_amount": bitpay_service.gateway_amount(order.total_amount),
        "multiplier": bitpay_service.amount_multiplier(),
        "redirect_url": callback_url,
        "test_mode": bitpay_service.is_test_mode(),
    }
    payment.request_data = json.dumps(request_payload, ensure_ascii=False)[:4000]
    payment.response_data = json.dumps(result.raw, ensure_ascii=False)[:4000] if result.raw else None

    if not result.ok:
        payment.status = "failed"
        db.session.commit()
        return False, result.message or "درگاه پرداخت آماده نشد.", None, payment

    payment.authority_or_token = result.token
    payment.status = "pending"
    previous = order.payment_status
    order.payment_status = "initiated"
    order.updated_at = utcnow()
    db.session.add(
        OrderStatusHistory(
            order_id=order.id,
            field="payment_status",
            from_value=previous,
            to_value="initiated",
            note=f"درخواست پرداخت {order.total_amount}",
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return True, "در حال انتقال به درگاه پرداخت…", result.redirect_url, payment


def find_payment_by_callback(params: dict) -> Payment | None:
    """یافتن پرداخت از روی پارامترهای بازگشتی درگاه (بدون اعتماد به آن‌ها)."""
    candidates = [
        params.get("id_get"),
        params.get("idGet"),
        params.get("token"),
        params.get("trans_id"),
        params.get("transId"),
        params.get("factorId"),
    ]
    for candidate in candidates:
        value = (candidate or "").strip()
        if not value:
            continue
        payment = (
            db.session.query(Payment)
            .filter(Payment.authority_or_token == value)
            .order_by(Payment.created_at.desc())
            .first()
        )
        if payment is not None:
            return payment
    trans_id = (params.get("trans_id") or params.get("transId") or "").strip()
    if trans_id:
        return (
            db.session.query(Payment)
            .filter(Payment.transaction_id == trans_id)
            .order_by(Payment.created_at.desc())
            .first()
        )
    return None


def _set_order_payment_status(order: Order, status: str, note: str = "") -> None:
    previous = order.payment_status
    order.payment_status = status
    order.updated_at = utcnow()
    db.session.add(
        OrderStatusHistory(
            order_id=order.id,
            field="payment_status",
            from_value=previous,
            to_value=status,
            note=note[:400] or None,
            created_at=utcnow(),
        )
    )


def handle_callback(params: dict) -> dict:
    """
    پردازش بازگشت از درگاه.
    بازگشتی: {status: success|failed|unknown, order, payment, message}
    """
    result = {"status": "unknown", "order": None, "payment": None, "message": "وضعیت پرداخت نامشخص است."}
    payment = find_payment_by_callback(params)
    if payment is None:
        result["message"] = "تراکنش متناظر با این بازگشت یافت نشد."
        return result

    order = db.session.get(Order, payment.order_id)
    result["order"] = order
    result["payment"] = payment

    # Idempotency: پرداخت قبلاً تأیید شده است
    if payment.status == "successful" and order is not None and order.payment_status == "paid":
        result.update(
            status="success",
            message="این پرداخت قبلاً تأیید و سفارش تکمیل شده است.",
        )
        return result

    verification = bitpay_service.verify(payment, params)
    payment.verified_at = utcnow()
    if verification.get("raw"):
        payment.response_data = json.dumps(verification["raw"], ensure_ascii=False)[:4000]
    callback_safe = {
        key: (str(value)[:200] if value is not None else None)
        for key, value in (params or {}).items()
        if key in {"trans_id", "id_get", "status", "factorId", "amount", "sig"}
    }
    payment.callback_data = json.dumps(callback_safe, ensure_ascii=False)[:2000]

    if not verification.get("verified"):
        payment.status = "failed"
        if order is not None and order.payment_status not in {"paid", "refunded"}:
            _set_order_payment_status(order, "failed", verification.get("message") or "پرداخت تأیید نشد")
            inventory_service.release_reservations(
                order, status="released", reason="پرداخت ناموفق"
            )
        db.session.commit()
        result.update(status="failed", message=verification.get("message") or "پرداخت تأیید نشد.")
        return result

    # تطبیق مبلغ پرداخت‌شده با مبلغ سفارش (در واحد درگاه)
    expected = bitpay_service.gateway_amount(payment.amount)
    paid_amount = verification.get("amount")
    if paid_amount is not None and int(paid_amount) != int(expected):
        payment.status = "unknown"
        if order is not None:
            order.admin_note = (
                (order.admin_note or "")
                + f"\n[سیستم] مبلغ پرداخت‌شده ({paid_amount}) با مبلغ سفارش ({expected}) هم‌خوانی ندارد."
            ).strip()
            _set_order_payment_status(order, "unknown", "عدم تطابق مبلغ تراکنش")
            inventory_service.release_reservations(order, status="released", reason="عدم تطابق مبلغ پرداخت")
        db.session.commit()
        current_app.logger.error(
            "payment amount mismatch order=%s expected=%s got=%s",
            getattr(order, "order_number", "-"), expected, paid_amount,
        )
        result.update(status="unknown", message="مبلغ تراکنش با مبلغ سفارش هم‌خوانی ندارد. با پشتیبانی تماس بگیرید.")
        return result

    payment.status = "successful"
    payment.transaction_id = str(verification.get("trans_id") or "")[:120] or None
    payment.reference_code = str(verification.get("reference") or "")[:120] or None
    db.session.commit()

    if order is None:
        result.update(status="unknown", message="سفارش متناظر یافت نشد. با پشتیبانی تماس بگیرید.")
        return result

    if order_service.mark_paid(order, payment):
        db.session.refresh(order)
        result.update(status="success", message=settings_service.get_str("payment_success_note") or "پرداخت با موفقیت انجام شد.")
    else:
        result.update(
            status="unknown",
            message="پرداخت شما ثبت شد اما تکمیل سفارش نیاز به بررسی دارد. با پشتیبانی تماس بگیرید.",
        )
    return result


def payment_result_message(status: str) -> str:
    return {
        "success": "پرداخت شما با موفقیت تأیید شد.",
        "failed": "پرداخت انجام نشد یا توسط شما لغو شد.",
        "unknown": "نتیجه پرداخت مشخص نیست. مبلغ در صورت کسر، حداکثر تا ۷۲ ساعت بازگشت داده می‌شود.",
    }.get(status, "وضعیت پرداخت نامشخص است.")


def status_label(status: str) -> str:
    return PAYMENT_STATUS_LABELS.get(status, status)


def complete_free_order(order: Order) -> bool:
    """تکمیل سفارش با مبلغ صفر (مثلاً سفارش کاملاً تخفیف‌گرفته)."""
    if order is None or order.total_amount > 0:
        return False
    payment = Payment(
        order_id=order.id,
        gateway="free",
        amount=0,
        currency=order.currency_code,
        status="successful",
        is_test=False,
        verified_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
        response_data='{"note": "سفارش با مبلغ نهایی صفر"}',
    )
    db.session.add(payment)
    db.session.commit()
    return order_service.mark_paid(order, payment)
