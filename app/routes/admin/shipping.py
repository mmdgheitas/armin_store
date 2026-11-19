"""مدیریت روش‌های ارسال و هزینه‌ها."""
from __future__ import annotations

import json

from flask import abort, flash, redirect, render_template, request, url_for

from app.constants import SHIPPING_TYPES
from app.decorators import permission_required
from app.extensions import db
from app.models import ShippingMethod
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, int_field, log_admin, text_field
from app.utils.dates import utcnow


@admin_bp.get("/shipping")
@permission_required("shipping.manage")
def shipping():
    methods = db.session.query(ShippingMethod).order_by(ShippingMethod.sort_order, ShippingMethod.id).all()
    return render_template("admin/shipping/list.html", methods=methods, types=SHIPPING_TYPES)


def _parse_configuration(raw: str) -> tuple[dict | None, str | None]:
    """ورودی JSON: {"province_costs": {"تهران": 50000}, "city_costs": {"تهران|تهران": 30000}}"""
    raw = (raw or "").strip()
    if not raw:
        return {}, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"ساختار JSON تنظیمات نامعتبر است: {exc.msg}"
    if not isinstance(data, dict):
        return None, "تنظیمات باید یک شیء JSON باشد."
    cleaned: dict = {}
    for section in ("province_costs", "city_costs"):
        values = data.get(section) or {}
        if not isinstance(values, dict):
            return None, f"بخش «{section}» باید از نوع کلید-مقدار باشد."
        section_costs = {}
        for key, value in values.items():
            try:
                cost = int(value)
            except (TypeError, ValueError):
                return None, f"هزینه «{key}» در «{section}» عددی نیست."
            if cost < 0:
                return None, "هزینه ارسال نمی‌تواند منفی باشد."
            section_costs[str(key)[:80]] = cost
        cleaned[section] = section_costs
    return cleaned, None


@admin_bp.route("/shipping/create", methods=["POST"])
@admin_bp.route("/shipping/<int:method_id>/edit", methods=["POST"])
@permission_required("shipping.manage")
def shipping_save(method_id: int | None = None):
    method = db.session.get(ShippingMethod, method_id) if method_id else None
    if method_id and method is None:
        abort(404)
    name = text_field("name", max_length=120)
    shipping_type = request.form.get("shipping_type", "flat")
    if len(name) < 2:
        flash("نام روش ارسال را وارد کنید.", "error")
        return redirect(url_for("admin.shipping"))
    if shipping_type not in dict(SHIPPING_TYPES):
        shipping_type = "flat"
    configuration, error = _parse_configuration(request.form.get("configuration_json", ""))
    if error:
        flash(error, "error")
        return redirect(url_for("admin.shipping"))

    if method is None:
        method = ShippingMethod(created_at=utcnow(), updated_at=utcnow())
        db.session.add(method)
    method.name = name
    method.description = text_field("description", max_length=400) or None
    method.shipping_type = shipping_type
    method.base_cost = max(0, int_field("base_cost"))
    method.free_threshold = max(0, int_field("free_threshold"))
    method.configuration_json = configuration or {}
    method.estimated_days = text_field("estimated_days", max_length=50) or None
    method.is_active = "is_active" in request.form
    method.sort_order = int_field("sort_order")
    method.updated_at = utcnow()
    db.session.commit()
    log_admin("shipping.save", "shipping_method", method.id, new={"name": method.name})
    flash("روش ارسال ذخیره شد.", "success")
    return redirect(url_for("admin.shipping"))


@admin_bp.post("/shipping/<int:method_id>/toggle")
@permission_required("shipping.manage")
def shipping_toggle(method_id: int):
    method = db.session.get(ShippingMethod, method_id)
    if method is None:
        abort(404)
    method.is_active = not method.is_active
    method.updated_at = utcnow()
    db.session.commit()
    log_admin("shipping.toggle", "shipping_method", method.id, new={"is_active": method.is_active})
    flash("وضعیت روش ارسال تغییر کرد.", "success")
    return redirect(url_for("admin.shipping"))


@admin_bp.post("/shipping/<int:method_id>/delete")
@permission_required("shipping.manage")
def shipping_delete(method_id: int):
    from app.models import Order

    method = db.session.get(ShippingMethod, method_id)
    if method is None:
        abort(404)
    used = db.session.query(Order.id).filter(Order.shipping_method_id == method.id).first()
    if used:
        method.is_active = False
        db.session.commit()
        flash("این روش در سفارش‌ها استفاده شده؛ به‌جای حذف غیرفعال شد.", "warning")
        return redirect(url_for("admin.shipping"))
    db.session.delete(method)
    db.session.commit()
    log_admin("shipping.delete", "shipping_method", method_id)
    flash("روش ارسال حذف شد.", "success")
    return redirect(url_for("admin.shipping"))
