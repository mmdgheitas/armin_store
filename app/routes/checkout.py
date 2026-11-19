"""تسویه حساب: اطلاعات گیرنده، روش ارسال، کوپن و ثبت سفارش."""
from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from app.extensions import db
from app.services import cart_service, order_service, settings_service, shipping_service
from app.utils.http import client_ip
from app.utils.security import is_valid_mobile, normalize_mobile

checkout_bp = Blueprint("checkout", __name__, url_prefix="/checkout")


def _shipping_options(address: dict, subtotal: int, has_physical: bool):
    if not has_physical:
        return []
    options = []
    for method in shipping_service.active_methods():
        options.append(
            {
                "method": method,
                "cost": method.cost_for(address.get("province"), address.get("city"), subtotal),
            }
        )
    return options


@checkout_bp.route("/", methods=["GET", "POST"])
def index():
    user = getattr(g, "user", None)
    summary = cart_service.summary(user=user)
    if summary["is_empty"]:
        flash("سبد خرید شما خالی است.", "warning")
        return redirect(url_for("cart.view"))

    if user is None and not settings_service.get_bool("guest_checkout_enabled", True):
        flash("برای تسویه حساب باید وارد حساب کاربری شوید.", "warning")
        return redirect(url_for("auth.login", next=request.path))

    form = request.form if request.method == "POST" else {}
    selected_address = None
    if user is not None and request.method == "GET":
        address_id = request.args.get("address_id")
        if address_id:
            selected_address = next((a for a in user.addresses if str(a.id) == str(address_id)), None)
        if selected_address is None:
            selected_address = user.default_address

    initial_address = (
        selected_address.to_dict()
        if selected_address is not None
        else {
            "recipient_name": user.full_name if user else form.get("recipient_name", ""),
            "mobile": user.mobile if user else form.get("mobile", ""),
            "province": form.get("province", ""),
            "city": form.get("city", ""),
            "full_address": "",
            "postal_code": "",
            "national_code": "",
        }
    )

    errors: list[str] = []
    if request.method == "POST":
        address = order_service.build_address_dict(request.form)
        if len(address["recipient_name"]) < 3:
            errors.append("نام و نام خانوادگی گیرنده را کامل وارد کنید.")
        if not is_valid_mobile(address["mobile"]):
            errors.append("شماره موبایل معتبر وارد کنید.")
        if len(address["province"]) < 2:
            errors.append("استان را انتخاب کنید.")
        if len(address["city"]) < 2:
            errors.append("شهر را وارد کنید.")
        if len(address["full_address"]) < 10:
            errors.append("آدرس کامل پستی را وارد کنید.")
        if address["postal_code"] and not address["postal_code"].isdigit():
            errors.append("کد پستی باید عددی باشد.")
        if not request.form.get("accept_terms"):
            errors.append("برای ادامه باید قوانین خرید را بپذیرید.")
        if summary["has_physical"]:
            method_id = request.form.get("shipping_method_id")
            if method_id:
                cart_service.set_shipping_method(method_id)
            if shipping_service.get_method(method_id) is None:
                errors.append("روش ارسال را انتخاب کنید.")

        if not errors:
            summary = cart_service.summary(user=user, address=address)
            order, error = order_service.place_order(
                user=user,
                address=address,
                customer_note=request.form.get("customer_note", ""),
                ip=client_ip(),
                user_agent=request.headers.get("User-Agent", ""),
                guest_name=address["recipient_name"],
                guest_mobile=address["mobile"],
                guest_email=request.form.get("email", ""),
            )
            if order is not None:
                order_service.grant_guest_access(order)
                if user is not None and request.form.get("save_address"):
                    saved, save_errors = order_service.save_address(
                        user, address, make_default=bool(request.form.get("make_default"))
                    )
                    if save_errors:
                        for message in save_errors:
                            flash(message, "warning")
                if request.form.get("email") and user is None:
                    order.guest_email = request.form.get("email", "")[:190]
                    db.session.commit()
                if order.total_amount <= 0:
                    from app.services import payment_service

                    payment_service.complete_free_order(order)
                    flash("سفارش شما با موفقیت ثبت شد.", "success")
                    return redirect(url_for("payment.result", order_number=order.order_number))
                return redirect(url_for("payment.start", order_number=order.order_number))
            errors.append(error or "ثبت سفارش انجام نشد.")
            summary = cart_service.summary(user=user, address=address)

        for message in errors:
            flash(message, "error")

    address_for_shipping = order_service.build_address_dict(form) if form else (initial_address or {})
    options = _shipping_options(address_for_shipping, summary["subtotal"], summary["has_physical"])
    effective_summary = summary
    if options and summary["shipping_method"] is None and request.method == "GET":
        # پیشنهاد ارزان‌ترین روش تا مبلغ نهایی قابل نمایش باشد
        cheapest = min(options, key=lambda item: item["cost"])
        cart_service.set_shipping_method(cheapest["method"].id)
        effective_summary = cart_service.summary(user=user)

    return render_template(
        "checkout/index.html",
        summary=effective_summary,
        options=options,
        addresses=user.addresses if user else [],
        selected_address=selected_address,
        initial_address=initial_address,
        form=form,
        errors=errors,
        coupon_code=cart_service.get_coupon_code(),
    )


@checkout_bp.get("/shipping")
def shipping_step():
    summary = cart_service.summary(user=getattr(g, "user", None))
    if summary["is_empty"]:
        return redirect(url_for("cart.view"))
    if not summary["has_physical"]:
        flash("سفارش شما فقط شامل محصولات دیجیتال است و نیازی به ارسال ندارد.", "info")
        return redirect(url_for("checkout.index"))
    address = order_service.build_address_dict(request.args.to_dict())
    options = _shipping_options(address, summary["subtotal"], True)
    return render_template(
        "checkout/shipping.html",
        summary=summary,
        options=options,
        address=address,
    )
