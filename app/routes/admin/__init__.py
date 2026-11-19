"""پنل مدیریت: Blueprint اصلی و محافظت سراسری."""
from __future__ import annotations

from flask import Blueprint, abort, g, redirect, request, url_for

from app.utils.http import wants_json

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.before_request
def _require_admin():
    user = getattr(g, "user", None)
    if user is None:
        if wants_json():
            abort(401)
        return redirect(url_for("auth.login", next=request.full_path))
    if not user.is_admin:
        abort(403)
    return None


@admin_bp.after_request
def _no_cache(response):
    response.headers["Cache-Control"] = "no-store, private"
    return response


# ثبت مسیرهای زیرمجموعه (وارد کردن ماژول‌ها باعث ثبت Route ها می‌شود)
from app.routes.admin import (  # noqa: E402,F401
    audit,
    backups,
    catalog,
    content,
    customers,
    dashboard,
    integrations,
    marketing,
    moderation,
    orders,
    reports,
    settings,
    shipping,
    support,
    users,
)
