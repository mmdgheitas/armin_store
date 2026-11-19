"""
سرویس سبد خرید بر پایه Session.

قیمت‌ها هرگز از Session خوانده نمی‌شوند؛ در هر درخواست از دیتابیس محاسبه
می‌شوند تا دستکاری سمت کلاینت بی‌اثر باشد.
"""
from __future__ import annotations

from flask import session

from app.extensions import db
from app.models.catalog import Category, Product, ProductVariant
from app.services import coupon_service, inventory_service, settings_service, shipping_service
from app.utils.money import percent_of

CART_KEY = "cart"
COUPON_KEY = "cart_coupon"
SHIPPING_KEY = "cart_shipping_method"
MAX_LINES = 50
MAX_QUANTITY = 99


def raw_cart() -> dict[str, int]:
    data = session.get(CART_KEY) or {}
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, value in data.items():
        try:
            variant_id = int(key)
            quantity = int(value)
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            cleaned[str(variant_id)] = min(quantity, MAX_QUANTITY)
    return cleaned


def _save(mapping: dict[str, int]) -> None:
    if mapping:
        session[CART_KEY] = mapping
    else:
        session.pop(CART_KEY, None)
    session.modified = True


def item_count() -> int:
    return sum(raw_cart().values())


def add(variant_id: int, quantity: int = 1) -> tuple[bool, str]:
    """افزودن محصول به سبد با اعتبارسنجی موجودی در سرور."""
    try:
        variant_id = int(variant_id)
        quantity = int(quantity)
    except (TypeError, ValueError):
        return False, "درخواست نامعتبر است."
    quantity = max(1, min(quantity, MAX_QUANTITY))

    variant = db.session.get(ProductVariant, variant_id)
    if variant is None or not variant.is_active:
        return False, "این تنوع محصول در دسترس نیست."
    product = variant.product
    if product is None or product.deleted_at is not None or not product.is_active:
        return False, "این محصول در دسترس نیست."

    cart = raw_cart()
    if str(variant_id) not in cart and len(cart) >= MAX_LINES:
        return False, "تعداد اقلام سبد خرید بیش از حد مجاز است."

    current = cart.get(str(variant_id), 0)
    new_quantity = min(current + quantity, MAX_QUANTITY)

    if not product.is_digital:
        available = inventory_service.available_quantity(variant)
        if available <= 0:
            return False, "این محصول در حال حاضر موجود نیست."
        if new_quantity > available:
            return False, f"حداکثر موجودی قابل خرید این محصول {available} عدد است."
    else:
        if not variant.is_active:
            return False, "این محصول در دسترس نیست."

    cart[str(variant_id)] = new_quantity
    _save(cart)
    return True, f"«{product.name}» به سبد خرید اضافه شد."


def set_quantity(variant_id: int, quantity: int) -> tuple[bool, str]:
    try:
        variant_id = int(variant_id)
        quantity = int(quantity)
    except (TypeError, ValueError):
        return False, "درخواست نامعتبر است."
    cart = raw_cart()
    key = str(variant_id)
    if key not in cart:
        return False, "این کالا در سبد خرید نیست."
    if quantity <= 0:
        return remove(variant_id)
    quantity = min(quantity, MAX_QUANTITY)

    variant = db.session.get(ProductVariant, variant_id)
    if variant is None:
        return False, "این محصول در دسترس نیست."
    if not variant.product.is_digital:
        available = inventory_service.available_quantity(variant)
        if quantity > available:
            return False, f"موجودی کافی نیست. حداکثر {available} عدد قابل سفارش است."
    cart[key] = quantity
    _save(cart)
    return True, "سبد خرید به‌روزرسانی شد."


def remove(variant_id) -> tuple[bool, str]:
    cart = raw_cart()
    cart.pop(str(variant_id), None)
    _save(cart)
    return True, "کالا از سبد خرید حذف شد."


def clear() -> None:
    session.pop(CART_KEY, None)
    session.pop(COUPON_KEY, None)
    session.pop(SHIPPING_KEY, None)
    session.modified = True


def set_coupon(code: str) -> None:
    code = (code or "").strip()
    if code:
        session[COUPON_KEY] = code
    else:
        session.pop(COUPON_KEY, None)
    session.modified = True


def get_coupon_code() -> str:
    return str(session.get(COUPON_KEY) or "")


def clear_coupon() -> None:
    session.pop(COUPON_KEY, None)
    session.modified = True


def set_shipping_method(method_id) -> None:
    try:
        session[SHIPPING_KEY] = int(method_id)
        session.modified = True
    except (TypeError, ValueError):
        session.pop(SHIPPING_KEY, None)


def get_shipping_method_id():
    return session.get(SHIPPING_KEY)


def build_items() -> list[dict]:
    """ساخت اقلام سبد از دیتابیس با آخرین قیمت و موجودی."""
    cart = raw_cart()
    if not cart:
        return []
    try:
        variant_ids = [int(key) for key in cart.keys()]
    except ValueError:
        return []

    rows = (
        db.session.query(ProductVariant, Product, Category)
        .join(Product, Product.id == ProductVariant.product_id)
        .outerjoin(Category, Category.id == Product.category_id)
        .filter(ProductVariant.id.in_(variant_ids))
        .all()
    )
    availability = inventory_service.available_map(variant_ids)
    found = {variant.id: (variant, product, category) for variant, product, category in rows}

    items: list[dict] = []
    for variant_id in variant_ids:
        quantity = cart.get(str(variant_id), 0)
        if quantity <= 0:
            continue
        entry = found.get(variant_id)
        if entry is None:
            continue
        variant, product, category = entry
        unit_price = int(variant.effective_price or 0)
        is_active = (
            variant.is_active
            and product.is_active
            and product.deleted_at is None
        )
        available = availability.get(variant_id, 0) if not product.is_digital else MAX_QUANTITY
        items.append(
            {
                "variant_id": variant_id,
                "product_id": product.id,
                "category_id": product.category_id,
                "variant": variant,
                "product": product,
                "category": category,
                "product_name": product.name,
                "product_slug": product.slug,
                "variant_label": variant.label_with_names if variant.attributes_json else "",
                "sku": variant.sku,
                "unit_price": unit_price,
                "compare_at_price": int(variant.compare_at_price or 0),
                "quantity": quantity,
                "line_total": unit_price * quantity,
                "available": available,
                "stock_ok": product.is_digital or available >= quantity,
                "is_active": is_active,
                "is_digital": product.is_digital,
                "delivery_type": "digital" if product.is_digital else "physical",
                "image_url": product.image_url,
                "max_quantity": MAX_QUANTITY if product.is_digital else max(1, available),
            }
        )
    return items


def summary(user=None, address: dict | None = None) -> dict:
    """محاسبه کامل مبالغ سبد خرید (Server-side)."""
    items = build_items()
    valid_items = [i for i in items if i["is_active"]]
    subtotal = sum(i["line_total"] for i in valid_items if i["stock_ok"])
    has_physical = any(not i["is_digital"] for i in items)
    issues: list[str] = []
    for item in items:
        if not item["is_active"]:
            issues.append(f"«{item['product_name']}» دیگر قابل خرید نیست و از مبلغ حذف شد.")
        elif not item["stock_ok"]:
            issues.append(
                f"موجودی «{item['product_name']}» کافی نیست (موجودی: {item['available']})."
            )

    coupon_code = get_coupon_code()
    coupon = None
    discount = 0
    coupon_message = ""
    if coupon_code and valid_items:
        result = coupon_service.validate(coupon_code, valid_items, user=user)
        if result["ok"]:
            coupon = result["coupon"]
            discount = result["discount"]
            coupon_message = result["message"]
        else:
            coupon_message = result["message"]
            issues.append(result["message"])

    shipping_method = None
    shipping_cost = 0
    if has_physical:
        shipping_method = shipping_service.get_method(get_shipping_method_id())
        if shipping_method is not None:
            shipping_cost = shipping_service.calculate(
                shipping_method, address, subtotal, has_physical
            )

    tax_amount = 0
    if settings_service.get_bool("tax_enabled", False):
        tax_amount = percent_of(max(0, subtotal - discount), settings_service.get_int("tax_percent", 0))

    total = max(0, subtotal - discount) + shipping_cost + tax_amount
    count = sum(i["quantity"] for i in items)

    return {
        "items": items,
        "count": count,
        "lines": len(items),
        "subtotal": subtotal,
        "discount": discount,
        "coupon_code": coupon_code,
        "coupon": coupon,
        "coupon_message": coupon_message,
        "shipping_method": shipping_method,
        "shipping_cost": shipping_cost,
        "tax_amount": tax_amount,
        "total": total,
        "has_physical": has_physical,
        "has_digital": any(i["is_digital"] for i in items),
        "issues": issues,
        "is_empty": len(items) == 0,
        "stock_ok": all(i["stock_ok"] and i["is_active"] for i in items) if items else True,
    }
