"""
سرویس سفارش: ساخت، تغییر وضعیت، لغو، مرجوعی و بازپرداخت.

قواعد کلیدی:
  * تمام مبالغ Snapshot می‌شوند (قیمت، هزینه، واحد پول) تا تغییرات آینده روی
    سفارش‌های قدیمی اثر نگذارد.
  * عملیات موجودی و ساخت سفارش در یک تراکنش با BEGIN IMMEDIATE اجرا می‌شود.
  * تغییر وضعیت فقط از مسیرهای مجاز و با ثبت تاریخچه انجام می‌شود.
  * وضعیت پرداخت هرگز به‌صورت دستی «پرداخت‌شده» نمی‌شود؛ فقط تأیید درگاه.
"""
from __future__ import annotations

import time
from datetime import timedelta

from flask import current_app, session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.constants import ORDER_STATUS_TRANSITIONS
from app.extensions import db
from app.models import (
    Address,
    InventoryReservation,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    User,
)
from app.services import (
    cart_service,
    coupon_service,
    inventory_service,
    notification_service,
    settings_service,
)
from app.utils.dates import utcnow
from app.utils.money import percent_of
from app.utils.security import generate_order_number, is_valid_mobile, normalize_mobile

VERIFIED_ORDERS_SESSION_KEY = "verified_orders"
_last_sweep = 0.0


class OrderError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _begin_immediate() -> None:
    """شروع تراکنش نوشتن با گرفتن قفل از همان ابتدا (سازگار با SQLite)."""
    db.session.commit()
    db.session.execute(text("BEGIN IMMEDIATE"))


def _is_locked_error(exc: Exception) -> bool:
    return "locked" in str(exc).lower() or "busy" in str(exc).lower()


def _unique_order_number() -> str:
    for _ in range(8):
        number = generate_order_number()
        if db.session.query(Order.id).filter(Order.order_number == number).first() is None:
            return number
    return f"AR-{int(time.time())}-{generate_order_number()[-4:]}"


def _history(order: Order, field: str, from_value: str | None, to_value: str | None, admin_id=None, note=None):
    db.session.add(
        OrderStatusHistory(
            order_id=order.id,
            field=field,
            from_value=from_value,
            to_value=to_value,
            note=(note or "")[:400] or None,
            admin_id=admin_id,
            created_at=utcnow(),
        )
    )


def _currency_snapshot() -> dict:
    return {
        "currency_code": settings_service.get_str("currency_code", "IRT")[:10],
        "currency_label": settings_service.get_str("currency_label", "تومان")[:40],
        "currency_unit": settings_service.get_str("currency_unit", "toman")[:20],
        "currency_ratio": settings_service.get_int("currency_ratio", 1),
    }


def place_order(
    user: User | None,
    address: dict,
    customer_note: str = "",
    ip: str | None = None,
    user_agent: str | None = None,
    guest_name: str = "",
    guest_mobile: str = "",
    guest_email: str = "",
) -> tuple[Order | None, str | None]:
    """
    ثبت سفارش از سبد خرید جاری.
    بازگشتی: (order, error_message)
    """
    summary = cart_service.summary(user=user, address=address)
    if summary["is_empty"]:
        return None, "سبد خرید شما خالی است."

    if not summary["stock_ok"]:
        return None, " ".join(summary["issues"][:3])

    min_order = settings_service.get_int("min_order_amount", 0)
    if min_order and summary["subtotal"] < min_order:
        from app.utils.money import format_money

        return None, f"حداقل مبلغ سفارش {format_money(min_order)} است."

    if summary["coupon_code"] and summary["coupon"] is None:
        return None, summary["coupon_message"] or "کد تخفیف معتبر نیست."

    has_physical = summary["has_physical"]
    shipping_method = summary["shipping_method"]
    if has_physical:
        if shipping_method is None:
            return None, "لطفاً روش ارسال را انتخاب کنید."
        if not address.get("province") or not address.get("city"):
            return None, "استان و شهر گیرنده را وارد کنید."
    shipping_cost = summary["shipping_cost"] if has_physical else 0

    if user is None:
        guest_mobile = normalize_mobile(guest_mobile or address.get("mobile", ""))
        if not is_valid_mobile(guest_mobile):
            return None, "شماره موبایل معتبر برای پیگیری سفارش الزامی است."
        if not settings_service.get_bool("guest_checkout_enabled", True):
            return None, "خرید مهمان غیرفعال است. لطفاً وارد حساب کاربری شوید."

    subtotal = summary["subtotal"]
    discount = summary["discount"] if summary["coupon"] else 0
    tax_amount = summary["tax_amount"]
    total = max(0, subtotal - discount) + shipping_cost + tax_amount

    items_data = [
        {
            "variant_id": item["variant_id"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
            "unit_cost": int(item["variant"].cost_price or 0),
            "product_id": item["product_id"],
            "product_name": item["product_name"],
            "product_slug": item["product_slug"],
            "variant_label": item["variant_label"],
            "sku": item["sku"],
            "delivery_type": item["delivery_type"],
        }
        for item in summary["items"]
        if item["is_active"] and item["stock_ok"]
    ]
    if not items_data:
        return None, "هیچ کالای قابل سفارشی در سبد خرید وجود ندارد."

    coupon = summary["coupon"]
    minutes = max(1, settings_service.get_int("reservation_minutes", 20))

    for attempt in range(3):
        order = None
        try:
            _begin_immediate()

            order = Order(
                order_number=_unique_order_number(),
                user_id=getattr(user, "id", None),
                guest_name=(guest_name or address.get("recipient_name") or "").strip()[:150] or None,
                guest_mobile=(normalize_mobile(guest_mobile) or None) if user is None else None,
                guest_email=(guest_email or "").strip()[:190] or None,
                subtotal=subtotal,
                discount_amount=discount,
                shipping_cost=shipping_cost,
                tax_amount=tax_amount,
                total_amount=total,
                status="pending_payment",
                payment_status="unpaid",
                shipping_status="pending",
                coupon_id=coupon.id if coupon else None,
                coupon_code=coupon.code if coupon else None,
                shipping_method_id=shipping_method.id if shipping_method and has_physical else None,
                shipping_method_name=shipping_method.name if shipping_method and has_physical else None,
                shipping_address_json=address or {},
                customer_note=(customer_note or "").strip()[:2000] or None,
                has_physical_items=has_physical,
                ip_address=(ip or "")[:64] or None,
                user_agent=(user_agent or "")[:255] or None,
                created_at=utcnow(),
                updated_at=utcnow(),
                **_currency_snapshot(),
            )
            db.session.add(order)
            db.session.flush()

            for item in items_data:
                db.session.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=item["product_id"],
                        variant_id=item["variant_id"],
                        product_name_snapshot=item["product_name"][:220],
                        product_slug_snapshot=(item["product_slug"] or "")[:240] or None,
                        variant_snapshot=(item["variant_label"] or "")[:255] or None,
                        sku_snapshot=(item["sku"] or "")[:80] or None,
                        unit_price=item["unit_price"],
                        unit_cost_snapshot=item["unit_cost"],
                        quantity=item["quantity"],
                        total_price=item["unit_price"] * item["quantity"],
                        delivery_type=item["delivery_type"],
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            db.session.flush()

            physical_reservations = [
                item for item in items_data if item["delivery_type"] == "physical"
            ]
            inventory_service.reserve_stock(order, physical_reservations, minutes=minutes)

            if coupon is not None:
                if not coupon_service.redeem(
                    coupon,
                    order,
                    user=user,
                    mobile=(user.mobile if user else normalize_mobile(guest_mobile)),
                    amount=discount,
                ):
                    raise OrderError("ظرفیت استفاده از این کد تخفیف هم‌زمان تکمیل شد. لطفاً بدون کد تخفیف دوباره تلاش کنید.")

            _history(order, "status", None, "pending_payment", note="ثبت سفارش")
            _history(order, "payment_status", None, "unpaid", note="در انتظار پرداخت")
            db.session.commit()
            break
        except OrderError as exc:
            db.session.rollback()
            return None, exc.message
        except OperationalError as exc:
            db.session.rollback()
            if _is_locked_error(exc) and attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
            current_app.logger.exception("order creation failed (db)")
            return None, "خطا در ثبت سفارش. لطفاً دوباره تلاش کنید."
        except inventory_service.StockError as exc:
            db.session.rollback()
            return None, exc.message
        except Exception:
            db.session.rollback()
            current_app.logger.exception("order creation failed")
            return None, "خطا در ثبت سفارش. لطفاً بعداً تلاش کنید."

    if order is None:
        return None, "ثبت سفارش انجام نشد."

    cart_service.clear()
    try:
        notification_service.on_order_created(order)
    except Exception:  # pragma: no cover
        current_app.logger.exception("order notification failed")
    return order, None


def find_by_number(order_number: str) -> Order | None:
    if not order_number:
        return None
    return db.session.query(Order).filter(Order.order_number == (order_number or "").strip()).first()


def can_access(order: Order, user: User | None) -> bool:
    """دسترسی به سفارش فقط برای مالک یا سفارش تأییدشده در نشست."""
    if order is None:
        return False
    if user is not None and order.user_id == user.id:
        return True
    verified = session.get(VERIFIED_ORDERS_SESSION_KEY) or []
    return order.order_number in verified


def grant_guest_access(order: Order) -> None:
    verified = session.get(VERIFIED_ORDERS_SESSION_KEY) or []
    if order.order_number not in verified:
        verified.append(order.order_number)
        session[VERIFIED_ORDERS_SESSION_KEY] = verified[-20:]
        session.modified = True


def mark_paid(order: Order, payment: Payment) -> bool:
    """
    تأیید پرداخت و تکمیل سفارش.
    Idempotent است: اجرای دوباره موجب کسر مجدد موجودی یا تحویل دوباره نمی‌شود.
    """
    from app.services import digital_delivery_service

    if order is None or payment is None:
        return False
    if order.payment_status == "paid":
        return True

    for attempt in range(3):
        try:
            _begin_immediate()
            db.session.refresh(order)
            if order.payment_status == "paid":
                db.session.commit()
                return True

            # اطمینان از وجود رزرو برای اقلام فیزیکی
            if not inventory_service.has_active_reservation(order):
                physical_items = [
                    {"variant_id": item.variant_id, "quantity": item.quantity, "product_id": item.product_id}
                    for item in order.items
                    if item.delivery_type == "physical" and item.variant_id
                ]
                if physical_items:
                    try:
                        inventory_service.reserve_stock(order, physical_items)
                    except inventory_service.StockError:
                        current_app.logger.error(
                            "paid order without stock order=%s", order.order_number
                        )
                        order.admin_note = (
                            (order.admin_note or "")
                            + "\n[سیستم] پرداخت تأیید شد اما موجودی برخی اقلام کافی نبود. بررسی دستی لازم است."
                        ).strip()

            inventory_service.consume_reservations(order)

            previous_status = order.status
            previous_payment = order.payment_status
            order.payment_status = "paid"
            order.paid_at = utcnow()
            if order.status == "pending_payment":
                order.status = "paid"
            order.updated_at = utcnow()
            _history(order, "payment_status", previous_payment, "paid", note=f"تأیید پرداخت {payment.gateway}")
            if previous_status != order.status:
                _history(order, "status", previous_status, order.status, note="پرداخت موفق")

            db.session.commit()
            break
        except OperationalError as exc:
            db.session.rollback()
            if _is_locked_error(exc) and attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
            current_app.logger.exception("mark_paid failed")
            return False
        except Exception:
            db.session.rollback()
            current_app.logger.exception("mark_paid failed")
            return False

    try:
        digital_delivery_service.fulfill_order(order)
    except Exception:  # pragma: no cover
        current_app.logger.exception("digital fulfillment failed")
    try:
        notification_service.on_order_status_change(order, "pending_payment", "paid")
    except Exception:  # pragma: no cover
        current_app.logger.exception("payment notification failed")
    return True


def cancel_order(order: Order, admin=None, reason: str = "", restock: bool = True) -> tuple[bool, str]:
    """لغو سفارش با هماهنگی موجودی و کوپن."""
    if order is None:
        return False, "سفارش یافت نشد."
    if order.status in {"cancelled", "returned", "refunded"}:
        return False, "این سفارش قبلاً بسته شده است."
    if order.status == "delivered":
        return False, "سفارش تحویل‌شده را باید از مسیر مرجوعی پیگیری کنید."

    previous = order.status
    try:
        _begin_immediate()
        db.session.refresh(order)
        inventory_service.release_reservations(order, status="released", reason="لغو سفارش")
        if restock and order.is_paid:
            inventory_service.restock_for_return(order, admin_id=getattr(admin, "id", None), reason="لغو سفارش پرداخت‌شده")
        if not order.is_paid:
            coupon_service.release_usage(order)
        order.status = "cancelled"
        order.cancelled_at = utcnow()
        order.updated_at = utcnow()
        _history(
            order,
            "status",
            previous,
            "cancelled",
            admin_id=getattr(admin, "id", None),
            note=reason or "لغو سفارش",
        )
        if order.is_paid:
            order.payment_status = "refunded" if order.refund_amount >= order.total_amount else order.payment_status
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("cancel order failed")
        return False, "لغو سفارش انجام نشد."

    try:
        notification_service.on_order_status_change(order, previous, "cancelled")
    except Exception:  # pragma: no cover
        pass
    return True, "سفارش لغو شد."


def change_status(
    order: Order,
    new_status: str,
    admin=None,
    note: str = "",
    tracking_code: str | None = None,
    restock: bool = True,
) -> tuple[bool, str]:
    """تغییر وضعیت با اعتبارسنجی مسیر مجاز و ثبت تاریخچه."""
    if order is None:
        return False, "سفارش یافت نشد."
    if new_status == order.status:
        return False, "وضعیت سفارش تغییری نکرده است."
    if new_status in {"pending_payment", "paid"}:
        return False, "وضعیت پرداخت فقط از طریق تأیید درگاه قابل تغییر است."
    allowed = ORDER_STATUS_TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        return False, f"تغییر وضعیت از «{order.status_label}» به این وضعیت مجاز نیست."

    previous = order.status
    try:
        _begin_immediate()
        db.session.refresh(order)
        order.status = new_status
        order.updated_at = utcnow()

        if new_status == "processing":
            order.shipping_status = "preparing"
        elif new_status == "ready_to_ship":
            order.shipping_status = "ready"
        elif new_status == "shipped":
            order.shipped_at = utcnow()
            order.shipping_status = "shipped"
            if tracking_code:
                order.tracking_code = tracking_code.strip()[:80]
        elif new_status == "delivered":
            order.delivered_at = utcnow()
            order.shipping_status = "delivered"
            if tracking_code:
                order.tracking_code = tracking_code.strip()[:80]
        elif new_status == "cancelled":
            order.cancelled_at = utcnow()
            inventory_service.release_reservations(order, status="released", reason="لغو سفارش")
            if restock:
                inventory_service.restock_for_return(order, admin_id=getattr(admin, "id", None))
            coupon_service.release_usage(order)
        elif new_status == "returned":
            order.shipping_status = "returned"
            if restock:
                inventory_service.restock_for_return(order, admin_id=getattr(admin, "id", None))
        elif new_status == "refunded":
            order.refunded_at = utcnow()
            order.payment_status = "refunded"
            db.session.add(
                OrderStatusHistory(
                    order_id=order.id,
                    field="payment_status",
                    from_value="paid",
                    to_value="refunded",
                    note=note or "بازپرداخت ثبت شد",
                    admin_id=getattr(admin, "id", None),
                    created_at=utcnow(),
                )
            )

        _history(
            order,
            "status",
            previous,
            new_status,
            admin_id=getattr(admin, "id", None),
            note=note or None,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("change status failed")
        return False, "تغییر وضعیت انجام نشد."

    try:
        notification_service.on_order_status_change(order, previous, new_status, order.tracking_code)
    except Exception:  # pragma: no cover
        pass
    return True, "وضعیت سفارش به‌روزرسانی شد."


def register_return(order: Order, admin=None, reason: str = "", restock: bool = True) -> tuple[bool, str]:
    """ثبت مرجوعی برای سفارش ارسال/تحویل‌شده."""
    if order is None:
        return False, "سفارش یافت نشد."
    if order.status not in {"shipped", "delivered"}:
        return False, "فقط سفارش ارسال‌شده یا تحویل‌شده قابل مرجوعی است."
    return change_status(order, "returned", admin=admin, note=reason or "درخواست مرجوعی", restock=restock)


def register_refund(order: Order, amount: int, admin=None, note: str = "") -> tuple[bool, str]:
    """ثبت بازپرداخت واقعی (فقط پس از انجام عملیات مالی)."""
    if order is None:
        return False, "سفارش یافت نشد."
    if not order.is_paid:
        return False, "سفارش پرداخت‌شده نیست."
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return False, "مبلغ بازپرداخت نامعتبر است."
    if amount <= 0:
        return False, "مبلغ بازپرداخت باید بزرگ‌تر از صفر باشد."
    if amount + (order.refund_amount or 0) > order.total_amount:
        return False, "مبلغ بازپرداخت از مبلغ سفارش بیشتر است."

    previous_status = order.status
    try:
        order.refund_amount = (order.refund_amount or 0) + amount
        order.refunded_at = utcnow()
        order.updated_at = utcnow()
        if order.refund_amount >= order.total_amount:
            order.payment_status = "refunded"
            order.status = "refunded"
        db.session.add(
            OrderStatusHistory(
                order_id=order.id,
                field="refund",
                from_value=str(order.refund_amount - amount),
                to_value=str(order.refund_amount),
                note=(note or "ثبت بازپرداخت")[:400],
                admin_id=getattr(admin, "id", None),
                created_at=utcnow(),
            )
        )
        if previous_status != order.status:
            _history(order, "status", previous_status, order.status, admin_id=getattr(admin, "id", None), note="بازپرداخت کامل")
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("refund failed")
        return False, "ثبت بازپرداخت انجام نشد."
    return True, "بازپرداخت ثبت شد."


def set_shipping_status(order: Order, status: str, admin=None, tracking_code: str | None = None) -> tuple[bool, str]:
    if status not in {"pending", "preparing", "ready", "shipped", "delivered", "returned"}:
        return False, "وضعیت ارسال نامعتبر است."
    order.shipping_status = status
    if tracking_code:
        order.tracking_code = tracking_code.strip()[:80]
    if status == "shipped" and not order.shipped_at:
        order.shipped_at = utcnow()
    if status == "delivered":
        order.delivered_at = utcnow()
    order.updated_at = utcnow()
    db.session.add(
        OrderStatusHistory(
            order_id=order.id,
            field="shipping_status",
            from_value=None,
            to_value=status,
            note=(tracking_code or "")[:400] or None,
            admin_id=getattr(admin, "id", None),
            created_at=utcnow(),
        )
    )
    db.session.commit()
    return True, "وضعیت ارسال به‌روزرسانی شد."


def sweep_expired_reservations(min_interval: int = 60) -> int:
    """
    آزادسازی رزروهای منقضی و لغو سفارش‌های پرداخت‌نشده.
    بدون نیاز به Cron؛ در هر درخواست با فاصله زمانی اجرا می‌شود.
    """
    global _last_sweep
    now_ts = time.time()
    if now_ts - _last_sweep < min_interval:
        return 0

    try:
        stored = settings_service.get_str("last_expiry_sweep")
        if stored:
            try:
                if now_ts - float(stored) < min_interval:
                    return 0
            except ValueError:
                pass
        _last_sweep = now_ts
        settings_service.set_value("last_expiry_sweep", f"{now_ts:.0f}")
        db.session.commit()
    except Exception:
        db.session.rollback()
        return 0

    now = utcnow()
    try:
        expired = (
            db.session.query(InventoryReservation)
            .filter(InventoryReservation.status == "active", InventoryReservation.expires_at <= now)
            .all()
        )
        if not expired:
            return 0
        order_ids = {r.order_id for r in expired}
        count = 0
        for order_id in order_ids:
            order = db.session.get(Order, order_id)
            if order is None:
                continue
            inventory_service.release_reservations(order, status="expired", reason="انقضای مهلت پرداخت")
            if order.status == "pending_payment" and order.payment_status != "paid":
                coupon_service.release_usage(order)
                order.status = "cancelled"
                order.cancelled_at = now
                order.updated_at = now
                order.admin_note = ((order.admin_note or "") + "\n[سیستم] لغو خودکار به دلیل انقضای مهلت پرداخت.").strip()
                db.session.add(
                    OrderStatusHistory(
                        order_id=order.id,
                        field="status",
                        from_value="pending_payment",
                        to_value="cancelled",
                        note="لغو خودکار: انقضای مهلت پرداخت",
                        created_at=now,
                    )
                )
                count += 1
        db.session.commit()
        # در صورت شکست پرداخت، آخرین وضعیت درگاه به‌روزرسانی می‌شود
        for order in db.session.query(Order).filter(Order.id.in_(order_ids)).all():
            for payment in order.payments:
                if payment.status in {"initiated", "pending"}:
                    payment.status = "failed"
            db.session.commit()
        return count
    except Exception:
        db.session.rollback()
        current_app.logger.exception("sweep expired reservations failed")
        return 0


def verify_guest_order(order_number: str, mobile: str) -> Order | None:
    """بررسی مالکیت سفارش مهمان با شماره موبایل (پس از تأیید OTP)."""
    order = find_by_number(order_number)
    if order is None:
        return None
    mobile = normalize_mobile(mobile)
    if not mobile:
        return None
    if order.customer_mobile and normalize_mobile(order.customer_mobile) == mobile:
        return order
    return None


def order_query_for_user(user: User):
    return db.session.query(Order).filter(Order.user_id == user.id)


def build_address_dict(data: dict) -> dict:
    return {
        "recipient_name": (data.get("recipient_name") or "").strip()[:150],
        "mobile": normalize_mobile(data.get("mobile", "")),
        "province": (data.get("province") or "").strip()[:80],
        "city": (data.get("city") or "").strip()[:80],
        "full_address": (data.get("full_address") or "").strip()[:1000],
        "postal_code": (data.get("postal_code") or "").strip()[:10],
        "national_code": (data.get("national_code") or "").strip()[:10],
    }


def save_address(user: User, data: dict, make_default: bool = False) -> tuple[Address | None, list[str]]:
    from app.utils.security import is_valid_mobile as _valid

    errors: list[str] = []
    address = build_address_dict(data)
    if len(address["recipient_name"]) < 3:
        errors.append("نام گیرنده را کامل وارد کنید.")
    if not _valid(address["mobile"]):
        errors.append("شماره موبایل گیرنده معتبر نیست.")
    if len(address["province"]) < 2:
        errors.append("استان را انتخاب کنید.")
    if len(address["city"]) < 2:
        errors.append("شهر را وارد کنید.")
    if len(address["full_address"]) < 10:
        errors.append("آدرس کامل را وارد کنید.")
    if address["postal_code"] and not address["postal_code"].isdigit():
        errors.append("کد پستی باید عددی باشد.")
    if address["national_code"] and not address["national_code"].isdigit():
        errors.append("کد ملی باید عددی باشد.")
    if errors:
        return None, errors

    if make_default:
        for existing in user.addresses:
            existing.is_default = False

    new_address = Address(
        user_id=user.id,
        recipient_name=address["recipient_name"],
        mobile=address["mobile"],
        province=address["province"],
        city=address["city"],
        full_address=address["full_address"],
        postal_code=address["postal_code"] or None,
        national_code=address["national_code"] or None,
        is_default=bool(make_default) or not user.addresses,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(new_address)
    db.session.commit()
    return new_address, []


def tax_for(subtotal_after_discount: int) -> int:
    if not settings_service.get_bool("tax_enabled", False):
        return 0
    return percent_of(subtotal_after_discount, settings_service.get_int("tax_percent", 0))
