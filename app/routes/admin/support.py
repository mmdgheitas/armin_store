"""مدیریت تیکت‌های پشتیبانی."""
from __future__ import annotations

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for
from sqlalchemy import or_

from app.constants import TICKET_DEPARTMENTS, TICKET_PRIORITIES, TICKET_STATUSES
from app.decorators import permission_required
from app.extensions import db
from app.models import Ticket, TicketMessage
from app.routes.admin import admin_bp
from app.routes.admin.helpers import log_admin, text_field
from app.services import media_service, settings_service, support_service
from app.utils.dates import utcnow
from app.utils.files import FileValidationError
from app.utils.pagination import paginate


@admin_bp.get("/tickets")
@permission_required("tickets.manage")
def tickets():
    stmt = db.session.query(Ticket).order_by(Ticket.updated_at.desc())
    status = request.args.get("status")
    if status in dict(TICKET_STATUSES):
        stmt = stmt.filter(Ticket.status == status)
    priority = request.args.get("priority")
    if priority in dict(TICKET_PRIORITIES):
        stmt = stmt.filter(Ticket.priority == priority)
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(
            or_(
                Ticket.subject.like(like),
                Ticket.ticket_number.like(like),
                Ticket.guest_mobile.like(like),
            )
        )
    page = paginate(stmt, per_page=25)
    return render_template(
        "admin/tickets/list.html",
        page=page,
        statuses=TICKET_STATUSES,
        priorities=TICKET_PRIORITIES,
        status=status,
        priority=priority,
        q=q,
    )


@admin_bp.get("/tickets/<int:ticket_id>")
@permission_required("tickets.manage")
def ticket_detail(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None:
        abort(404)
    return render_template(
        "admin/tickets/detail.html",
        ticket=ticket,
        messages=support_service.ticket_messages(ticket),
        statuses=TICKET_STATUSES,
        priorities=TICKET_PRIORITIES,
    )


@admin_bp.post("/tickets/<int:ticket_id>/reply")
@permission_required("tickets.manage")
def ticket_reply(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None:
        abort(404)
    attachment = None
    file = request.files.get("attachment")
    if file is not None and file.filename:
        try:
            saved = media_service.save_upload(
                file,
                "tickets",
                kind="document",
                max_mb=max(1, settings_service.get_int("ticket_attachment_max_mb", 5)),
                private=True,
            )
            attachment = {"path": saved["path"], "name": saved["name"]}
        except FileValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("admin.ticket_detail", ticket_id=ticket.id))
    ok, message = support_service.add_message(
        ticket, request.form.get("message", ""), g.user, is_admin_reply=True, attachment=attachment
    )
    if ok:
        log_admin("ticket.reply", "ticket", ticket.id)
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket.id))


@admin_bp.post("/tickets/<int:ticket_id>/status")
@permission_required("tickets.manage")
def ticket_status(ticket_id: int):
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None:
        abort(404)
    status = request.form.get("status")
    if status not in dict(TICKET_STATUSES):
        abort(400)
    ticket.status = status
    if status == "closed":
        ticket.closed_at = utcnow()
    ticket.updated_at = utcnow()
    db.session.commit()
    log_admin("ticket.status", "ticket", ticket.id, new={"status": status})
    flash("وضعیت تیکت به‌روزرسانی شد.", "success")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket.id))


@admin_bp.get("/tickets/<int:ticket_id>/attachment/<int:message_id>")
@permission_required("tickets.manage")
def ticket_attachment(ticket_id: int, message_id: int):
    message = db.session.get(TicketMessage, message_id)
    if message is None or message.ticket_id != ticket_id or not message.attachment_path:
        abort(404)
    path = media_service.private_file_path(message.attachment_path)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=True, download_name=message.attachment_name or path.name)
