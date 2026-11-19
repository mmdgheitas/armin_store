"""انبار: حرکات موجودی و رزروها."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class InventoryMovement(db.Model, TimestampMixin):
    __tablename__ = "inventory_movements"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant_id = db.Column(
        db.Integer, db.ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    movement_type = db.Column(db.String(20), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    previous_stock = db.Column(db.Integer, nullable=False, default=0)
    new_stock = db.Column(db.Integer, nullable=False, default=0)
    unit_cost = db.Column(db.Integer, default=0, nullable=False)
    reason = db.Column(db.String(300), nullable=True)
    reference_type = db.Column(db.String(40), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    admin_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    product = db.relationship("Product")
    variant = db.relationship("ProductVariant")
    admin = db.relationship("User")

    __table_args__ = (
        db.Index("ix_movements_variant_created", "variant_id", "created_at"),
        db.Index("ix_movements_type_created", "movement_type", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Movement {self.movement_type} {self.quantity}>"


class InventoryReservation(db.Model, TimestampMixin):
    __tablename__ = "inventory_reservations"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_id = db.Column(
        db.Integer, db.ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default="active", nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)

    order = db.relationship("Order", back_populates="reservations")
    variant = db.relationship("ProductVariant", back_populates="reservations")

    __table_args__ = (db.Index("ix_reservations_status_expiry", "status", "expires_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Reservation order={self.order_id} variant={self.variant_id} status={self.status}>"
