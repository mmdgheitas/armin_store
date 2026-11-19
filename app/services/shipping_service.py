"""سرویس روش‌های ارسال و محاسبه هزینه."""
from __future__ import annotations

from app.extensions import db
from app.models.shipping import ShippingMethod


def active_methods() -> list[ShippingMethod]:
    return (
        db.session.query(ShippingMethod)
        .filter(ShippingMethod.is_active.is_(True))
        .order_by(ShippingMethod.sort_order, ShippingMethod.id)
        .all()
    )


def all_methods() -> list[ShippingMethod]:
    return (
        db.session.query(ShippingMethod)
        .order_by(ShippingMethod.sort_order, ShippingMethod.id)
        .all()
    )


def get_method(method_id) -> ShippingMethod | None:
    try:
        method = db.session.get(ShippingMethod, int(method_id))
    except (TypeError, ValueError):
        return None
    if method is None or not method.is_active:
        return None
    return method


def calculate(method: ShippingMethod | None, address: dict | None, subtotal: int, has_physical: bool) -> int:
    """هزینه ارسال؛ برای سفارش‌های فقط دیجیتال صفر است."""
    if not has_physical:
        return 0
    if method is None:
        return 0
    address = address or {}
    return method.cost_for(address.get("province"), address.get("city"), subtotal)


def free_shipping_threshold(method: ShippingMethod | None) -> int:
    if method and method.shipping_type == "free_threshold":
        return int(method.free_threshold or 0)
    return 0
