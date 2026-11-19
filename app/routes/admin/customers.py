"""مدیریت مشتریان."""
from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from app.decorators import permission_required
from app.extensions import db
from app.models import Order, User
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, log_admin, text_field
from app.services import auth_service
from app.utils.dates import utcnow
from app.utils.pagination import paginate
from app.utils.security import hash_password, is_valid_mobile, normalize_mobile


@admin_bp.get("/customers")
@permission_required("customers.view")
def customers():
    q = (request.args.get("q") or "").strip()
    stmt = db.session.query(User).filter(User.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(or_(User.full_name.like(like), User.mobile.like(like), User.email.like(like)))
    status = request.args.get("status")
    if status == "active":
        stmt = stmt.filter(User.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.filter(User.is_active.is_(False))
    page = paginate(stmt.order_by(User.created_at.desc()), per_page=25)

    ids = [user.id for user in page.items]
    order_stats = {}
    if ids:
        rows = (
            db.session.query(
                Order.user_id,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
            )
            .filter(Order.user_id.in_(ids), Order.payment_status == "paid")
            .group_by(Order.user_id)
            .all()
        )
        order_stats = {row[0]: {"count": row[1], "total": int(row[2] or 0)} for row in rows}
    return render_template("admin/customers/list.html", page=page, q=q, status=status, order_stats=order_stats)


@admin_bp.get("/customers/<int:user_id>")
@permission_required("customers.view")
def customer_detail(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    page = paginate(
        db.session.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()),
        per_page=10,
    )
    stats = (
        db.session.query(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), 0),
        )
        .filter(Order.user_id == user.id, Order.payment_status == "paid")
        .first()
    )
    return render_template(
        "admin/customers/detail.html",
        user=user,
        page=page,
        paid_orders=int(stats[0] or 0),
        total_spent=int(stats[1] or 0),
    )


@admin_bp.post("/customers/<int:user_id>/toggle")
@permission_required("customers.manage")
def customer_toggle(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == g.user.id:
        flash("نمی‌توانید حساب خودتان را غیرفعال کنید.", "error")
        return redirect(url_for("admin.customer_detail", user_id=user.id))
    user.is_active = not user.is_active
    user.updated_at = utcnow()
    db.session.commit()
    log_admin("customer.toggle", "user", user.id, new={"is_active": user.is_active})
    flash("وضعیت کاربر تغییر کرد.", "success")
    return redirect(url_for("admin.customer_detail", user_id=user.id))


@admin_bp.post("/customers/<int:user_id>/reset-password")
@permission_required("customers.manage")
def customer_reset_password(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    new_password = request.form.get("new_password", "")
    from app.utils.security import validate_password_strength

    errors = validate_password_strength(new_password, 8)
    if errors:
        for message in errors:
            flash(message, "error")
        return redirect(url_for("admin.customer_detail", user_id=user.id))
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.failed_login_count = 0
    user.locked_until = None
    user.updated_at = utcnow()
    db.session.commit()
    log_admin("customer.reset_password", "user", user.id)
    flash("رمز عبور موقت تنظیم شد؛ کاربر در ورود بعدی باید آن را تغییر دهد.", "success")
    return redirect(url_for("admin.customer_detail", user_id=user.id))


@admin_bp.route("/customers/create", methods=["GET", "POST"])
@permission_required("customers.manage")
def customer_create():
    if request.method == "POST":
        full_name = text_field("full_name", max_length=150)
        mobile = normalize_mobile(request.form.get("mobile", ""))
        password = request.form.get("password", "")
        errors = []
        if len(full_name) < 3:
            errors.append("نام و نام خانوادگی را وارد کنید.")
        if not is_valid_mobile(mobile):
            errors.append("شماره موبایل معتبر نیست.")
        if db.session.query(User.id).filter(User.mobile == mobile).first():
            errors.append("این شماره قبلاً ثبت شده است.")
        from app.utils.security import validate_password_strength

        errors.extend(validate_password_strength(password, 8))
        if errors:
            for message in errors:
                flash(message, "error")
        else:
            user = User(
                full_name=full_name,
                mobile=mobile,
                email=text_field("email", max_length=190) or None,
                password_hash=hash_password(password),
                is_active=True,
                is_verified=bool_field("is_verified"),
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.session.add(user)
            db.session.commit()
            auth_service.link_guest_orders(user)
            log_admin("customer.create", "user", user.id, new={"mobile": user.mobile})
            flash("مشتری جدید ایجاد شد.", "success")
            return redirect(url_for("admin.customer_detail", user_id=user.id))
    return render_template("admin/customers/form.html")
