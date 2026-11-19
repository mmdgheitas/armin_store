"""دکوراتورهای کنترل دسترسی (اجرا در Backend، نه فقط پنهان‌سازی در Frontend)."""
from __future__ import annotations

from functools import wraps

from flask import abort, flash, g, jsonify, redirect, request, url_for

from app.utils.http import wants_json


def _login_redirect():
    if wants_json():
        return jsonify({"success": False, "message": "برای ادامه باید وارد حساب شوید.", "login_required": True}), 401
    flash("برای ادامه باید وارد حساب کاربری خود شوید.", "warning")
    return redirect(url_for("auth.login", next=request.full_path if request.method == "GET" else request.path))


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if getattr(g, "user", None) is None:
            return _login_redirect()
        return view(*args, **kwargs)

    return wrapper


def verified_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user.is_verified:
            flash("برای این بخش باید شماره موبایل خود را تأیید کنید.", "warning")
            return redirect(url_for("auth.verify_mobile", next=request.path))
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if getattr(g, "user", None) is None:
            return _login_redirect()
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def permission_required(*permissions: str, any_of: bool = False):
    """بررسی مجوز مدیریتی؛ به‌طور پیش‌فرض همه مجوزهای داده‌شده لازم است."""

    def decorator(view):
        @wraps(view)
        @admin_required
        def wrapper(*args, **kwargs):
            keys = g.user.permission_keys
            if "*" in keys:
                return view(*args, **kwargs)
            if any_of:
                granted = any(key in keys for key in permissions)
            else:
                granted = all(key in keys for key in permissions)
            if not granted:
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def anonymous_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if getattr(g, "user", None) is not None:
            return redirect(url_for("account.dashboard"))
        return view(*args, **kwargs)

    return wrapper


def installed_required(view):
    """اجرای برنامه فقط پس از تکمیل نصب."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        from app.services.setup_service import is_installed

        if not is_installed():
            return redirect(url_for("setup.index"))
        return view(*args, **kwargs)

    return wrapper
