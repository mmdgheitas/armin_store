"""تحویل محصولات دیجیتال: توکن‌های دانلود."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class DownloadToken(db.Model, TimestampMixin):
    __tablename__ = "download_tokens"

    id = db.Column(db.Integer, primary_key=True)
    order_item_id = db.Column(
        db.Integer, db.ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    digital_asset_id = db.Column(
        db.Integer, db.ForeignKey("digital_assets.id", ondelete="SET NULL"), nullable=True
    )
    token_hash = db.Column(db.String(128), unique=True, nullable=False, index=True)
    download_count = db.Column(db.Integer, default=0, nullable=False)
    max_downloads = db.Column(db.Integer, default=5, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    last_download_at = db.Column(db.DateTime, nullable=True)
    issued_license = db.Column(db.String(255), nullable=True)

    order_item = db.relationship("OrderItem", back_populates="download_tokens")
    digital_asset = db.relationship("DigitalAsset")

    @property
    def is_expired(self) -> bool:
        from app.utils.dates import utcnow

        return bool(self.expires_at and self.expires_at < utcnow())

    @property
    def is_exhausted(self) -> bool:
        return bool(self.max_downloads and self.download_count >= self.max_downloads)

    @property
    def is_usable(self) -> bool:
        return not self.is_expired and not self.is_exhausted

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DownloadToken item={self.order_item_id} {self.download_count}/{self.max_downloads}>"
