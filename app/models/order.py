"""سفارش‌ها، اقلام، پرداخت‌ها و تاریخچه وضعیت."""
from __future__ import annotations

from app.constants import (
    ORDER_STATUS_LABELS,
    ORDER_STATUS_TRANSITIONS,
    PAYMENT_STATUS_LABELS,
    SHIPPING_STATUS_LABELS,
)
from app.extensions import db
from app.models.base import TimestampMixin


class Order(db.Model, TimestampMixin):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    guest_name = db.Column(db.String(150), nullable=True)
    guest_mobile = db.Column(db.String(11), nullable=True, index=True)
    guest_email = db.Column(db.String(190), nullable=True)

    currency_code = db.Column(db.String(10), default="IRT", nullable=False)
    currency_label = db.Column(db.String(40), default="تومان", nullable=False)
    currency_unit = db.Column(db.String(20), default="toman", nullable=False)
    currency_ratio = db.Column(db.Integer, default=1, nullable=False)

    subtotal = db.Column(db.Integer, default=0, nullable=False)
    discount_amount = db.Column(db.Integer, default=0, nullable=False)
    shipping_cost = db.Column(db.Integer, default=0, nullable=False)
    tax_amount = db.Column(db.Integer, default=0, nullable=False)
    total_amount = db.Column(db.Integer, default=0, nullable=False)
    refund_amount = db.Column(db.Integer, default=0, nullable=False)

    status = db.Column(db.String(30), default="pending_payment", nullable=False, index=True)
    payment_status = db.Column(db.String(30), default="unpaid", nullable=False, index=True)
    shipping_status = db.Column(db.String(30), default="pending", nullable=False, index=True)

    coupon_id = db.Column(
        db.Integer, db.ForeignKey("coupons.id", ondelete="SET NULL"), nullable=True, index=True
    )
    coupon_code = db.Column(db.String(50), nullable=True)

    shipping_method_id = db.Column(
        db.Integer, db.ForeignKey("shipping_methods.id", ondelete="SET NULL"), nullable=True
    )
    shipping_method_name = db.Column(db.String(120), nullable=True)
    shipping_address_json = db.Column(db.JSON, default=dict, nullable=False)
    tracking_code = db.Column(db.String(80), nullable=True, index=True)
    has_physical_items = db.Column(db.Boolean, default=True, nullable=False)

    customer_note = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)

    paid_at = db.Column(db.DateTime, nullable=True)
    shipped_at = db.Column(db.DateTime, nullable=True)
    delivered_at = db.Column(db.DateTime, nullable=True)
    cancelled_at = db.Column(db.DateTime, nullable=True)
    refunded_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref="orders")
    coupon = db.relationship("Coupon")
    shipping_method = db.relationship("ShippingMethod")
    items = db.relationship(
        "OrderItem",
        back_populates="order",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
    )
    payments = db.relationship(
        "Payment",
        back_populates="order",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="Payment.created_at.desc()",
    )
    status_history = db.relationship(
        "OrderStatusHistory",
        back_populates="order",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="OrderStatusHistory.created_at.desc()",
    )
    reservations = db.relationship(
        "InventoryReservation", back_populates="order", lazy="dynamic", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.Index("ix_orders_status_created", "status", "created_at"),
        db.Index("ix_orders_user_created", "user_id", "created_at"),
        db.Index("ix_orders_paystatus_created", "payment_status", "created_at"),
    )

    # --- برچسب‌ها ---
    @property
    def status_label(self) -> str:
        return ORDER_STATUS_LABELS.get(self.status, self.status)

    @property
    def payment_status_label(self) -> str:
        return PAYMENT_STATUS_LABELS.get(self.payment_status, self.payment_status)

    @property
    def shipping_status_label(self) -> str:
        return SHIPPING_STATUS_LABELS.get(self.shipping_status, self.shipping_status)

    @property
    def customer_name(self) -> str:
        if self.user is not None:
            return self.user.full_name
        return self.guest_name or (self.shipping_address_json or {}).get("recipient_name", "")

    @property
    def customer_mobile(self) -> str:
        if self.user is not None:
            return self.user.mobile
        return self.guest_mobile or (self.shipping_address_json or {}).get("mobile", "")

    @property
    def is_guest(self) -> bool:
        return self.user_id is None

    @property
    def is_paid(self) -> bool:
        return self.payment_status == "paid"

    @property
    def is_terminal(self) -> bool:
        return self.status in {"cancelled", "returned", "refunded"}

    @property
    def can_be_cancelled(self) -> bool:
        return self.status in {"pending_payment", "paid", "processing"}

    @property
    def can_be_paid(self) -> bool:
        return self.status == "pending_payment" and self.payment_status in {"unpaid", "failed", "cancelled", "unknown"}

    @property
    def allowed_next_statuses(self) -> list[str]:
        return sorted(ORDER_STATUS_TRANSITIONS.get(self.status, set()))

    @property
    def items_count(self) -> int:
        return sum(item.quantity for item in self.items)

    @property
    def has_digital_items(self) -> bool:
        return any(item.delivery_type == "digital" for item in self.items)

    @property
    def shipping_address(self) -> dict:
        return self.shipping_address_json or {}

    @property
    def profit_amount(self) -> int:
        """سود ناخالص بر مبنای Snapshot قیمت خرید و فروش (فقط سفارش پرداخت‌شده)."""
        if not self.is_paid:
            return 0
        revenue = self.subtotal - self.discount_amount
        cost = sum((item.unit_cost_snapshot or 0) * item.quantity for item in self.items)
        return revenue - cost

    @property
    def successful_payment(self):
        for payment in self.payments:
            if payment.status == "successful":
                return payment
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Order {self.order_number} {self.status}>"


class OrderItem(db.Model, TimestampMixin):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant_id = db.Column(
        db.Integer, db.ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    digital_asset_id = db.Column(
        db.Integer, db.ForeignKey("digital_assets.id", ondelete="SET NULL"), nullable=True
    )
    product_name_snapshot = db.Column(db.String(220), nullable=False)
    product_slug_snapshot = db.Column(db.String(240), nullable=True)
    variant_snapshot = db.Column(db.String(255), nullable=True)
    sku_snapshot = db.Column(db.String(80), nullable=True)
    unit_price = db.Column(db.Integer, nullable=False, default=0)
    unit_cost_snapshot = db.Column(db.Integer, nullable=False, default=0)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    total_price = db.Column(db.Integer, nullable=False, default=0)
    delivery_type = db.Column(db.String(20), default="physical", nullable=False)
    delivered_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")
    variant = db.relationship("ProductVariant")
    digital_asset = db.relationship("DigitalAsset")
    download_tokens = db.relationship(
        "DownloadToken", back_populates="order_item", lazy="selectin", cascade="all, delete-orphan"
    )

    @property
    def is_digital(self) -> bool:
        return self.delivery_type == "digital"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrderItem {self.product_name_snapshot} x{self.quantity}>"


class OrderStatusHistory(db.Model):
    __tablename__ = "order_status_history"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field = db.Column(db.String(30), default="status", nullable=False)
    from_value = db.Column(db.String(40), nullable=True)
    to_value = db.Column(db.String(40), nullable=True)
    note = db.Column(db.String(400), nullable=True)
    admin_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    order = db.relationship("Order", back_populates="status_history")
    admin = db.relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<History {self.field}: {self.from_value}->{self.to_value}>"


class Payment(db.Model, TimestampMixin):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gateway = db.Column(db.String(40), default="bitpay", nullable=False, index=True)
    amount = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(20), default="IRT", nullable=False)
    authority_or_token = db.Column(db.String(255), nullable=True, index=True)
    transaction_id = db.Column(db.String(120), nullable=True, index=True)
    reference_code = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(30), default="initiated", nullable=False, index=True)
    request_data = db.Column(db.Text, nullable=True)
    response_data = db.Column(db.Text, nullable=True)
    callback_data = db.Column(db.Text, nullable=True)
    is_test = db.Column(db.Boolean, default=False, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)

    order = db.relationship("Order", back_populates="payments")

    __table_args__ = (db.Index("ix_payments_status_created", "status", "created_at"),)

    @property
    def is_successful(self) -> bool:
        return self.status == "successful"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Payment {self.id} {self.status} {self.amount}>"
