"""نظرات و ویدئوهای مشتریان."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Review(db.Model, TimestampMixin):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    order_item_id = db.Column(
        db.Integer, db.ForeignKey("order_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rating = db.Column(db.Integer, nullable=False, default=5)
    title = db.Column(db.String(180), nullable=True)
    body = db.Column(db.Text, nullable=False)
    is_verified_purchase = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    admin_note = db.Column(db.String(300), nullable=True)

    product = db.relationship("Product", backref=db.backref("reviews_rel", lazy="dynamic"))
    user = db.relationship("User")
    order_item = db.relationship("OrderItem")

    __table_args__ = (
        db.Index("ix_reviews_product_status", "product_id", "status", "created_at"),
        db.Index("ix_reviews_user_product", "user_id", "product_id"),
    )

    @property
    def status_label(self) -> str:
        return {"pending": "در انتظار تأیید", "approved": "تأییدشده", "rejected": "ردشده"}.get(
            self.status, self.status
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Review {self.id} rating={self.rating}>"


class CustomerVideo(db.Model, TimestampMixin):
    __tablename__ = "customer_videos"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    order_item_id = db.Column(
        db.Integer, db.ForeignKey("order_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    file_path = db.Column(db.String(255), nullable=False)
    thumbnail_path = db.Column(db.String(255), nullable=True)
    original_name = db.Column(db.String(200), nullable=True)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default="pending", nullable=False, index=True)
    admin_note = db.Column(db.String(300), nullable=True)

    product = db.relationship("Product", backref=db.backref("customer_videos_rel", lazy="dynamic"))
    user = db.relationship("User")
    order_item = db.relationship("OrderItem")

    __table_args__ = (
        db.Index("ix_customer_videos_product_status", "product_id", "status", "created_at"),
    )

    @property
    def status_label(self) -> str:
        return {"pending": "در انتظار تأیید", "approved": "تأییدشده", "rejected": "ردشده"}.get(
            self.status, self.status
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CustomerVideo {self.id} status={self.status}>"
