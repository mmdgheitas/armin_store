from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from app.db import get_db
from app.repositories.orders import (
    get_order_by_id,
    get_order_history,
    get_order_items,
    get_order_payments,
    list_user_orders,
)
from app.repositories.users import (
    delete_user_address,
    get_province_and_city_names,
    get_user_address,
    get_user_by_email,
    get_user_by_mobile,
    list_provinces,
    list_user_addresses,
    save_address,
)
from app.services.auth import hash_password, login_required, validate_password_policy
from app.services.bitpay import get_payment_gateway
from app.services.orders import (
    ORDER_STATUS_LABELS,
    PAYMENT_METHOD_LABELS,
    PAYMENT_STATUS_LABELS,
    initiate_gateway_payment,
    transition_order_status,
)
from app.services.otp import verify_otp
from app.utils.persian import normalize_iran_mobile, now_utc_iso, validate_postal_code

account_bp = Blueprint("account", __name__, url_prefix="/account")


@account_bp.route("/")
@login_required
def dashboard():
    conn = get_db()
    uid = int(g.current_user["id"])
    orders, total_orders = list_user_orders(conn, uid, page=1, per_page=5)
    addresses = list_user_addresses(conn, uid)
    wish_count = conn.execute(
        "SELECT COUNT(*) FROM wishlists WHERE user_id = ?", (uid,)
    ).fetchone()[0]
    review_count = conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE user_id = ?", (uid,)
    ).fetchone()[0]
    return render_template(
        "account/dashboard.html",
        recent_orders=orders,
        total_orders=total_orders,
        addresses_count=len(addresses),
        wish_count=wish_count,
        review_count=review_count,
        status_labels=ORDER_STATUS_LABELS,
        noindex=True,
    )


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    conn = get_db()
    uid = int(g.current_user["id"])

    if request.method == "POST":
        action = request.form.get("action", "profile")
        if action == "profile":
            full_name = (request.form.get("full_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            if len(full_name) < 2:
                flash("نام و نام خانوادگی معتبر نیست.", "danger")
                return redirect(url_for("account.profile"))
            if email:
                existing = get_user_by_email(conn, email)
                if existing and int(existing["id"]) != uid:
                    flash("این ایمیل قبلاً توسط کاربر دیگری ثبت شده است.", "danger")
                    return redirect(url_for("account.profile"))
            conn.execute(
                "UPDATE users SET full_name = ?, email = ?, updated_at = ? WHERE id = ?",
                (full_name, email or None, now_utc_iso(), uid),
            )
            flash("اطلاعات پروفایل با موفقیت ذخیره شد.", "success")
        elif action == "change_mobile":
            new_mobile = normalize_iran_mobile(request.form.get("mobile"))
            code = request.form.get("otp_code", "")
            if not new_mobile:
                flash("شماره موبایل معتبر نیست.", "danger")
                return redirect(url_for("account.profile"))
            existing = get_user_by_mobile(conn, new_mobile)
            if existing and int(existing["id"]) != uid:
                flash("این شماره موبایل قبلاً به حساب دیگری متصل است.", "danger")
                return redirect(url_for("account.profile"))
            vres = verify_otp(conn, raw_mobile=new_mobile, raw_code=code, purpose="mobile_change")
            if not vres.ok:
                flash(vres.message, "danger")
                return redirect(url_for("account.profile"))
            conn.execute(
                "UPDATE users SET mobile = ?, mobile_verified = 1, updated_at = ? WHERE id = ?",
                (new_mobile, now_utc_iso(), uid),
            )
            flash("شماره موبایل شما تایید و به‌روزرسانی شد.", "success")
        elif action == "set_password":
            pw = request.form.get("password") or ""
            pw2 = request.form.get("password_confirm") or ""
            ok, msg = validate_password_policy(pw)
            if not ok:
                flash(msg, "danger")
                return redirect(url_for("account.profile"))
            if pw != pw2:
                flash("تکرار رمز عبور مطابقت ندارد.", "danger")
                return redirect(url_for("account.profile"))
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(pw), now_utc_iso(), uid),
            )
            flash("رمز عبور با موفقیت تنظیم/تغییر یافت.", "success")
        return redirect(url_for("account.profile"))

    return render_template("account/profile.html", noindex=True)


@account_bp.route("/addresses", methods=["GET", "POST"])
@login_required
def addresses():
    conn = get_db()
    uid = int(g.current_user["id"])

    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            aid = int(request.form.get("address_id", 0) or 0)
            if delete_user_address(conn, aid, uid):
                flash("آدرس حذف شد.", "info")
            return redirect(url_for("account.addresses"))

        address_id = int(request.form.get("address_id", 0) or 0) or None
        if address_id and not get_user_address(conn, address_id, uid):
            abort(403)
        title = (request.form.get("title") or "منزل").strip()
        recipient = (request.form.get("recipient_name") or "").strip()
        mobile = normalize_iran_mobile(request.form.get("mobile"))
        province_id = int(request.form.get("province_id", 0) or 0) or None
        city_id = int(request.form.get("city_id", 0) or 0) or None
        address_line = (request.form.get("address_line") or "").strip()
        postal_code = validate_postal_code(request.form.get("postal_code"))
        is_default = request.form.get("is_default") == "1"

        p_name, c_name = get_province_and_city_names(conn, province_id, city_id)
        if not recipient or not mobile or not p_name or not c_name or len(address_line) < 6 or not postal_code:
            flash("لطفاً تمام فیلدهای آدرس (شامل موبایل و کد پستی ۱۰ رقمی) را صحیح وارد کنید.", "danger")
            return redirect(url_for("account.addresses"))

        save_address(
            conn,
            user_id=uid,
            address_id=address_id,
            title=title,
            recipient_name=recipient,
            mobile=mobile,
            province_id=province_id,
            province_name=p_name,
            city_id=city_id,
            city_name=c_name,
            address_line=address_line,
            postal_code=postal_code,
            is_default=is_default,
        )
        flash("آدرس با موفقیت ذخیره شد.", "success")
        return redirect(url_for("account.addresses"))

    addrs = list_user_addresses(conn, uid)
    provinces = list_provinces(conn)
    return render_template(
        "account/addresses.html",
        addresses=addrs,
        provinces=provinces,
        noindex=True,
    )


@account_bp.route("/orders")
@login_required
def orders():
    conn = get_db()
    uid = int(g.current_user["id"])
    page = max(1, int(request.args.get("page", 1) or 1))
    rows, total = list_user_orders(conn, uid, page=page, per_page=15)
    return render_template(
        "account/orders.html",
        orders=rows,
        total=total,
        page=page,
        status_labels=ORDER_STATUS_LABELS,
        payment_labels=PAYMENT_STATUS_LABELS,
        noindex=True,
    )


@account_bp.route("/orders/<order_number>")
@login_required
def order_detail(order_number: str):
    conn = get_db()
    uid = int(g.current_user["id"])
    order = conn.execute(
        "SELECT * FROM orders WHERE order_number = ? AND user_id = ?",
        (order_number, uid),
    ).fetchone()
    if not order:
        abort(404)
    items = get_order_items(conn, int(order["id"]))
    history = get_order_history(conn, int(order["id"]))
    payments = get_order_payments(conn, int(order["id"]))
    return render_template(
        "account/order_detail.html",
        order=dict(order),
        items=items,
        history=history,
        payments=payments,
        status_labels=ORDER_STATUS_LABELS,
        payment_labels=PAYMENT_STATUS_LABELS,
        method_labels=PAYMENT_METHOD_LABELS,
        noindex=True,
    )


@account_bp.route("/orders/<order_number>/cancel", methods=["POST"])
@login_required
def cancel_order(order_number: str):
    conn = get_db()
    uid = int(g.current_user["id"])
    order = conn.execute(
        "SELECT * FROM orders WHERE order_number = ? AND user_id = ?",
        (order_number, uid),
    ).fetchone()
    if not order:
        abort(404)
    if order["order_status"] not in {"pending_payment", "paid", "processing"}:
        flash("این سفارش در مرحله‌ای است که امکان لغو آنلاین ندارد.", "danger")
        return redirect(url_for("account.order_detail", order_number=order_number))

    ok, msg = transition_order_status(
        conn,
        order_id=int(order["id"]),
        new_status="cancelled",
        actor_user_id=uid,
        note="لغو سفارش توسط مشتری",
    )
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("account.order_detail", order_number=order_number))


@account_bp.route("/orders/<order_number>/retry-payment", methods=["POST"])
@login_required
def retry_order_payment(order_number: str):
    conn = get_db()
    uid = int(g.current_user["id"])
    order = conn.execute(
        "SELECT * FROM orders WHERE order_number = ? AND user_id = ?",
        (order_number, uid),
    ).fetchone()
    if not order:
        abort(404)
    gateway = get_payment_gateway(conn, base_url=request.host_url.rstrip("/"))
    callback_url = url_for("payment.callback", _external=True)
    ok, redirect_url, err = initiate_gateway_payment(
        conn, order_id=int(order["id"]), gateway=gateway, callback_url=callback_url
    )
    if not ok:
        flash(err, "danger")
        return redirect(url_for("account.order_detail", order_number=order_number))
    return redirect(redirect_url)


@account_bp.route("/reviews")
@login_required
def my_reviews():
    conn = get_db()
    uid = int(g.current_user["id"])
    rows = conn.execute(
        """
        SELECT r.*, p.title AS product_title, p.slug AS product_slug
        FROM reviews r
        JOIN products p ON p.id = r.product_id
        WHERE r.user_id = ?
        ORDER BY r.id DESC
        """,
        (uid,),
    ).fetchall()
    return render_template("account/reviews.html", reviews=rows, noindex=True)


@account_bp.route("/wishlist")
@login_required
def wishlist():
    conn = get_db()
    uid = int(g.current_user["id"])
    rows = conn.execute(
        """
        SELECT w.id AS wish_id, p.*,
               COALESCE((SELECT pi.thumb_path FROM product_images pi WHERE pi.product_id = p.id ORDER BY pi.is_cover DESC, pi.sort_order ASC LIMIT 1), '') AS cover_thumb,
               COALESCE((SELECT MIN(v.price_rial) FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1), 0) AS min_price_rial,
               COALESCE((SELECT SUM(v.stock_quantity) FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1), 0) AS total_stock
        FROM wishlists w
        JOIN products p ON p.id = w.product_id
        WHERE w.user_id = ? AND p.status = 'published'
        ORDER BY w.id DESC
        """,
        (uid,),
    ).fetchall()
    return render_template("account/wishlist.html", items=rows, noindex=True)
