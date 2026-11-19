"""محتوا، تنظیمات، اعلان‌ها، پیامک، OTP، هزینه‌ها، لاگ‌ها و بکاپ."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class BlogCategory(db.Model, TimestampMixin):
    __tablename__ = "blog_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(140), unique=True, nullable=False, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlogCategory {self.slug}>"


class BlogPost(db.Model, TimestampMixin):
    __tablename__ = "blog_posts"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("blog_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title = db.Column(db.String(220), nullable=False, index=True)
    slug = db.Column(db.String(240), unique=True, nullable=False, index=True)
    excerpt = db.Column(db.String(400), nullable=True)
    content = db.Column(db.Text, nullable=False)
    cover_image = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default="draft", nullable=False, index=True)  # draft|published
    author_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)
    views = db.Column(db.Integer, default=0, nullable=False)
    published_at = db.Column(db.DateTime, nullable=True, index=True)

    category = db.relationship("BlogCategory", backref="posts")
    author = db.relationship("User")

    __table_args__ = (db.Index("ix_blog_status_published", "status", "published_at"),)

    @property
    def is_published(self) -> bool:
        return self.status == "published"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlogPost {self.slug}>"


class Page(db.Model, TimestampMixin):
    __tablename__ = "pages"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)
    is_published = db.Column(db.Boolean, default=True, nullable=False)
    show_in_footer = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Page {self.slug}>"


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type = db.Column(db.String(40), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(300), nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    user = db.relationship("User", backref=db.backref("notifications", lazy="dynamic"))

    __table_args__ = (db.Index("ix_notifications_user_read", "user_id", "is_read", "created_at"),)


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    is_sensitive = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Setting {self.key}>"


class SmsLog(db.Model):
    __tablename__ = "sms_logs"

    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(11), nullable=False, index=True)
    pattern_code = db.Column(db.String(80), nullable=True)
    event = db.Column(db.String(40), nullable=True, index=True)
    parameters_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, index=True)  # sent|failed|skipped
    error_message = db.Column(db.String(400), nullable=True)
    provider_response = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    __table_args__ = (db.Index("ix_sms_logs_status_created", "status", "created_at"),)


class OtpCode(db.Model):
    __tablename__ = "otp_codes"

    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(11), nullable=False, index=True)
    code_hash = db.Column(db.String(128), nullable=False)
    purpose = db.Column(db.String(30), default="login", nullable=False, index=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    ip_address = db.Column(db.String(64), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    consumed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True)


class RateLimitEntry(db.Model):
    __tablename__ = "rate_limit_entries"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(160), nullable=False, index=True)
    window_start = db.Column(db.DateTime, nullable=False)
    count = db.Column(db.Integer, default=0, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (db.UniqueConstraint("key", "window_start", name="uq_rate_limit_window"),)


class Expense(db.Model, TimestampMixin):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(80), nullable=True, index=True)
    amount = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, nullable=True)
    expense_date = db.Column(db.Date, nullable=False, index=True)
    created_by = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    creator = db.relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Expense {self.title} {self.amount}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action = db.Column(db.String(80), nullable=False, index=True)
    entity_type = db.Column(db.String(60), nullable=True, index=True)
    entity_id = db.Column(db.Integer, nullable=True, index=True)
    old_data = db.Column(db.Text, nullable=True)
    new_data = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    admin = db.relationship("User")

    __table_args__ = (db.Index("ix_audit_entity", "entity_type", "entity_id"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.action}>"


class BackupLog(db.Model):
    __tablename__ = "backup_logs"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    size = db.Column(db.Integer, default=0, nullable=False)
    status = db.Column(db.String(20), default="success", nullable=False)
    backup_type = db.Column(db.String(20), default="manual", nullable=False)
    note = db.Column(db.String(300), nullable=True)
    admin_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    admin = db.relationship("User")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BackupLog {self.filename}>"
