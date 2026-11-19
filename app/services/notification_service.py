"""اعلان‌های درون‌برنامه‌ای و پیامکی مشتری."""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models.content import Notification
from app.utils.dates import utcnow

STATUS_SMS_EVENTS = {
    "paid": "payment_success",
    "processing": "order_processing",
    "ready_to_ship": "order_ready",
    "shipped": "order_shipped",
    "delivered": "order_delivered",
    "cancelled": "order_cancelled",
}

STATUS_TITLES = {
    "paid": "پرداخت سفارش تأیید شد",
    "processing": "سفارش شما در حال پردازش است",
    "ready_to_ship": "سفارش شما آماده ارسال است",
    "shipped": "سفارش شما ارسال شد",
    "delivered": "سفارش شما تحویل داده شد",
    "cancelled": "سفارش شما لغو شد",
    "returned": "مرجوعی سفارش ثبت شد",
    "refunded": "بازپرداخت سفارش انجام شد",
}


def push(user_id: int | None, type_: str, title: str, message: str = "", link: str | None = None) -> None:
    """افزودن اعلان درون‌برنامه‌ای (برای کاربران ثبت‌نام‌شده)."""
    if not user_id:
        return
    try:
        db.session.add(
            Notification(
                user_id=user_id,
                type=type_[:40],
                title=title[:200],
                message=message,
                link=(link or "")[:300] or None,
                is_read=False,
                created_at=utcnow(),
            )
        )
        db.session.commit()
    except Exception:  # pragma: no cover
        db.session.rollback()
        current_app.logger.exception("notification push failed")


def _send_sms_safe(event: str, mobile: str | None, parameters: dict) -> None:
    if not mobile:
        return
    try:
        from app.services import sms_service

        sms_service.send_by_event(event, mobile, parameters)
    except Exception:  # pragma: no cover
        current_app.logger.exception("sms send failed event=%s", event)


def on_order_created(order) -> None:
    push(
        order.user_id,
        "order_created",
        f"سفارش {order.order_number} ثبت شد",
        "سفارش شما ثبت شد و در انتظار پرداخت است.",
        link=f"/account/orders/{order.order_number}",
    )
    _send_sms_safe("order_created", order.customer_mobile, {"ORDER": order.order_number, "AMOUNT": order.total_amount})


def on_order_status_change(order, previous: str, new: str, tracking_code: str | None = None) -> None:
    title = STATUS_TITLES.get(new)
    if title:
        push(
            order.user_id,
            "order_status",
            f"{title} — {order.order_number}",
            f"وضعیت سفارش از «{previous}» به «{new}» تغییر کرد."
            + (f" کد رهگیری: {tracking_code}" if tracking_code else ""),
            link=f"/account/orders/{order.order_number}",
        )
    event = STATUS_SMS_EVENTS.get(new)
    if event:
        _send_sms_safe(
            event,
            order.customer_mobile,
            {"ORDER": order.order_number, "TRACKING": tracking_code or "-"},
        )


def on_ticket_reply(ticket) -> None:
    if ticket.user_id:
        push(
            ticket.user_id,
            "ticket_reply",
            f"پاسخ جدید در تیکت {ticket.ticket_number}",
            "کارشناسان پشتیبانی به تیکت شما پاسخ دادند.",
            link=f"/account/tickets/{ticket.id}",
        )
    mobile = ticket.user.mobile if ticket.user else ticket.guest_mobile
    _send_sms_safe("ticket_reply", mobile, {"TICKET": ticket.ticket_number})


def unread_count(user_id: int | None) -> int:
    if not user_id:
        return 0
    try:
        return (
            db.session.query(Notification)
            .filter(Notification.user_id == user_id, Notification.is_read.is_(False))
            .count()
        )
    except Exception:  # pragma: no cover
        return 0
