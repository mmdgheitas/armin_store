"""ثبت رویدادهای مدیریتی (Audit Log) با پوشاندن اطلاعات حساس."""
from __future__ import annotations

import json

from flask import current_app, g, has_app_context, has_request_context

from app.extensions import db
from app.models.content import AuditLog
from app.utils.dates import utcnow
from app.utils.http import client_ip

SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "password_confirm",
    "current_password",
    "new_password",
    "api_key",
    "secret",
    "token",
    "authority_or_token",
    "csrf_token",
    "sms_api_key",
    "bitpay_api_key",
}


def _clean(data):
    if isinstance(data, dict):
        return {
            key: ("***" if any(s in str(key).lower() for s in SENSITIVE_KEYS) else _clean(value))
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_clean(item) for item in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        if isinstance(data, str) and len(data) > 500:
            return data[:500] + "…"
        return data
    return str(data)[:200]


def log(
    action: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
    old_data=None,
    new_data=None,
    admin=None,
    ip: str | None = None,
    commit: bool = True,
) -> AuditLog | None:
    """ثبت یک رویداد مدیریتی. در صورت خطا، عملیات اصلی مختل نمی‌شود."""
    try:
        admin_user = admin
        if admin_user is None and has_request_context():
            admin_user = getattr(g, "user", None)
        entry = AuditLog(
            admin_id=getattr(admin_user, "id", None),
            action=action[:80],
            entity_type=(entity_type or "")[:60] or None,
            entity_id=entity_id,
            old_data=json.dumps(_clean(old_data), ensure_ascii=False)[:4000] if old_data is not None else None,
            new_data=json.dumps(_clean(new_data), ensure_ascii=False)[:4000] if new_data is not None else None,
            ip_address=(ip or (client_ip() if has_request_context() else None) or "")[:64] or None,
            created_at=utcnow(),
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
        return entry
    except Exception:  # pragma: no cover
        db.session.rollback()
        if has_app_context():
            current_app.logger.exception("audit log failed action=%s", action)
        return None


def recent(limit: int = 50):
    return db.session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
