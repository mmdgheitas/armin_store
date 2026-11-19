"""کلاس‌های پایه مدل‌ها."""
from __future__ import annotations

from app.extensions import db
from app.utils.dates import utcnow


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class SoftDeleteMixin:
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = utcnow()
