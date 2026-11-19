"""مدیریت تبلیغات (نوار بالای سایت) و کدهای تخفیف."""
from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy import or_

from app.constants import COUPON_DISCOUNT_TYPES
from app.decorators import permission_required
from app.extensions import db
from app.models import Advertisement, Category, Coupon, Product
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, int_field, log_admin, text_field
from app.utils.dates import parse_date, utcnow
from app.utils.pagination import paginate

# ---------------------------------------------------------------------------)
# تبلیغات
# ---------------------------------------------------------------------------


def _parse_datetime_input(value: str | None):
    """پذیرش YYYY-MM-DD یا YYYY-MM-DDTHH:MM به وقت تهران (ذخیره UTC)."""
    if not value:
        return None
    value = value.strip().replace("T", " ")
    date_part = value.split(" ")[0]
    parsed = parse_date(date_part)
    if parsed is None:
        return None
    from datetime import datetime, timedelta

    hour, minute = 0, 0
    if " " in value:
        time_part = value.split(" ")[1]
        try:
            hour, minute = (int(part) for part in time_part.split(":")[:2])
        except (ValueError, TypeError):
            hour, minute = 0, 0
    return datetime(parsed.year, parsed.month, parsed.day, hour, minute) - timedelta(hours=3, minutes=30)


@admin_bp.get("/ads")
@permission_required("ads.manage")
def ads():
    items = db.session.query(Advertisement).order_by(Advertisement.sort_order, Advertisement.id).all()
    return render_template("admin/ads/list.html", ads=items, now=utcnow())


@admin_bp.route("/ads/create", methods=["POST"])
@admin_bp.route("/ads/<int:ad_id>/edit", methods=["POST"])
@permission_required("ads.manage")
def ad_save(ad_id: int | None = None):
    ad = db.session.get(Advertisement, ad_id) if ad_id else None
    if ad_id and ad is None:
        abort(404)
    text = text_field("text", max_length=300)
    if len(text) < 3:
        flash("متن تبلیغ را وارد کنید.", "error")
        return redirect(url_for("admin.ads"))
    if ad is None:
        ad = Advertisement(created_at=utcnow(), updated_at=utcnow())
        db.session.add(ad)
    ad.text = text
    ad.link = text_field("link", max_length=500) or None
    ad.background_color = text_field("background_color") or "#111827"
    ad.text_color = text_field("text_color") or "#ffffff"
    ad.starts_at = _parse_datetime_input(request.form.get("starts_at"))
    ad.expires_at = _parse_datetime_input(request.form.get("expires_at"))
    ad.is_active = bool_field("is_active") if "is_active" in request.form else False
    ad.sort_order = int_field("sort_order")
    ad.updated_at = utcnow()
    db.session.commit()
    log_admin("ad.save", "advertisement", ad.id, new={"text": ad.text})
    flash("تبلیغ ذخیره شد.", "success")
    return redirect(url_for("admin.ads"))


@admin_bp.post("/ads/<int:ad_id>/toggle")
@permission_required("ads.manage")
def ad_toggle(ad_id: int):
    ad = db.session.get(Advertisement, ad_id)
    if ad is None:
        abort(404)
    ad.is_active = not ad.is_active
    ad.updated_at = utcnow()
    db.session.commit()
    log_admin("ad.toggle", "advertisement", ad.id, new={"is_active": ad.is_active})
    flash("وضعیت تبلیغ تغییر کرد.", "success")
    return redirect(url_for("admin.ads"))


@admin_bp.post("/ads/<int:ad_id>/delete")
@permission_required("ads.manage")
def ad_delete(ad_id: int):
    ad = db.session.get(Advertisement, ad_id)
    if ad is None:
        abort(404)
    db.session.delete(ad)
    db.session.commit()
    log_admin("ad.delete", "advertisement", ad_id)
    flash("تبلیغ حذف شد.", "success")
    return redirect(url_for("admin.ads"))


# ---------------------------------------------------------------------------
# کوپن‌ها
# ---------------------------------------------------------------------------


@admin_bp.get("/coupons")
@permission_required("coupons.manage")
def coupons():
    stmt = db.session.query(Coupon).order_by(Coupon.created_at.desc())
    q = (request.args.get("q") or "").strip()
    if q:
        stmt = stmt.filter(Coupon.code.like(f"%{q.upper()}%"))
    page = paginate(stmt, per_page=25)
    return render_template("admin/coupons/list.html", page=page, q=q, now=utcnow())


@admin_bp.get("/coupons/<int:coupon_id>/edit")
@permission_required("coupons.manage")
def coupon_edit(coupon_id: int):
    coupon = db.session.get(Coupon, coupon_id)
    if coupon is None:
        abort(404)
    return render_template(
        "admin/coupons/form.html",
        coupon=coupon,
        categories=db.session.query(Category).order_by(Category.name).all(),
        products=db.session.query(Product).filter(Product.deleted_at.is_(None)).order_by(Product.name).limit(300).all(),
    )


@admin_bp.route("/coupons/create", methods=["GET", "POST"])
@admin_bp.route("/coupons/<int:coupon_id>/update", methods=["POST"])
@permission_required("coupons.manage")
def coupon_save(coupon_id: int | None = None):
    coupon = db.session.get(Coupon, coupon_id) if coupon_id else None
    if coupon_id and coupon is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "admin/coupons/form.html",
            coupon=None,
            categories=db.session.query(Category).order_by(Category.name).all(),
            products=db.session.query(Product).filter(Product.deleted_at.is_(None)).order_by(Product.name).limit(300).all(),
        )

    code = text_field("code").upper()
    discount_type = request.form.get("discount_type", "percent")
    discount_value = int_field("discount_value")
    errors: list[str] = []
    if len(code) < 3:
        errors.append("کد تخفیف را وارد کنید (حداقل ۳ کاراکتر).")
    if duplicate := (
        db.session.query(Coupon.id)
        .filter(Coupon.code == code, Coupon.id != (coupon.id if coupon else 0))
        .first()
    ):
        errors.append("این کد تخفیف قبلاً ثبت شده است.")
    if discount_type not in {"percent", "fixed"}:
        discount_type = "percent"
    if discount_value <= 0:
        errors.append("مقدار تخفیف باید بزرگ‌تر از صفر باشد.")
    if discount_type == "percent" and discount_value > 100:
        errors.append("درصد تخفیف نمی‌تواند بیشتر از ۱۰۰ باشد.")
    starts_at = _parse_datetime_input(request.form.get("starts_at"))
    expires_at = _parse_datetime_input(request.form.get("expires_at"))
    if starts_at and expires_at and expires_at <= starts_at:
        errors.append("تاریخ پایان باید بعد از تاریخ شروع باشد.")
    if errors:
        for message in errors:
            flash(message, "error")
        return render_template(
            "admin/coupons/form.html",
            coupon=coupon,
            form=request.form,
            categories=db.session.query(Category).order_by(Category.name).all(),
            products=db.session.query(Product).filter(Product.deleted_at.is_(None)).order_by(Product.name).limit(300).all(),
        )

    if coupon is None:
        coupon = Coupon(created_at=utcnow(), updated_at=utcnow())
        db.session.add(coupon)
    coupon.code = code
    coupon.description = text_field("description", max_length=255) or None
    coupon.discount_type = discount_type
    coupon.discount_value = discount_value
    coupon.minimum_order = int_field("minimum_order")
    coupon.maximum_discount = int_field("maximum_discount")
    coupon.usage_limit = int_field("usage_limit")
    coupon.per_user_limit = max(0, int_field("per_user_limit", 1))
    coupon.starts_at = starts_at
    coupon.expires_at = expires_at
    coupon.is_active = "is_active" in request.form
    coupon.applies_to = request.form.get("applies_to", "all")
    coupon.category_id = int_field("category_id") or None
    coupon.product_id = int_field("product_id") or None
    coupon.updated_at = utcnow()
    db.session.commit()
    log_admin("coupon.save", "coupon", coupon.id, new={"code": coupon.code})
    flash("کد تخفیف ذخیره شد.", "success")
    return redirect(url_for("admin.coupons"))


@admin_bp.post("/coupons/<int:coupon_id>/toggle")
@permission_required("coupons.manage")
def coupon_toggle(coupon_id: int):
    coupon = db.session.get(Coupon, coupon_id)
    if coupon is None:
        abort(404)
    coupon.is_active = not coupon.is_active
    coupon.updated_at = utcnow()
    db.session.commit()
    log_admin("coupon.toggle", "coupon", coupon.id, new={"is_active": coupon.is_active})
    flash("وضعیت کوپن تغییر کرد.", "success")
    return redirect(url_for("admin.coupons"))


@admin_bp.post("/coupons/<int:coupon_id>/delete")
@permission_required("coupons.manage")
def coupon_delete(coupon_id: int):
    from app.models import Order

    coupon = db.session.get(Coupon, coupon_id)
    if coupon is None:
        abort(404)
    used = db.session.query(Order.id).filter(Order.coupon_id == coupon.id).first()
    if used:
        coupon.is_active = False
        db.session.commit()
        flash("این کوپن در سفارش‌ها استفاده شده است؛ به‌جای حذف، غیرفعال شد.", "warning")
        return redirect(url_for("admin.coupons"))
    db.session.delete(coupon)
    db.session.commit()
    log_admin("coupon.delete", "coupon", coupon_id)
    flash("کوپن حذف شد.", "success")
    return redirect(url_for("admin.coupons"))
