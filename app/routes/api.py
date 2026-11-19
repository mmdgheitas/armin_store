"""API سبک برای عملیات AJAX فروشگاه (JSON، CSRF با هدر)."""
from __future__ import annotations

from flask import Blueprint, g, request, session

from app.extensions import db
from app.models import Product, Wishlist
from app.services import cart_service, coupon_service, otp_service
from app.utils.http import client_ip, json_error, json_ok
from app.utils.security import is_valid_mobile, normalize_mobile
from app.utils.dates import utcnow

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _cart_state() -> dict:
    summary = cart_service.summary(user=getattr(g, "user", None))
    return {
        "count": summary["count"],
        "subtotal": summary["subtotal"],
        "discount": summary["discount"],
        "shipping_cost": summary["shipping_cost"],
        "total": summary["total"],
        "issues": summary["issues"],
    }


@api_bp.post("/cart/add")
def cart_add():
    data = _payload()
    ok, message = cart_service.add(data.get("variant_id", 0), data.get("quantity", 1))
    if not ok:
        return json_error(message, 400)
    return json_ok(message, cart=_cart_state())


@api_bp.post("/cart/update")
def cart_update():
    data = _payload()
    ok, message = cart_service.set_quantity(data.get("variant_id", 0), data.get("quantity", 1))
    if not ok:
        return json_error(message, 400)
    return json_ok(message, cart=_cart_state())


@api_bp.post("/cart/remove")
def cart_remove():
    data = _payload()
    ok, message = cart_service.remove(data.get("variant_id", 0))
    return json_ok(message, cart=_cart_state())


@api_bp.get("/cart")
def cart_get():
    return json_ok("", cart=_cart_state())


@api_bp.post("/cart/coupon")
def cart_coupon():
    data = _payload()
    summary = cart_service.summary(user=getattr(g, "user", None))
    if summary["is_empty"]:
        return json_error("سبد خرید خالی است.", 400)
    result = coupon_service.validate(data.get("code", ""), summary["items"], user=getattr(g, "user", None))
    if result["ok"]:
        cart_service.set_coupon(result["coupon"].code)
        return json_ok(result["message"], cart=_cart_state())
    cart_service.clear_coupon()
    return json_error(result["message"], 400, cart=_cart_state())


@api_bp.post("/wishlist/toggle")
def wishlist_toggle():
    user = getattr(g, "user", None)
    if user is None:
        return json_error("برای استفاده از علاقه‌مندی‌ها وارد حساب شوید.", 401)
    data = _payload()
    try:
        product_id = int(data.get("product_id", 0))
    except (TypeError, ValueError):
        return json_error("محصول نامعتبر است.", 400)
    product = db.session.get(Product, product_id)
    if product is None or not product.is_active:
        return json_error("محصول یافت نشد.", 404)
    existing = (
        db.session.query(Wishlist)
        .filter(Wishlist.user_id == user.id, Wishlist.product_id == product_id)
        .first()
    )
    if existing is not None:
        db.session.delete(existing)
        db.session.commit()
        return json_ok("از علاقه‌مندی‌ها حذف شد.", active=False)
    db.session.add(Wishlist(user_id=user.id, product_id=product_id, created_at=utcnow()))
    db.session.commit()
    return json_ok("به علاقه‌مندی‌ها اضافه شد.", active=True)


@api_bp.post("/compare/toggle")
def compare_toggle():
    from app.services import settings_service

    data = _payload()
    try:
        product_id = int(data.get("product_id", 0))
    except (TypeError, ValueError):
        return json_error("محصول نامعتبر است.", 400)
    product = db.session.get(Product, product_id)
    if product is None or not product.is_active:
        return json_error("محصول یافت نشد.", 404)
    ids = [int(i) for i in (session.get("compare") or []) if str(i).isdigit()]
    limit = max(2, settings_service.get_int("comparison_limit", 4))
    if product_id in ids:
        ids.remove(product_id)
        session["compare"] = ids
        session.modified = True
        return json_ok("از لیست مقایسه حذف شد.", active=False, count=len(ids))
    if len(ids) >= limit:
        return json_error(f"حداکثر {limit} محصول قابل مقایسه است.", 400, count=len(ids))
    ids.append(product_id)
    session["compare"] = ids
    session.modified = True
    return json_ok("به لیست مقایسه اضافه شد.", active=True, count=len(ids))


@api_bp.post("/otp/request")
def otp_request():
    data = _payload()
    mobile = normalize_mobile(data.get("mobile", ""))
    if not is_valid_mobile(mobile):
        return json_error("شماره موبایل معتبر نیست.", 400)
    purpose = data.get("purpose", "login")
    if purpose not in {"login", "reset_password", "verify_mobile"}:
        purpose = "login"
    try:
        result = otp_service.send_code(mobile, purpose, client_ip())
    except otp_service.OtpError as exc:
        return json_error(exc.message, 429 if exc.retry_after else 400)
    return json_ok(result.get("message", "کد ارسال شد."), debug_code=result.get("debug_code"))
