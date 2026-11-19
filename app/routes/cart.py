"""سبد خرید: نمایش و عملیات (روش‌های POST برای تغییر داده)."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.services import cart_service, coupon_service, shipping_service
from app.utils.http import is_safe_redirect

cart_bp = Blueprint("cart", __name__, url_prefix="/cart")


def _back(default_endpoint: str = "cart.view") -> str:
    target = request.form.get("next") or request.referrer
    if target and is_safe_redirect(target):
        return target
    return url_for(default_endpoint)


@cart_bp.get("/")
def view():
    summary = cart_service.summary(user=getattr(g, "user", None))
    methods = shipping_service.active_methods() if summary["has_physical"] else []
    return render_template("cart/view.html", summary=summary, methods=methods)


@cart_bp.post("/add")
def add():
    ok, message = cart_service.add(
        request.form.get("variant_id", 0), request.form.get("quantity", 1)
    )
    flash(message, "success" if ok else "error")
    if ok and request.form.get("buy_now"):
        return redirect(url_for("checkout.index"))
    return redirect(_back("cart.view"))


@cart_bp.post("/update")
def update():
    ok, message = cart_service.set_quantity(
        request.form.get("variant_id", 0), request.form.get("quantity", 1)
    )
    flash(message, "success" if ok else "error")
    return redirect(url_for("cart.view"))


@cart_bp.post("/remove")
def remove():
    ok, message = cart_service.remove(request.form.get("variant_id", 0))
    flash(message, "success" if ok else "error")
    return redirect(url_for("cart.view"))


@cart_bp.post("/clear")
def clear():
    cart_service.clear()
    flash("سبد خرید خالی شد.", "info")
    return redirect(url_for("cart.view"))


@cart_bp.post("/coupon")
def apply_coupon():
    code = (request.form.get("code") or "").strip()
    summary = cart_service.summary(user=getattr(g, "user", None))
    if summary["is_empty"]:
        flash("سبد خرید شما خالی است.", "warning")
        return redirect(url_for("cart.view"))
    if not code:
        flash("کد تخفیف را وارد کنید.", "error")
        return redirect(_back("cart.view"))
    result = coupon_service.validate(
        code, summary["items"], user=getattr(g, "user", None)
    )
    if result["ok"]:
        cart_service.set_coupon(result["coupon"].code)
        flash(result["message"], "success")
    else:
        cart_service.clear_coupon()
        flash(result["message"], "error")
    return redirect(_back("cart.view"))


@cart_bp.post("/coupon/remove")
def remove_coupon():
    cart_service.clear_coupon()
    flash("کد تخفیف حذف شد.", "info")
    return redirect(url_for("cart.view"))


@cart_bp.post("/shipping")
def set_shipping():
    method_id = request.form.get("shipping_method_id")
    if shipping_service.get_method(method_id) is None:
        flash("روش ارسال انتخاب‌شده معتبر نیست.", "error")
    else:
        cart_service.set_shipping_method(method_id)
        flash("روش ارسال انتخاب شد.", "success")
    return redirect(_back("cart.view"))
