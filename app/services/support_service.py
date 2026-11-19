"""سرویس پشتیبانی: ایجاد تیکت، پاسخ، بستن و کنترل دسترسی."""
from __future__ import annotations

import secrets

from flask import current_app, session

from app.extensions import db
from app.models import Ticket, TicketMessage, User
from app.services import notification_service, settings_service
from app.utils.dates import utcnow
from app.utils.security import generate_token, is_valid_mobile, normalize_mobile

GUEST_TICKETS_SESSION_KEY = "guest_tickets"


def _ticket_number() -> str:
    for _ in range(6):
        number = f"TK-{secrets.token_hex(4).upper()}"
        if db.session.query(Ticket.id).filter(Ticket.ticket_number == number).first() is None:
            return number
    return f"TK-{int(utcnow().timestamp())}"


def create_ticket(
    *,
    user: User | None,
    subject: str,
    message: str,
    department: str = "support",
    priority: str = "normal",
    guest_name: str = "",
    guest_mobile: str = "",
    guest_email: str = "",
) -> tuple[Ticket | None, list[str]]:
    if not settings_service.get_bool("support_enabled", True):
        return None, ["سیستم پشتیبانی در حال حاضر غیرفعال است."]

    errors: list[str] = []
    subject = (subject or "").strip()
    message = (message or "").strip()
    if len(subject) < 3:
        errors.append("موضوع تیکت را وارد کنید.")
    if len(message) < 10:
        errors.append("متن پیام باید حداقل ۱۰ کاراکتر باشد.")
    if department not in {"sales", "support", "order", "other"}:
        department = "support"
    if priority not in {"low", "normal", "high", "urgent"}:
        priority = "normal"
    mobile = normalize_mobile(guest_mobile)
    if user is None:
        if not is_valid_mobile(mobile):
            errors.append("برای ثبت تیکت شماره موبایل معتبر لازم است.")
    if errors:
        return None, errors

    ticket = Ticket(
        ticket_number=_ticket_number(),
        user_id=getattr(user, "id", None),
        guest_name=(guest_name or "").strip()[:150] or None,
        guest_mobile=mobile or None,
        guest_email=(guest_email or "").strip()[:190] or None,
        subject=subject[:200],
        department=department,
        priority=priority,
        status="open",
        last_reply_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(ticket)
    db.session.flush()
    db.session.add(
        TicketMessage(
            ticket_id=ticket.id,
            sender_id=getattr(user, "id", None),
            is_admin_reply=False,
            message=message[:5000],
            created_at=utcnow(),
        )
    )
    db.session.commit()

    if user is None:
        tickets = session.get(GUEST_TICKETS_SESSION_KEY) or []
        tickets.append(ticket.ticket_number)
        session[GUEST_TICKETS_SESSION_KEY] = tickets[-10:]
        session.modified = True
    return ticket, []


def can_access(ticket: Ticket | None, user: User | None) -> bool:
    if ticket is None:
        return False
    if user is not None and (ticket.user_id == user.id or user.is_admin):
        return True
    guest_numbers = session.get(GUEST_TICKETS_SESSION_KEY) or []
    return ticket.ticket_number in guest_numbers


def add_message(ticket: Ticket, message: str, sender: User | None, is_admin_reply: bool = False, attachment: dict | None = None) -> tuple[bool, str]:
    message = (message or "").strip()
    if len(message) < 2 and not attachment:
        return False, "متن پیام را وارد کنید."
    if len(message) > 5000:
        return False, "متن پیام بیش از حد مجاز است."
    db.session.add(
        TicketMessage(
            ticket_id=ticket.id,
            sender_id=getattr(sender, "id", None),
            is_admin_reply=is_admin_reply,
            message=message[:5000],
            attachment_path=(attachment or {}).get("path"),
            attachment_name=(attachment or {}).get("name"),
            created_at=utcnow(),
        )
    )
    ticket.status = "answered" if is_admin_reply else "pending"
    ticket.last_reply_at = utcnow()
    ticket.updated_at = utcnow()
    if not is_admin_reply and ticket.status == "closed":
        ticket.status = "open"
    db.session.commit()

    if is_admin_reply:
        try:
            notification_service.on_ticket_reply(ticket)
        except Exception:  # pragma: no cover
            current_app.logger.exception("ticket notification failed")
    return True, "پیام ثبت شد."


def close_ticket(ticket: Ticket, user: User | None) -> tuple[bool, str]:
    if not can_access(ticket, user):
        return False, "دسترسی مجاز نیست."
    ticket.status = "closed"
    ticket.closed_at = utcnow()
    ticket.updated_at = utcnow()
    db.session.commit()
    return True, "تیکت بسته شد."


def ticket_messages(ticket: Ticket):
    return (
        db.session.query(TicketMessage)
        .filter(TicketMessage.ticket_id == ticket.id)
        .order_by(TicketMessage.created_at)
        .all()
    )


def tickets_for_user(user: User, page_query=None):
    query = db.session.query(Ticket).filter(Ticket.user_id == user.id)
    return query.order_by(Ticket.updated_at.desc())
