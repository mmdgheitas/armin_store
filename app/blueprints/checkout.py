from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.db import get_db
from app.repositories.settings import get_bool_setting
from app.repositories.users import (
    create_user,
    get_province_and_city_names,
    get_user_address,
    get_user_by_mobile,
    list_provinces,
    list_user_addresses,
    save_address,
)
from app.services.bitpay import get_payment_gateway
from app.services.cart import build_cart_summary
from app.services.inventory import InsufficientStockError, expire_unpaid_orders
from app.services.orders import (
    OrderError,
    create_checkout_order,
    initiate_gateway_payment,
)
from app.services.otp import verify_otp
from app.utils.persian import normalize_iran_mobile, validate_postal_code

checkout_bp = Blueprint("checkout", __name__, url_prefix="/checkout")


@checkout_bp.route("/", methods=["GET", "POST"])
def index():
    conn = get_db()
    expire_unpaid_orders(conn)
    uid = int(g.current_user["id"]) if g.current_user else None
    summary = build_cart_summary(conn, user_id=uid)

    if not summary.lines:
        flash("سبد خرید شما خالی است.", "warning")
        return redirect(url_for("cart.view_cart"))

    addresses = list_user_addresses(conn, uid) if uid else []
    provinces = list_provinces(conn)
    bitpay_enabled = get_bool_setting(conn, "payment_bitpay_enabled", True)
    cod_enabled = get_bool_setting(conn, "payment_cod_enabled", False)
    verified_guest_mobile = session.get("checkout_verified_mobile", "")

    if request.method == "POST":
        if summary.has_stock_issues:
            flash("لطفاً ابتدا مشکل موجودی کالاهای سبد خرید را برطرف کنید.", "danger")
            return redirect(url_for("cart.view_cart"))

        if request.form.get("accept_terms") != "1":
            flash("پذیرش قوانین و مقررات فروشگاه الزامی است.", "danger")
            return render_template(
                "checkout/checkout.html",
                summary=summary,
                addresses=addresses,
                provinces=provinces,
                bitpay_enabled=bitpay_enabled,
                cod_enabled=cod_enabled,
                verified_guest_mobile=verified_guest_mobile,
                form_data=request.form,
                noindex=True,
            )

        payment_method = request.form.get("payment_method", "bitpay")
        if payment_method == "bitpay" and not bitpay_enabled:
            flash("درگاه پرداخت آنلاین در حال حاضر غیرفعال است.", "danger")
            return redirect(url_for("checkout.index"))
        if payment_method == "cod" and not cod_enabled:
            flash("پرداخت در محل در حال حاضر فعال نیست.", "danger")
            return redirect(url_for("checkout.index"))

        # Resolve shipping info & customer identity
        selected_addr_id = request.form.get("address_id", "new")
        customer_email = (request.form.get("customer_email") or "").strip()
        customer_note = (request.form.get("customer_note") or "").strip()

        if uid and selected_addr_id not in ("", "new"):
            try:
                addr_row = get_user_address(conn, int(selected_addr_id), uid)
            except ValueError:
                addr_row = None
            if not addr_row:
                flash("آدرس انتخاب‌شده یافت نشد.", "danger")
                return redirect(url_for("checkout.index"))
            customer_name = addr_row["recipient_name"]
            customer_mobile = addr_row["mobile"]
            province_name = addr_row["province_name"]
            city_name = addr_row["city_name"]
            address_line = addr_row["address_line"]
            postal_code = addr_row["postal_code"]
            if not customer_email:
                customer_email = g.current_user.get("email") or ""
        else:
            customer_name = (request.form.get("customer_name") or "").strip()
            customer_mobile = normalize_iran_mobile(request.form.get("customer_mobile"))
            province_id = int(request.form.get("province_id", 0) or 0) or None
            city_id = int(request.form.get("city_id", 0) or 0) or None
            province_name, city_name = get_province_and_city_names(conn, province_id, city_id)
            address_line = (request.form.get("address_line") or "").strip()
            postal_code = validate_postal_code(request.form.get("postal_code"))

            if (
                len(customer_name) < 2
                or not customer_mobile
                or not province_name
                or not city_name
                or len(address_line) < 6
                or not postal_code
            ):
                flash(
                    "لطفاً نام گیرنده، شماره موبایل معتبر، استان، شهر، نشانی کامل و کد پستی ۱۰ رقمی را صحیح وارد کنید.",
                    "danger",
                )
                return render_template(
                    "checkout/checkout.html",
                    summary=summary,
                    addresses=addresses,
                    provinces=provinces,
                    bitpay_enabled=bitpay_enabled,
                    cod_enabled=cod_enabled,
                    verified_guest_mobile=verified_guest_mobile,
                    form_data=request.form,
                    noindex=True,
                )

            # Guest checkout requires mobile OTP verification
            order_user_id = uid
            if not uid:
                if verified_guest_mobile != customer_mobile:
                    otp_code = request.form.get("otp_code", "")
                    vres = verify_otp(
                        conn,
                        raw_mobile=customer_mobile,
                        raw_code=otp_code,
                        purpose="checkout",
                    )
                    if not vres.ok:
                        flash(f"تایید شماره موبایل مهمان: {vres.message}", "danger")
                        return render_template(
                            "checkout/checkout.html",
                            summary=summary,
                            addresses=addresses,
                            provinces=provinces,
                            bitpay_enabled=bitpay_enabled,
                            cod_enabled=cod_enabled,
                            verified_guest_mobile=verified_guest_mobile,
                            form_data=request.form,
                            noindex=True,
                        )
                    session["checkout_verified_mobile"] = customer_mobile
                    session.modified = True

                # Link to existing user by mobile if present, or keep guest order
                existing_u = get_user_by_mobile(conn, customer_mobile)
                if existing_u:
                    order_user_id = int(existing_u["id"])
                else:
                    order_user_id = create_user(
                        conn,
                        mobile=customer_mobile,
                        email=customer_email or None,
                        full_name=customer_name,
                        role="customer",
                        mobile_verified=1,
                    )
            else:
                if request.form.get("save_address") == "1":
                    save_address(
                        conn,
                        user_id=uid,
                        title="آدرس ارسال",
                        recipient_name=customer_name,
                        mobile=customer_mobile,
                        province_id=province_id,
                        province_name=province_name,
                        city_id=city_id,
                        city_name=city_name,
                        address_line=address_line,
                        postal_code=postal_code,
                        is_default=(len(addresses) == 0),
                    )
            uid_for_order = order_user_id
        if uid and selected_addr_id not in ("", "new"):
            uid_for_order = uid

        lines_input = [(ln.variant_id, ln.quantity) for ln in summary.lines]
        try:
            order = create_checkout_order(
                conn,
                user_id=uid_for_order,
                lines_input=lines_input,
                customer_name=customer_name,
                customer_mobile=customer_mobile,
                customer_email=customer_email,
                shipping_province=province_name,
                shipping_city=city_name,
                shipping_address=address_line,
                shipping_postal_code=postal_code,
                coupon_code=summary.coupon_code if not summary.coupon_error else "",
                payment_method=payment_method,
                customer_note=customer_note,
                clear_user_cart=True,
            )
            # Clear guest cart even if guest got attached to a newly created user
            session.pop("guest_cart", None)
            session.pop("guest_coupon_code", None)
            allowed_orders = session.get("recent_order_numbers", [])
            allowed_orders.append(order["order_number"])
            session["recent_order_numbers"] = allowed_orders[-10:]
            session.modified = True
        except (InsufficientStockError, OrderError) as exc:
            flash(str(exc), "danger")
            return redirect(url_for("cart.view_cart"))

        if payment_method == "cod":
            return redirect(url_for("payment.result", order_number=order["order_number"]))

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
            return redirect(url_for("payment.result", order_number=order["order_number"]))
        return redirect(redirect_url)

    return render_template(
        "checkout/checkout.html",
        summary=summary,
        addresses=addresses,
        provinces=provinces,
        bitpay_enabled=bitpay_enabled,
        cod_enabled=cod_enabled,
        verified_guest_mobile=verified_guest_mobile,
        form_data={},
        noindex=True,
    )
