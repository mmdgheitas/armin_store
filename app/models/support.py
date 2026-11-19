"""سیستم پشتیبانی (تیکت)."""
from __future__ import annotations

from app.extensions import db
from app.models.base import TimestampMixin


class Ticket(db.Model, TimestampMixin):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    ticket_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    guest_name = db.Column(db.String(150), nullable=True)
    guest_mobile = db.Column(db.String(11), nullable=True, index=True)
    guest_email = db.Column(db.String(190), nullable=True)
    subject = db.Column(db.String(200), nullable=False)
    department = db.Column(db.String(30), default="support", nullable=False, index=True)
    priority = db.Column(db.String(20), default="normal", nullable=False, index=True)
    status = db.Column(db.String(20), default="open", nullable=False, index=True)
    last_reply_at = db.Column(db.DateTime, nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")
    messages = db.relationship(
        "TicketMessage",
        back_populates="ticket",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )

    __table_args__ = (db.Index("ix_tickets_status_updated", "status", "updated_at"),)

    @property
    def status_label(self) -> str:
        return {
            "open": "باز",
            "answered": "پاسخ داده‌شده",
            "pending": "در انتظار مشتری",
            "closed": "بسته",
        }.get(self.status, self.status)

    @property
    def priority_label(self) -> str:
        return {"low": "کم", "normal": "عادی", "high": "زیاد", "urgent": "فوری"}.get(
            self.priority, self.priority
        )

    @property
    def department_label(self) -> str:
        return {
            "sales": "فروش",
            "support": "پشتیبانی فنی",
            "order": "پیگیری سفارش",
            "other": "سایر",
        }.get(self.department, self.department)

    @property
    def owner_name(self) -> str:
        return self.user.full_name if self.user else (self.guest_name or self.guest_mobile or "مهمان")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Ticket {self.ticket_number} {self.status}>"


class TicketMessage(db.Model):
    __tablename__ = "ticket_messages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_admin_reply = db.Column(db.Boolean, default=False, nullable=False)
    message = db.Column(db.Text, nullable=False)
    attachment_path = db.Column(db.String(255), nullable=True)
    attachment_name = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, index=True)

    ticket = db.relationship("Ticket", back_populates="messages")
    sender = db.relationship("User")

    @property
    def sender_name(self) -> str:
        if self.is_admin_reply:
            return "پشتیبانی فروشگاه"
        return self.sender.full_name if self.sender else self.ticket.owner_name

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TicketMessage {self.id}>"
