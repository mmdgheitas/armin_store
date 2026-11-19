"""بازاریابی: کوپن، تبلیغات، علاقه‌مندی‌ها، مقایسه."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin
from app.utils.money import apply_coupon


class Coupon(db.Model, TimestampMixin):
    __tablename__ = "coupons"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    discount_type = db.Column(db.String(20), default="percent", nullable=False)
    discount_value = db.Column(db.Integer, default=0, nullable=False)
    minimum_order = db.Column(db.Integer, default=0, nullable=False)
    maximum_discount = db.Column(db.Integer, default=0, nullable=False)
    usage_limit = db.Column(db.Integer, default=0, nullable=False)  # 0 = نامحدود
    per_user_limit = db.Column(db.Integer, default=1, nullable=False)
    used_count = db.Column(db.Integer, default=0, nullable=False)
    starts_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    applies_to = db.Column(db.String(20), default="all", nullable=False)  # all | category | product
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True
    )
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="SET NULL"), nullable=True)

    category = db.relationship("Category")
    product = db.relationship("Product")
    redemptions = db.relationship(
        "CouponRedemption", back_populates="coupon", lazy="dynamic", cascade="all, delete-orphan"
    )

    @property
    def is_expired(self) -> bool:
        from app.utils.dates import utcnow

        return bool(self.expires_at and self.expires_at < utcnow())

    @property
    def is_started(self) -> bool:
        from app.utils.dates import utcnow

        return not self.starts_at or self.starts_at <= utcnow()

    @property
    def is_exhausted(self) -> bool:
        return bool(self.usage_limit and self.used_count >= self.usage_limit)

    @property
    def discount_label(self) -> str:
        from app.utils.money import format_number

        if self.discount_type == "percent":
            return f"{format_number(self.discount_value)}٪ تخفیف"
        return f"{format_number(self.discount_value)} تومان تخفیف"

    def calculate(self, subtotal: int) -> int:
        return apply_coupon(self.discount_type, self.discount_value, subtotal, self.maximum_discount)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Coupon {self.code}>"


class CouponRedemption(db.Model):
    __tablename__ = "coupon_redemptions"

    id = db.Column(db.Integer, primary_key=True)
    coupon_id = db.Column(
        db.Integer, db.ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mobile = db.Column(db.String(11), nullable=True, index=True)
    amount = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    coupon = db.relationship("Coupon", back_populates="redemptions")
    order = db.relationship("Order")
    user = db.relationship("User")

    __table_args__ = (db.Index("ix_redemptions_coupon_user", "coupon_id", "user_id"),)


class Advertisement(db.Model, TimestampMixin):
    __tablename__ = "advertisements"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(500), nullable=True)
    background_color = db.Column(db.String(20), default="#111827", nullable=False)
    text_color = db.Column(db.String(20), default="#ffffff", nullable=False)
    starts_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (db.Index("ix_ads_active_sort", "is_active", "sort_order"),)

    def is_visible_now(self) -> bool:
        from app.utils.dates import utcnow

        now = utcnow()
        if not self.is_active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.expires_at and self.expires_at < now:
            return False
        return True

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Ad {self.text[:20]}>"


class Wishlist(db.Model):
    __tablename__ = "wishlists"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    user = db.relationship("User", backref=db.backref("wishlist_items", lazy="dynamic", cascade="all"))
    product = db.relationship("Product")

    __table_args__ = (db.UniqueConstraint("user_id", "product_id", name="uq_wishlist_user_product"),)
