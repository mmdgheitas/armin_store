from __future__ import annotations

import datetime as dt
import re
import sqlite3
from functools import wraps
from typing import Callable

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app.repositories.users import get_user_by_id, update_user_last_login
from app.utils.persian import now_utc_iso

ADMIN_ROLES = {"super_admin", "manager", "order_operator", "content_editor"}

ROLE_LABELS: dict[str, str] = {
    "customer": "مشتری",
    "super_admin": "مدیر کل (Super Admin)",
    "manager": "مدیر فروشگاه",
    "order_operator": "اپراتور سفارشات",
    "content_editor": "ویرایشگر محتوا",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "super_admin": {
        "dashboard",
        "products",
        "categories",
        "orders",
        "orders_delete",
        "inventory",
        "customers",
        "reviews",
        "coupons",
        "cms",
        "redirects",
        "settings",
        "payments",
        "sms",
        "reports",
        "users_roles",
        "audit",
    },
    "manager": {
        "dashboard",
        "products",
        "categories",
        "orders",
        "inventory",
        "customers",
        "reviews",
        "coupons",
        "cms",
        "redirects",
        "settings",
        "payments",
        "sms",
        "reports",
        "audit",
    },
    "order_operator": {
        "dashboard",
        "orders",
        "inventory",
        "customers",
        "payments",
        "reports",
    },
    "content_editor": {
        "dashboard",
        "products",
        "categories",
        "reviews",
        "cms",
        "redirects",
    },
}


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="scrypt")


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash or not password:
        return False
    return check_password_hash(password_hash, password)


def validate_password_policy(password: str) -> tuple[bool, str]:
    if not password or len(password) < 8:
        return False, "رمز عبور باید حداقل ۸ کاراکتر باشد."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "رمز عبور باید شامل حداقل یک حرف انگلیسی و یک عدد باشد."
    return True, ""


def has_permission(user: dict | sqlite3.Row | None, permission: str) -> bool:
    if not user:
        return False
    role = user["role"]
    if role == "super_admin":
        return True
    return permission in ROLE_PERMISSIONS.get(role, set())


def login_user_session(
    conn: sqlite3.Connection,
    user: dict | sqlite3.Row,
    *,
    is_admin_session: bool = False,
) -> None:
    """Protect against session fixation by preserving cart/CSRF and regenerating session markers."""
    guest_cart = session.get("guest_cart")
    guest_coupon = session.get("guest_coupon_code")
    csrf_token = session.get("csrf_token")
    session.clear()
    if guest_cart:
        session["guest_cart"] = guest_cart
    if guest_coupon:
        session["guest_coupon_code"] = guest_coupon
    if csrf_token:
        session["csrf_token"] = csrf_token

    session["user_id"] = int(user["id"])
    session["user_role"] = user["role"]
    session["is_admin_auth"] = bool(is_admin_session and user["role"] in ADMIN_ROLES)
    session["last_activity_ts"] = int(dt.datetime.now(dt.timezone.utc).timestamp())
    session.permanent = True
    session.modified = True
    update_user_last_login(conn, int(user["id"]))


def logout_user_session() -> None:
    csrf_token = session.get("csrf_token")
    session.clear()
    if csrf_token:
        session["csrf_token"] = csrf_token
    session.modified = True


def load_current_user(conn: sqlite3.Connection) -> None:
    g.current_user = None
    uid = session.get("user_id")
    if not uid:
        return

    idle_minutes = int(current_app.config.get("PERMANENT_SESSION_LIFETIME").total_seconds() // 60)
    last_ts = session.get("last_activity_ts")
    now_ts = int(dt.datetime.now(dt.timezone.utc).timestamp())
    if last_ts and (now_ts - int(last_ts)) > idle_minutes * 60:
        logout_user_session()
        return

    user = get_user_by_id(conn, int(uid))
    if not user or not user["is_active"] or user["is_blocked"]:
        logout_user_session()
        return

    session["last_activity_ts"] = now_ts
    g.current_user = dict(user)


def login_required(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not getattr(g, "current_user", None):
            flash("لطفاً ابتدا وارد حساب کاربری خود شوید.", "warning")
            return redirect(url_for("auth.login", next=request.full_path))
        return fn(*args, **kwargs)

    return wrapper


def admin_required(permission: str | None = None) -> Callable:
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if not user or user["role"] not in ADMIN_ROLES or not session.get("is_admin_auth"):
                return redirect(url_for("admin.login", next=request.full_path))
            if permission and not has_permission(user, permission):
                abort(403)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
