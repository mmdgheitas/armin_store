from __future__ import annotations

import secrets

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

from app import csrf, limiter
from app.db import get_db
from app.repositories.orders import get_order_by_id, get_order_by_number, get_order_items
from app.services.bitpay import get_payment_gateway
from app.services.orders import (
    ORDER_STATUS_LABELS,
    PAYMENT_METHOD_LABELS,
    PAYMENT_STATUS_LABELS,
    initiate_gateway_payment,
    process_payment_callback,
)

payment_bp = Blueprint("payment", __name__, url_prefix="/payment")


def _can_view_order(order: dict) -> bool:
    if g.current_user and order.get("user_id") == g.current_user["id"]:
        return True
    recent = session.get("recent_order_numbers") or []
    return order["order_number"] in recent


@payment_bp.route("/callback", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("60/minute")
def callback():
    conn = get_db()
    id_get = (request.values.get("id_get") or "").strip()
    trans_id = (request.values.get("trans_id") or "").strip()

    gateway = get_payment_gateway(conn, base_url=request.host_url.rstrip("/"))
    ok, order, msg = process_payment_callback(
        conn,
        gateway=gateway,
        id_get=id_get,
        trans_id=trans_id,
    )

    if not order:
        flash(msg, "danger")
        return render_template(
            "payment/result.html",
            order=None,
            items=[],
            success=False,
            message=msg,
            noindex=True,
        )

    # Allow guest/customer to view result page for this order
    recent = session.get("recent_order_numbers", [])
    if order["order_number"] not in recent:
        recent.append(order["order_number"])
        session["recent_order_numbers"] = recent[-10:]
        session.modified = True

    flash(msg, "success" if ok else "danger")
    return redirect(url_for("payment.result", order_number=order["order_number"]))


@payment_bp.route("/result/<order_number>")
def result(order_number: str):
    conn = get_db()
    order = get_order_by_number(conn, order_number)
    if not order or not _can_view_order(dict(order)):
        abort(404)
    items = get_order_items(conn, int(order["id"]))
    pay_row = conn.execute(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1",
        (order["id"],),
    ).fetchone()
    success = order["payment_status"] == "paid" or order["payment_method"] == "cod"
    return render_template(
        "payment/result.html",
        order=dict(order),
        items=items,
        payment=dict(pay_row) if pay_row else None,
        success=success,
        status_labels=ORDER_STATUS_LABELS,
        payment_labels=PAYMENT_STATUS_LABELS,
        method_labels=PAYMENT_METHOD_LABELS,
        noindex=True,
    )


@payment_bp.route("/retry/<order_number>", methods=["POST"])
def retry(order_number: str):
    conn = get_db()
    order = get_order_by_number(conn, order_number)
    if not order or not _can_view_order(dict(order)):
        abort(404)
    gateway = get_payment_gateway(conn, base_url=request.host_url.rstrip("/"))
    callback_url = url_for("payment.callback", _external=True)
    ok, redirect_url, err = initiate_gateway_payment(
        conn,
        order_id=int(order["id"]),
        gateway=gateway,
        callback_url=callback_url,
    )
    if not ok:
        flash(err, "danger")
        return redirect(url_for("payment.result", order_number=order_number))
    return redirect(redirect_url)


@payment_bp.route("/mock-gateway/<id_get>", methods=["GET", "POST"])
def mock_gateway(id_get: str):
    """Interactive simulator for Bitpay.ir when PAYMENT_MODE=mock."""
    conn = get_db()
    pay_row = conn.execute(
        "SELECT * FROM payments WHERE id_get = ? ORDER BY id DESC LIMIT 1", (id_get,)
    ).fetchone()
    if not pay_row:
        abort(404)
    order = get_order_by_id(conn, int(pay_row["order_id"]))
    if not order:
        abort(404)

    if request.method == "POST":
        outcome = request.form.get("outcome", "success")
        if outcome == "success":
            trans_id = str(10000000 + secrets.randbelow(89999999))
            return redirect(url_for("payment.callback", id_get=id_get, trans_id=trans_id))
        if outcome == "fail":
            return redirect(url_for("payment.callback", id_get=id_get, trans_id="FAIL"))
        return redirect(url_for("payment.callback", id_get=id_get, trans_id=""))

    return render_template(
        "payment/mock_gateway.html",
        id_get=id_get,
        order=dict(order),
        noindex=True,
    )
