from __future__ import annotations

import datetime as dt

from flask import (
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app import limiter
from app.blueprints.admin import admin_bp
from app.db import get_db
from app.repositories.users import get_user_by_username_or_email
from app.services.auth import (
    ADMIN_ROLES,
    admin_required,
    login_user_session,
    logout_user_session,
    verify_password,
)
from app.services.inventory import expire_unpaid_orders
from app.services.orders import ORDER_STATUS_LABELS, PAYMENT_STATUS_LABELS
from app.services.otp import request_otp, verify_otp
from app.utils.persian import TEHRAN_TZ, to_jalali_date


@admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20/minute")
def login():
    if g.current_user and g.current_user["role"] in ADMIN_ROLES and session.get("is_admin_auth"):
        return redirect(url_for("admin.dashboard"))

    conn = get_db()
    step = "credentials"
    pending_uid = session.get("admin_2fa_uid")

    if request.method == "POST":
        action = request.form.get("action", "credentials")
        if action == "verify_2fa" and pending_uid:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (pending_uid,)).fetchone()
            if not user or not user["mobile"]:
                session.pop("admin_2fa_uid", None)
                flash("نشست احراز هویت نامعتبر است.", "danger")
                return redirect(url_for("admin.login"))
            code = request.form.get("otp_code", "")
            vres = verify_otp(conn, raw_mobile=user["mobile"], raw_code=code, purpose="admin_2fa")
            if not vres.ok:
                flash(vres.message, "danger")
                return render_template("admin/login.html", step="2fa", masked_mobile=user["mobile"][-4:], noindex=True)
            session.pop("admin_2fa_uid", None)
            login_user_session(conn, user, is_admin_session=True)
            flash("به پنل مدیریت خوش آمدید.", "success")
            return redirect(url_for("admin.dashboard"))

        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        user = get_user_by_username_or_email(conn, identifier) if identifier else None
        if (
            not user
            or user["role"] not in ADMIN_ROLES
            or not user["is_active"]
            or user["is_blocked"]
            or not verify_password(user["password_hash"], password)
        ):
            flash("نام کاربری/ایمیل یا رمز عبور مدیریت اشتباه است.", "danger")
            return render_template("admin/login.html", step="credentials", noindex=True)

        if user["otp_2fa_enabled"] and user["mobile"]:
            res = request_otp(
                conn,
                raw_mobile=user["mobile"],
                purpose="admin_2fa",
                ip_address=request.remote_addr or "",
            )
            session["admin_2fa_uid"] = int(user["id"])
            session.modified = True
            flash(
                res.message + (f" (کد آزمایشی: {res.dev_code})" if res.dev_code else ""),
                "info",
            )
            return render_template("admin/login.html", step="2fa", masked_mobile=user["mobile"][-4:], noindex=True)

        login_user_session(conn, user, is_admin_session=True)
        flash("ورود به پنل مدیریت با موفقیت انجام شد.", "success")
        next_url = request.args.get("next") or url_for("admin.dashboard")
        if not next_url.startswith("/"):
            next_url = url_for("admin.dashboard")
        return redirect(next_url)

    return render_template("admin/login.html", step=step, noindex=True)


@admin_bp.route("/logout", methods=["POST"])
def logout():
    logout_user_session()
    flash("از پنل مدیریت خارج شدید.", "info")
    return redirect(url_for("admin.login"))


@admin_bp.route("/")
@admin_required("dashboard")
def dashboard():
    conn = get_db()
    expire_unpaid_orders(conn)

    now_tehran = dt.datetime.now(TEHRAN_TZ)
    today_start_utc = (
        now_tehran.replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )
    d7_utc = (
        (now_tehran - dt.timedelta(days=7))
        .astimezone(dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )
    d30_utc = (
        (now_tehran - dt.timedelta(days=30))
        .astimezone(dt.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    )

    sales_today = conn.execute(
        "SELECT COALESCE(SUM(total_rial), 0) FROM orders WHERE payment_status = 'paid' AND created_at >= ?",
        (today_start_utc,),
    ).fetchone()[0]
    sales_7d = conn.execute(
        "SELECT COALESCE(SUM(total_rial), 0) FROM orders WHERE payment_status = 'paid' AND created_at >= ?",
        (d7_utc,),
    ).fetchone()[0]
    sales_30d = conn.execute(
        "SELECT COALESCE(SUM(total_rial), 0) FROM orders WHERE payment_status = 'paid' AND created_at >= ?",
        (d30_utc,),
    ).fetchone()[0]

    status_counts = {
        r["order_status"]: r["cnt"]
        for r in conn.execute(
            "SELECT order_status, COUNT(*) AS cnt FROM orders GROUP BY order_status"
        ).fetchall()
    }

    # 14-day daily sales series for vanilla JS canvas/SVG chart
    chart_labels: list[str] = []
    chart_values_toman: list[int] = []
    for offset in range(13, -1, -1):
        day_local = (now_tehran - dt.timedelta(days=offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end_local = day_local + dt.timedelta(days=1)
        u1 = day_local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        u2 = day_end_local.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        total_r = conn.execute(
            "SELECT COALESCE(SUM(total_rial), 0) FROM orders WHERE payment_status = 'paid' AND created_at >= ? AND created_at < ?",
            (u1, u2),
        ).fetchone()[0]
        chart_labels.append(to_jalali_date(u1))
        chart_values_toman.append(int(total_r) // 10)

    low_stock_variants = conn.execute(
        """
        SELECT v.*, p.title AS product_title, p.id AS product_id
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.is_active = 1 AND p.status != 'archived' AND v.stock_quantity <= v.low_stock_threshold
        ORDER BY v.stock_quantity ASC, v.id ASC
        LIMIT 12
        """
    ).fetchall()

    latest_orders = conn.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 10"
    ).fetchall()

    top_products = conn.execute(
        """
        SELECT p.id, p.title, p.slug, p.total_sold,
               COALESCE((SELECT SUM(oi.line_total_rial) FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE oi.product_id = p.id AND o.payment_status = 'paid'), 0) AS revenue_rial
        FROM products p
        WHERE p.status != 'archived'
        ORDER BY p.total_sold DESC, revenue_rial DESC
        LIMIT 8
        """
    ).fetchall()

    return render_template(
        "admin/dashboard.html",
        sales_today=sales_today,
        sales_7d=sales_7d,
        sales_30d=sales_30d,
        status_counts=status_counts,
        status_labels=ORDER_STATUS_LABELS,
        payment_labels=PAYMENT_STATUS_LABELS,
        chart_labels=chart_labels,
        chart_values_toman=chart_values_toman,
        low_stock_variants=low_stock_variants,
        latest_orders=latest_orders,
        top_products=top_products,
    )
