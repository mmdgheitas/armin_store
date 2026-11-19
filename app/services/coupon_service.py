"""سرویس کدهای تخفیف با اعتبارسنجی سمت سرور و مصرف اتمیک."""
from __future__ import annotations

from sqlalchemy import text

from app.extensions import db
from app.models.marketing import Coupon, CouponRedemption
from app.utils.dates import utcnow
from app.utils.money import apply_coupon


def normalize_code(code: str | None) -> str:
    return (code or "").strip().upper()


def find(code: str | None) -> Coupon | None:
    code = normalize_code(code)
    if not code:
        return None
    return db.session.query(Coupon).filter(Coupon.code == code).first()


def eligible_subtotal(coupon: Coupon, items: list[dict]) -> int:
    """مبلغ مشمول تخفیف بر اساس محدودیت محصول/دسته."""
    if coupon.applies_to == "category" and coupon.category_id:
        return sum(i["line_total"] for i in items if i.get("category_id") == coupon.category_id)
    if coupon.applies_to == "product" and coupon.product_id:
        return sum(i["line_total"] for i in items if i.get("product_id") == coupon.product_id)
    return sum(i["line_total"] for i in items)


def usage_count(coupon: Coupon, user_id: int | None, mobile: str | None) -> int:
    query = db.session.query(CouponRedemption).filter(CouponRedemption.coupon_id == coupon.id)
    if user_id:
        return query.filter(CouponRedemption.user_id == user_id).count()
    if mobile:
        return query.filter(CouponRedemption.mobile == mobile).count()
    return 0


def validate(code: str | None, items: list[dict], user=None, mobile: str | None = None) -> dict:
    """
    اعتبارسنجی کامل کوپن.
    بازگشتی: {ok, message, coupon, discount}
    """
    code = normalize_code(code)
    if not code:
        return {"ok": False, "message": "کد تخفیف را وارد کنید.", "coupon": None, "discount": 0}

    coupon = find(code)
    if coupon is None:
        return {"ok": False, "message": "کد تخفیف یافت نشد.", "coupon": None, "discount": 0}
    if not coupon.is_active:
        return {"ok": False, "message": "این کد تخفیف غیرفعال است.", "coupon": coupon, "discount": 0}
    if not coupon.is_started:
        return {"ok": False, "message": "زمان استفاده از این کد تخفیف هنوز فرا نرسیده است.", "coupon": coupon, "discount": 0}
    if coupon.is_expired:
        return {"ok": False, "message": "اعتبار این کد تخفیف به پایان رسیده است.", "coupon": coupon, "discount": 0}
    if coupon.is_exhausted:
        return {"ok": False, "message": "ظرفیت استفاده از این کد تخفیف تکمیل شده است.", "coupon": coupon, "discount": 0}

    subtotal = eligible_subtotal(coupon, items)
    if subtotal <= 0:
        return {
            "ok": False,
            "message": "این کد تخفیف برای محصولات سبد خرید شما معتبر نیست.",
            "coupon": coupon,
            "discount": 0,
        }
    total_items = sum(i["line_total"] for i in items)
    if coupon.minimum_order and total_items < coupon.minimum_order:
        from app.utils.money import format_money

        return {
            "ok": False,
            "message": f"حداقل مبلغ سفارش برای استفاده از این کد {format_money(coupon.minimum_order)} است.",
            "coupon": coupon,
            "discount": 0,
        }

    user_id = getattr(user, "id", None)
    if coupon.per_user_limit and coupon.per_user_limit > 0:
        used = usage_count(coupon, user_id, mobile or getattr(user, "mobile", None))
        if used >= coupon.per_user_limit:
            return {
                "ok": False,
                "message": "شما پیش‌تر از این کد تخفیف استفاده کرده‌اید.",
                "coupon": coupon,
                "discount": 0,
            }

    discount = apply_coupon(coupon.discount_type, coupon.discount_value, subtotal, coupon.maximum_discount)
    if discount <= 0:
        return {"ok": False, "message": "این کد تخفیف برای سفارش شما قابل اعمال نیست.", "coupon": coupon, "discount": 0}
    return {
        "ok": True,
        "message": f"کد تخفیف اعمال شد. {coupon.description or coupon.discount_label}",
        "coupon": coupon,
        "discount": discount,
    }


def redeem(coupon: Coupon, order, user=None, mobile: str | None = None, amount: int = 0) -> bool:
    """
    مصرف اتمیک کوپن: افزایش شمارنده فقط اگر ظرفیت باقی مانده باشد.
    از مصرف هم‌زمان بیش از ظرفیت جلوگیری می‌کند.
    """
    if coupon is None:
        return False
    result = db.session.execute(
        text(
            "UPDATE coupons SET used_count = used_count + 1 "
            "WHERE id = :id AND (usage_limit = 0 OR used_count < usage_limit)"
        ),
        {"id": coupon.id},
    )
    if result.rowcount != 1:
        return False
    db.session.add(
        CouponRedemption(
            coupon_id=coupon.id,
            order_id=getattr(order, "id", None),
            user_id=getattr(user, "id", None),
            mobile=(mobile or "")[:11] or None,
            amount=int(amount),
            created_at=utcnow(),
        )
    )
    db.session.flush()
    db.session.expire(coupon, ["used_count"])
    return True


def release_usage(order) -> None:
    """آزادسازی مصرف کوپن در صورت لغو سفارش پیش از پرداخت."""
    if not order.coupon_id:
        return
    redemption = (
        db.session.query(CouponRedemption)
        .filter(CouponRedemption.order_id == order.id)
        .first()
    )
    if redemption is None:
        return
    db.session.delete(redemption)
    db.session.execute(
        text("UPDATE coupons SET used_count = MAX(0, used_count - 1) WHERE id = :id"),
        {"id": order.coupon_id},
    )
    db.session.flush()
