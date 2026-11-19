"""
سرویس انبار: کنترل موجودی، رزرو و آزادسازی، حرکات انبار.

اصل طراحی:
  * stock_quantity = موجودی فیزیکی انبار (با رزرو کاهش نمی‌یابد)
  * موجودی قابل فروش = stock_quantity - مجموع رزروهای فعال و منقضی‌نشده
  * رزرو با یک دستور اتمیک INSERT..SELECT انجام می‌شود تا دو درخواست هم‌زمان
    نتوانند آخرین موجودی را بفروشند.
  * هیچ‌گاه موجودی منفی نمی‌شود (گارد در سطح دستور SQL).
"""
from __future__ import annotations

from datetime import timedelta

from flask import current_app
from sqlalchemy import func, text

from app.extensions import db
from app.models.catalog import Product, ProductVariant
from app.models.inventory import InventoryMovement, InventoryReservation
from app.utils.dates import utcnow


class StockError(Exception):
    """خطای موجودی با پیام قابل نمایش به کاربر."""

    def __init__(self, message: str, variant_id: int | None = None):
        super().__init__(message)
        self.message = message
        self.variant_id = variant_id


def _active_reserved(variant_id: int) -> int:
    now = utcnow()
    total = (
        db.session.query(func.coalesce(func.sum(InventoryReservation.quantity), 0))
        .filter(
            InventoryReservation.variant_id == variant_id,
            InventoryReservation.status == "active",
            InventoryReservation.expires_at > now,
        )
        .scalar()
    )
    return int(total or 0)


def available_quantity(variant: ProductVariant | int | None) -> int:
    if variant is None:
        return 0
    variant_id = variant.id if isinstance(variant, ProductVariant) else int(variant)
    variant_obj = variant if isinstance(variant, ProductVariant) else db.session.get(ProductVariant, variant_id)
    if variant_obj is None:
        return 0
    stock = int(variant_obj.stock_quantity or 0)
    if not variant_obj.is_active:
        return 0
    return max(0, stock - _active_reserved(variant_id))


def available_map(variant_ids: list[int]) -> dict[int, int]:
    """محاسبه یکجای موجودی قابل فروش چند تنوع (بدون N+1)."""
    if not variant_ids:
        return {}
    now = utcnow()
    rows = (
        db.session.query(
            ProductVariant.id,
            ProductVariant.stock_quantity,
            func.coalesce(func.sum(InventoryReservation.quantity), 0).label("reserved"),
        )
        .outerjoin(
            InventoryReservation,
            (InventoryReservation.variant_id == ProductVariant.id)
            & (InventoryReservation.status == "active")
            & (InventoryReservation.expires_at > now),
        )
        .filter(ProductVariant.id.in_(variant_ids))
        .group_by(ProductVariant.id, ProductVariant.stock_quantity)
        .all()
    )
    return {row[0]: max(0, int(row[1] or 0) - int(row[2] or 0)) for row in rows}


def record_movement(
    variant: ProductVariant,
    movement_type: str,
    quantity: int,
    previous_stock: int,
    new_stock: int,
    reason: str | None = None,
    admin_id: int | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    unit_cost: int = 0,
) -> InventoryMovement:
    movement = InventoryMovement(
        product_id=variant.product_id if variant else None,
        variant_id=variant.id if variant else None,
        movement_type=movement_type,
        quantity=quantity,
        previous_stock=previous_stock,
        new_stock=new_stock,
        unit_cost=int(unit_cost or 0),
        reason=(reason or "")[:300] or None,
        reference_type=reference_type,
        reference_id=reference_id,
        admin_id=admin_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(movement)
    return movement


def reserve_stock(order, items: list[dict], minutes: int | None = None) -> None:
    """
    رزرو اتمیک موجودی برای اقلام سفارش.
    items: [{"variant_id": int, "quantity": int, "product_id": int}]
    در صورت عدم موجودی، StockError پرتاب می‌شود و کل تراکنش باید Rollback شود.
    """
    from app.services import settings_service

    if minutes is None:
        minutes = max(1, settings_service.get_int("reservation_minutes", 20))
    now = utcnow()
    expires = now + timedelta(minutes=minutes)

    for item in items:
        variant_id = int(item["variant_id"])
        quantity = int(item["quantity"])
        if quantity <= 0:
            continue
        result = db.session.execute(
            text(
                """
                INSERT INTO inventory_reservations
                    (order_id, variant_id, quantity, status, expires_at, created_at, updated_at)
                SELECT :order_id, :variant_id, :quantity, 'active', :expires_at, :now, :now
                WHERE :quantity <= (
                    SELECT COALESCE(stock_quantity, 0) FROM product_variants
                    WHERE id = :variant_id AND is_active = 1
                ) - COALESCE((
                    SELECT SUM(quantity) FROM inventory_reservations
                    WHERE variant_id = :variant_id AND status = 'active' AND expires_at > :now
                ), 0)
                """
            ),
            {
                "order_id": order.id,
                "variant_id": variant_id,
                "quantity": quantity,
                "expires_at": expires,
                "now": now,
            },
        )
        if result.rowcount != 1:
            variant = db.session.get(ProductVariant, variant_id)
            label = variant.product.name if variant and variant.product else "کالای انتخابی"
            raise StockError(
                f"موجودی «{label}» برای این تعداد کافی نیست. لطفاً تعداد یا تنوع دیگری انتخاب کنید.",
                variant_id,
            )
        variant = db.session.get(ProductVariant, variant_id)
        if variant is not None:
            record_movement(
                variant,
                "reserve",
                quantity,
                variant.stock_quantity,
                variant.stock_quantity,
                reason="رزرو موجودی هنگام شروع پرداخت",
                reference_type="order",
                reference_id=order.id,
            )
    db.session.flush()


def consume_reservations(order) -> None:
    """تبدیل رزرو به فروش قطعی و کسر موجودی (فقط یک‌بار)."""
    reservations = (
        db.session.query(InventoryReservation)
        .filter(InventoryReservation.order_id == order.id, InventoryReservation.status == "active")
        .all()
    )
    for reservation in reservations:
        variant = db.session.get(ProductVariant, reservation.variant_id)
        if variant is None:
            reservation.status = "consumed"
            continue
        previous = int(variant.stock_quantity or 0)
        quantity = int(reservation.quantity)
        if previous < quantity:
            # نباید رخ دهد؛ برای جلوگیری از موجودی منفی، مقدار موجود کسر می‌شود.
            current_app.logger.error(
                "Stock underflow guard order=%s variant=%s stock=%s qty=%s",
                order.id, variant.id, previous, quantity,
            )
            quantity = previous
        variant.stock_quantity = previous - quantity
        reservation.status = "consumed"
        record_movement(
            variant,
            "sale",
            -quantity,
            previous,
            variant.stock_quantity,
            reason=f"فروش سفارش {order.order_number}",
            reference_type="order",
            reference_id=order.id,
            unit_cost=variant.cost_price or 0,
        )
        product = db.session.get(Product, variant.product_id)
        if product is not None:
            product.sold_count = (product.sold_count or 0) + quantity
    db.session.flush()


def release_reservations(order, status: str = "released", reason: str = "آزادسازی رزرو") -> int:
    """آزادسازی رزروها بدون تغییر موجودی فیزیکی."""
    reservations = (
        db.session.query(InventoryReservation)
        .filter(InventoryReservation.order_id == order.id, InventoryReservation.status == "active")
        .all()
    )
    for reservation in reservations:
        reservation.status = status
        variant = db.session.get(ProductVariant, reservation.variant_id)
        if variant is not None:
            record_movement(
                variant,
                "release",
                reservation.quantity,
                variant.stock_quantity,
                variant.stock_quantity,
                reason=reason,
                reference_type="order",
                reference_id=order.id,
            )
    db.session.flush()
    return len(reservations)


def has_active_reservation(order) -> bool:
    return (
        db.session.query(InventoryReservation.id)
        .filter(InventoryReservation.order_id == order.id, InventoryReservation.status == "active")
        .first()
        is not None
    )


def restock_for_return(order, admin_id: int | None = None, reason: str = "مرجوعی سفارش") -> None:
    """بازگرداندن موجودی اقلام فیزیکی یک سفارش به انبار (یک‌بار)."""
    for item in order.items:
        if item.variant_id is None or item.delivery_type == "digital":
            continue
        variant = db.session.get(ProductVariant, item.variant_id)
        if variant is None:
            continue
        previous = int(variant.stock_quantity or 0)
        variant.stock_quantity = previous + int(item.quantity)
        record_movement(
            variant,
            "return",
            int(item.quantity),
            previous,
            variant.stock_quantity,
            reason=f"{reason} — {order.order_number}",
            admin_id=admin_id,
            reference_type="order",
            reference_id=order.id,
            unit_cost=variant.cost_price or 0,
        )
    db.session.flush()


def adjust_stock(
    variant: ProductVariant,
    movement_type: str,
    quantity: int,
    reason: str,
    admin_id: int | None = None,
    unit_cost: int | None = None,
    absolute: bool = False,
) -> InventoryMovement | None:
    """
    تغییر موجودی توسط مدیر.
    movement_type: in | out | adjust
    اگر absolute=True، quantity مقدار نهایی موجودی است (اصلاح موجودی).
    """
    if variant is None:
        return None
    previous = int(variant.stock_quantity or 0)
    quantity = int(quantity)

    if movement_type == "in":
        if quantity <= 0:
            raise StockError("تعداد ورود کالا باید بزرگ‌تر از صفر باشد.")
        new_stock = previous + quantity
        delta = quantity
    elif movement_type == "out":
        if quantity <= 0:
            raise StockError("تعداد خروج کالا باید بزرگ‌تر از صفر باشد.")
        if quantity > previous:
            raise StockError("موجودی برای این خروج کافی نیست.")
        new_stock = previous - quantity
        delta = -quantity
    elif movement_type == "adjust":
        if absolute:
            new_stock = quantity
            delta = new_stock - previous
        else:
            new_stock = previous + quantity
            delta = quantity
        if new_stock < 0:
            raise StockError("موجودی نمی‌تواند منفی باشد.")
    else:
        raise StockError("نوع عملیات انبار نامعتبر است.")

    if not reason or len(reason.strip()) < 3:
        raise StockError("ثبت دلیل تغییر موجودی الزامی است.")

    variant.stock_quantity = new_stock
    if unit_cost is not None and unit_cost >= 0:
        variant.cost_price = int(unit_cost)
        product = db.session.get(Product, variant.product_id)
        if product is not None and not product.base_cost:
            product.base_cost = int(unit_cost)

    movement = record_movement(
        variant,
        movement_type,
        delta,
        previous,
        new_stock,
        reason=reason,
        admin_id=admin_id,
        reference_type="manual",
        unit_cost=unit_cost or variant.cost_price or 0,
    )
    db.session.commit()
    return movement


def low_stock_variants(limit: int = 50):
    return (
        db.session.query(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(
            ProductVariant.is_active.is_(True),
            Product.deleted_at.is_(None),
            ProductVariant.stock_quantity <= ProductVariant.low_stock_threshold,
            ProductVariant.stock_quantity > 0,
        )
        .order_by(ProductVariant.stock_quantity)
        .limit(limit)
        .all()
    )


def out_of_stock_variants(limit: int = 50):
    return (
        db.session.query(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(
            ProductVariant.is_active.is_(True),
            Product.deleted_at.is_(None),
            ProductVariant.stock_quantity <= 0,
            Product.product_type == "physical",
        )
        .order_by(ProductVariant.stock_quantity)
        .limit(limit)
        .all()
    )


def inventory_value() -> dict:
    """ارزش موجودی انبار بر اساس قیمت خرید و فروش."""
    rows = (
        db.session.query(
            func.coalesce(func.sum(ProductVariant.stock_quantity * ProductVariant.cost_price), 0),
            func.coalesce(func.sum(ProductVariant.stock_quantity * ProductVariant.price), 0),
            func.coalesce(func.sum(ProductVariant.stock_quantity), 0),
        )
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(ProductVariant.is_active.is_(True), Product.deleted_at.is_(None))
        .first()
    )
    return {
        "cost_value": int(rows[0] or 0),
        "sale_value": int(rows[1] or 0),
        "units": int(rows[2] or 0),
    }
