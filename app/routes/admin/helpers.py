"""ابزارهای مشترک پنل مدیریت."""
from __future__ import annotations

from flask import abort, flash, g, redirect, request, url_for

from app.extensions import db
from app.services import audit_service
from app.utils.money import parse_money_input


def int_field(name: str, default: int = 0) -> int:
    value = parse_money_input(request.form.get(name))
    return default if value is None else value


def bool_field(name: str) -> bool:
    return request.form.get(name) in {"1", "on", "true", "yes"}


def text_field(name: str, default: str = "", max_length: int | None = None) -> str:
    value = (request.form.get(name) or default).strip()
    return value[:max_length] if max_length else value


def flash_result(ok: bool, message: str) -> None:
    flash(message, "success" if ok else "error")


def require_entity(model, entity_id, label: str = "مورد"):
    entity = db.session.get(model, entity_id)
    if entity is None:
        abort(404, description=f"{label} یافت نشد.")
    return entity


def log_admin(action: str, entity_type: str, entity_id=None, old=None, new=None) -> None:
    audit_service.log(
        action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_data=old,
        new_data=new,
        admin=getattr(g, "user", None),
    )


def pagination_per_page(default: int = 20) -> int:
    try:
        return min(100, max(5, int(request.args.get("per_page", default))))
    except (TypeError, ValueError):
        return default


def active_menu(endpoint: str, prefix: str = "") -> str:
    if endpoint and endpoint.endswith(prefix):
        return "active"
    return ""


def redirect_back(default_endpoint: str):
    target = request.form.get("next") or request.referrer
    if target:
        return redirect(target)
    return redirect(url_for(default_endpoint))
