"""مدیریت کاربران پنل و نقش‌ها/مجوزها (RBAC)."""
from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, or_

from app.constants import PERMISSIONS
from app.decorators import permission_required
from app.extensions import db
from app.models import Permission, Role, User
from app.models.user import user_roles
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, log_admin, text_field
from app.utils.dates import utcnow
from app.utils.pagination import paginate
from app.utils.security import (
    hash_password,
    is_valid_mobile,
    normalize_mobile,
    validate_password_strength,
)


def _super_admin_role() -> Role | None:
    return db.session.query(Role).filter(Role.name == "super_admin").first()


def _active_super_admins(exclude_user_id: int | None = None) -> int:
    role = _super_admin_role()
    if role is None:
        return 0
    query = (
        db.session.query(func.count(User.id))
        .join(User.roles)
        .filter(Role.id == role.id, User.is_active.is_(True), User.deleted_at.is_(None))
    )
    if exclude_user_id:
        query = query.filter(User.id != exclude_user_id)
    return int(query.scalar() or 0)


@admin_bp.get("/users")
@permission_required("users.manage")
def users():
    q = (request.args.get("q") or "").strip()
    stmt = db.session.query(User).filter(User.deleted_at.is_(None))
    if request.args.get("staff") == "1":
        stmt = stmt.filter(User.id.in_(db.session.query(User.id).join(User.roles)))
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(or_(User.full_name.like(like), User.mobile.like(like)))
    page = paginate(stmt.order_by(User.created_at.desc()), per_page=25)
    return render_template("admin/users/list.html", page=page, q=q, roles=db.session.query(Role).all())


@admin_bp.route("/users/create", methods=["GET", "POST"])
@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@permission_required("users.manage")
def user_save(user_id: int | None = None):
    user = db.session.get(User, user_id) if user_id else None
    if user_id and user is None:
        abort(404)
    roles = db.session.query(Role).order_by(Role.name).all()

    if request.method == "POST":
        full_name = text_field("full_name", max_length=150)
        mobile = normalize_mobile(request.form.get("mobile", ""))
        role_ids = [int(v) for v in request.form.getlist("role_ids[]") if v.isdigit()]
        selected_roles = db.session.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []
        errors = []
        if len(full_name) < 3:
            errors.append("نام کاربر را وارد کنید.")
        if not is_valid_mobile(mobile):
            errors.append("شماره موبایل معتبر نیست.")
        duplicate = (
            db.session.query(User.id)
            .filter(User.mobile == mobile, User.id != (user.id if user else 0))
            .first()
        )
        if duplicate:
            errors.append("این شماره برای کاربر دیگری ثبت شده است.")
        password = request.form.get("password", "")
        if user is None and not password:
            errors.append("رمز عبور را وارد کنید.")
        if password:
            errors.extend(validate_password_strength(password, 8))

        super_role = _super_admin_role()
        if user is not None and super_role is not None and super_role in user.roles:
            if super_role not in selected_roles:
                errors.append("نمی‌توان نقش «مدیر کل» را از آخرین مدیر کل حذف کرد.")
        if user is not None and user.id == g.user.id and "is_active" not in request.form:
            errors.append("نمی‌توانید حساب خودتان را غیرفعال کنید.")

        if errors:
            for message in errors:
                flash(message, "error")
        else:
            if user is None:
                user = User(created_at=utcnow(), updated_at=utcnow(), is_verified=True)
                db.session.add(user)
                log_action = "user.create"
                old = None
            else:
                log_action = "user.update"
                old = {"roles": user.role_names, "is_active": user.is_active}
            user.full_name = full_name
            user.mobile = mobile
            user.email = text_field("email", max_length=190) or None
            user.is_active = bool_field("is_active") if "is_active" in request.form else (user.is_active if user_id else True)
            user.roles = selected_roles
            if password:
                user.password_hash = hash_password(password)
                user.must_change_password = bool_field("must_change_password")
            user.updated_at = utcnow()
            db.session.commit()
            log_admin(log_action, "user", user.id, old=old, new={"roles": user.role_names, "active": user.is_active})
            flash("کاربر ذخیره شد.", "success")
            return redirect(url_for("admin.users"))

    return render_template("admin/users/form.html", user=user, roles=roles)


@admin_bp.post("/users/<int:user_id>/toggle")
@permission_required("users.manage")
def user_toggle(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == g.user.id:
        flash("نمی‌توانید حساب خودتان را غیرفعال کنید.", "error")
        return redirect(url_for("admin.users"))
    if user.is_active and _super_admin_role() in user.roles and _active_super_admins(exclude_user_id=user.id) == 0:
        flash("حداقل یک مدیر کل فعال باید باقی بماند.", "error")
        return redirect(url_for("admin.users"))
    user.is_active = not user.is_active
    user.updated_at = utcnow()
    db.session.commit()
    log_admin("user.toggle", "user", user.id, new={"is_active": user.is_active})
    flash("وضعیت کاربر تغییر کرد.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.get("/roles")
@permission_required("users.manage")
def roles():
    items = db.session.query(Role).order_by(Role.name).all()
    counts = {
        row[0]: row[1]
        for row in db.session.query(user_roles.c.role_id, func.count())
        .group_by(user_roles.c.role_id)
        .all()
    }
    return render_template("admin/users/roles.html", roles=items, permissions=PERMISSIONS, counts=counts)


@admin_bp.route("/roles/create", methods=["POST"])
@admin_bp.route("/roles/<int:role_id>/edit", methods=["POST"])
@permission_required("users.manage")
def role_save(role_id: int | None = None):
    role = db.session.get(Role, role_id) if role_id else None
    if role_id and role is None:
        abort(404)
    name = (text_field("name") or "").lower().replace(" ", "_")
    title = text_field("title", max_length=120)
    if not name and role is None:
        flash("نام نقش را وارد کنید.", "error")
        return redirect(url_for("admin.roles"))
    if role and role.name == "super_admin":
        # نقش مدیر کل همیشه همه مجوزها را دارد
        role.title = title or role.title
        db.session.commit()
        flash("عنوان نقش مدیر کل به‌روزرسانی شد.", "success")
        return redirect(url_for("admin.roles"))

    keys = request.form.getlist("permissions[]")
    permissions = (
        db.session.query(Permission).filter(Permission.key.in_(keys)).all() if keys else []
    )
    if role is None:
        duplicate = db.session.query(Role.id).filter(Role.name == name).first()
        if duplicate:
            flash("نقشی با این نام وجود دارد.", "error")
            return redirect(url_for("admin.roles"))
        role = Role(name=name, title=title or name, created_at=utcnow())
        db.session.add(role)
    role.title = title or role.name
    role.permissions = permissions
    db.session.commit()
    log_admin("role.save", "role", role.id, new={"name": role.name, "permissions": keys})
    flash("نقش ذخیره شد.", "success")
    return redirect(url_for("admin.roles"))


@admin_bp.post("/roles/<int:role_id>/delete")
@permission_required("users.manage")
def role_delete(role_id: int):
    role = db.session.get(Role, role_id)
    if role is None:
        abort(404)
    if role.name == "super_admin":
        flash("نقش مدیر کل قابل حذف نیست.", "error")
        return redirect(url_for("admin.roles"))
    if role.users:
        flash("این نقش به کاربرانی اختصاص یافته است؛ ابتدا نقش آن‌ها را تغییر دهید.", "error")
        return redirect(url_for("admin.roles"))
    db.session.delete(role)
    db.session.commit()
    log_admin("role.delete", "role", role_id)
    flash("نقش حذف شد.", "success")
    return redirect(url_for("admin.roles"))
