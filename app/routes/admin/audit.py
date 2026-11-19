"""مشاهده لاگ رویدادهای مدیریتی."""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import render_template, request
from sqlalchemy import or_

from app.decorators import permission_required
from app.extensions import db
from app.models import AuditLog, User
from app.routes.admin import admin_bp
from app.utils.dates import parse_date
from app.utils.pagination import paginate


@admin_bp.get("/audit")
@permission_required("audit.view")
def audit_logs():
    stmt = db.session.query(AuditLog).order_by(AuditLog.created_at.desc())
    q = (request.args.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(or_(AuditLog.action.like(like), AuditLog.entity_type.like(like)))
    admin_id = request.args.get("admin", type=int)
    if admin_id:
        stmt = stmt.filter(AuditLog.admin_id == admin_id)
    from_date = parse_date(request.args.get("from"))
    to_date = parse_date(request.args.get("to"))
    if from_date:
        stmt = stmt.filter(
            AuditLog.created_at >= datetime(from_date.year, from_date.month, from_date.day) - timedelta(hours=3, minutes=30)
        )
    if to_date:
        stmt = stmt.filter(
            AuditLog.created_at
            < datetime(to_date.year, to_date.month, to_date.day) + timedelta(days=1)
            - timedelta(hours=3, minutes=30)
        )
    page = paginate(stmt, per_page=40)
    admins = db.session.query(User).filter(User.id.in_(db.session.query(AuditLog.admin_id))).all()
    return render_template(
        "admin/audit.html", page=page, q=q, admins=admins, admin_id=admin_id, args=request.args
    )
