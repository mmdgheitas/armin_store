"""پرداخت: شروع، بازگشت از درگاه و صفحه نتیجه."""
from __future__ import annotations

import secrets
import time

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.extensions import db
from app.models import Payment
from app.security import csrf_exempt
from app.services import bitpay_service, order_service, payment_service, settings_service

payment_bp = Blueprint("payment", __name__, url_prefix="/payment")


def _accessible_order(order_number: str):
    order = order_service.find_by_number(order_number)
    if order is None:
        abort(404)
    if not order_service.can_access(order, getattr(g, "user", None)):
        flash("برای مشاهده این سفارش، مالکیت خود را تأیید کنید.", "warning")
        return None, redirect(url_for("storefront.track_order", order=order_number))
    return order, None


@payment_bp.get("/start/<order_number>")
def start(order_number: str):
    order, response = _accessible_order(order_number)
    if response is not None:
        return response
    if order.payment_status == "paid":
        flash("این سفارش قبلاً پرداخت شده است.", "info")
        return redirect(url_for("payment.result", order_number=order.order_number))

    callback_url = url_for("payment.callback", _external=True)
    ok, message, redirect_url, payment = payment_service.start_payment(order, callback_url)
    if ok and redirect_url:
        return redirect(redirect_url)
    flash(message, "error")
    return redirect(url_for("payment.result", order_number=order.order_number))


@payment_bp.route("/callback", methods=["GET", "POST"])
@csrf_exempt
def callback():
    """بازگشت از درگاه. اعتبار همه پارامترها سمت سرور بررسی می‌شود."""
    params = request.values.to_dict()
    result = payment_service.handle_callback(params)
    order = result.get("order")
    if order is None:
        return render_template(
            "payment/unknown.html",
            message=result.get("message") or "نتیجه پرداخت نامشخص است.",
        ), 200
    order_service.grant_guest_access(order)
    session[f"payment_attempt_{order.order_number}"] = result.get("status")
    return redirect(
        url_for(
            "payment.result",
            order_number=order.order_number,
            state=result.get("status", "unknown"),
        )
    )


@payment_bp.get("/result/<order_number>")
def result(order_number: str):
    order, response = _accessible_order(order_number)
    if response is not None:
        return response
    requested_state = request.args.get("state")
    if order.payment_status == "paid":
        state = "success"
    elif requested_state in {"failed", "unknown"}:
        state = requested_state
    elif order.payment_status == "failed":
        state = "failed"
    elif order.payment_status in {"initiated", "pending"}:
        state = "pending"
    else:
        state = "unknown"

    template = {
        "success": "payment/success.html",
        "failed": "payment/failed.html",
        "pending": "payment/pending.html",
    }.get(state, "payment/unknown.html")
    return render_template(
        template,
        order=order,
        state=state,
        message=payment_service.payment_result_message(state if state != "pending" else "unknown"),
        payment=order.successful_payment,
    )


# --- درگاه آزمایشی داخلی (فقط زمانی که حالت آزمایشی فعال است) ---

@payment_bp.get("/test/<token>")
def test_gateway(token: str):
    if not bitpay_service.is_test_mode():
        abort(404)
    payment = db.session.query(Payment).filter(Payment.authority_or_token == token).first()
    if payment is None:
        abort(404)
    return render_template(
        "payment/test_gateway.html",
        payment=payment,
        order=payment.order,
        store_name=settings_service.get_str("store_name", "فروشگاه"),
    )


@payment_bp.post("/test/<token>/complete")
def test_complete(token: str):
    if not bitpay_service.is_test_mode():
        abort(404)
    payment = db.session.query(Payment).filter(Payment.authority_or_token == token).first()
    if payment is None:
        abort(404)
    success = request.form.get("action") == "success"
    params = {
        "id_get": payment.authority_or_token,
        "trans_id": f"TEST{int(time.time())}{secrets.token_hex(2)}" if success else "",
        "amount": bitpay_service.gateway_amount(payment.amount) if success else 0,
        "sig": bitpay_service.test_signature(payment),
    }
    result = payment_service.handle_callback(params)
    order = result.get("order")
    if order is None:
        flash(result.get("message", "نتیجه نامشخص است."), "error")
        return redirect(url_for("storefront.home"))
    order_service.grant_guest_access(order)
    return redirect(
        url_for("payment.result", order_number=order.order_number, state=result.get("status"))
    )
