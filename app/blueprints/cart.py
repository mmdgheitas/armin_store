from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app.db import get_db
from app.services.cart import (
    add_to_cart,
    build_cart_summary,
    remove_from_cart,
    set_cart_coupon,
    update_cart_quantity,
)
from app.utils.persian import format_toman, to_persian_digits

cart_bp = Blueprint("cart", __name__, url_prefix="/cart")


def _summary_json(summary) -> dict:
    return {
        "item_count": summary.item_count,
        "item_count_fa": to_persian_digits(summary.item_count),
        "subtotal_rial": summary.subtotal_rial,
        "subtotal_toman_str": format_toman(summary.subtotal_rial),
        "shipping_fee_rial": summary.shipping_fee_rial,
        "shipping_fee_str": "رایگان" if summary.shipping_fee_rial == 0 else format_toman(summary.shipping_fee_rial),
        "discount_rial": summary.discount_rial,
        "discount_str": format_toman(summary.discount_rial),
        "total_rial": summary.total_rial,
        "total_toman_str": format_toman(summary.total_rial),
        "coupon_code": summary.coupon_code,
        "coupon_error": summary.coupon_error,
        "has_stock_issues": summary.has_stock_issues,
        "warnings": summary.warnings,
        "lines": [
            {
                "variant_id": ln.variant_id,
                "quantity": ln.quantity,
                "available_stock": ln.available_stock,
                "line_total_str": format_toman(ln.line_total_rial),
                "warning": ln.warning,
            }
            for ln in summary.lines
        ],
    }


@cart_bp.route("/")
def view_cart():
    conn = get_db()
    uid = int(g.current_user["id"]) if g.current_user else None
    summary = build_cart_summary(conn, user_id=uid)
    return render_template("cart/cart.html", summary=summary, noindex=True)


@cart_bp.route("/add", methods=["POST"])
def add():
    conn = get_db()
    uid = int(g.current_user["id"]) if g.current_user else None
    data = request.get_json(silent=True) if request.is_json else request.form
    try:
        variant_id = int(data.get("variant_id", 0))
        qty = int(data.get("quantity", 1))
    except (TypeError, ValueError):
        if request.is_json:
            return jsonify({"ok": False, "message": "اطلاعات نامعتبر است."}), 400
        flash("اطلاعات نامعتبر است.", "danger")
        return redirect(request.referrer or url_for("cart.view_cart"))

    ok, msg = add_to_cart(conn, user_id=uid, variant_id=variant_id, quantity=qty)
    summary = build_cart_summary(conn, user_id=uid)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": ok, "message": msg, "cart": _summary_json(summary)}), (200 if ok else 400)

    flash(msg, "success" if ok else "danger")
    return redirect(request.referrer or url_for("cart.view_cart"))


@cart_bp.route("/update", methods=["POST"])
def update():
    conn = get_db()
    uid = int(g.current_user["id"]) if g.current_user else None
    data = request.get_json(silent=True) if request.is_json else request.form
    try:
        variant_id = int(data.get("variant_id", 0))
        qty = int(data.get("quantity", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "مقدار نامعتبر است."}), 400

    ok, msg = update_cart_quantity(conn, user_id=uid, variant_id=variant_id, quantity=qty)
    summary = build_cart_summary(conn, user_id=uid)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": ok, "message": msg, "cart": _summary_json(summary)}), (200 if ok else 400)
    flash(msg, "success" if ok else "warning")
    return redirect(url_for("cart.view_cart"))


@cart_bp.route("/remove", methods=["POST"])
def remove():
    conn = get_db()
    uid = int(g.current_user["id"]) if g.current_user else None
    data = request.get_json(silent=True) if request.is_json else request.form
    try:
        variant_id = int(data.get("variant_id", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "شناسه نامعتبر است."}), 400

    ok, msg = remove_from_cart(conn, user_id=uid, variant_id=variant_id)
    summary = build_cart_summary(conn, user_id=uid)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": ok, "message": msg, "cart": _summary_json(summary)})
    flash(msg, "info")
    return redirect(url_for("cart.view_cart"))


@cart_bp.route("/coupon", methods=["POST"])
def apply_coupon():
    conn = get_db()
    uid = int(g.current_user["id"]) if g.current_user else None
    action = request.form.get("action", "apply")
    code = "" if action == "remove" else (request.form.get("coupon_code") or "").strip()
    ok, msg = set_cart_coupon(conn, user_id=uid, code=code)
    flash(msg, "success" if ok else "danger")
    next_target = request.form.get("next") or url_for("cart.view_cart")
    if not next_target.startswith("/"):
        next_target = url_for("cart.view_cart")
    return redirect(next_target)
