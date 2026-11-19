"""داشبورد آماری پنل مدیریت."""
from __future__ import annotations

import platform
import sqlite3
import sys
from pathlib import Path

from flask import jsonify, render_template, request

from app.database.migrator import schema_status
from app.decorators import permission_required
from app.extensions import db
from app.models import AuditLog, BackupLog, Order, Payment, ProductVariant, SmsLog
from app.routes.admin import admin_bp
from app.services import inventory_service, report_service, settings_service
from app.utils.dates import utcnow


def _dir_size(path: Path, limit_files: int = 5000) -> int:
    total = 0
    count = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
                count += 1
                if count >= limit_files:
                    break
    except OSError:
        return total
    return total


@admin_bp.get("/")
@permission_required("dashboard.view")
def dashboard():
    stats = report_service.dashboard_stats()
    payments = (
        db.session.query(Payment).order_by(Payment.created_at.desc()).limit(6).all()
    )
    low_stock = inventory_service.low_stock_variants(limit=6)
    last_backup = (
        db.session.query(BackupLog).order_by(BackupLog.created_at.desc()).first()
    )

    db_path = Path(db.config["DATABASE_PATH"]) if hasattr(db, "config") else Path()
    from flask import current_app

    db_path = Path(current_app.config["DATABASE_PATH"])
    try:
        db_size = db_path.stat().st_size
    except OSError:
        db_size = 0

    storage_size = _dir_size(Path(current_app.config["STORAGE_DIR"]))
    pending_orders = (
        db.session.query(Order)
        .filter(Order.status == "pending_payment")
        .order_by(Order.created_at.desc())
        .limit(6)
        .all()
    )
    system = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "app_version": "1.0.0",
        "db_size": db_size,
        "storage_size": storage_size,
        "schema": schema_status(),
        "last_backup": last_backup,
        "maintenance": settings_service.get_bool("maintenance_mode", False),
        "gateway_enabled": settings_service.get_bool("bitpay_enabled", False),
        "sms_enabled": settings_service.get_bool("sms_enabled", False),
        "gateway_test_mode": settings_service.get_bool("bitpay_test_mode", False),
        "sms_last": db.session.query(SmsLog).order_by(SmsLog.created_at.desc()).first(),
        "uptime_server": utcnow(),
    }
    recent_audit = (
        db.session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(6).all()
    )
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        payments=payments,
        low_stock=low_stock,
        pending_orders=pending_orders,
        system=system,
        recent_audit=recent_audit,
        series=stats["series"],
    )


@admin_bp.get("/api/sales-series")
@permission_required("dashboard.view")
def sales_series():
    try:
        days = min(365, max(7, int(request.args.get("days", 30))))
    except (TypeError, ValueError):
        days = 30
    series = report_service.daily_series(days)
    return jsonify(
        {
            "labels": [item["label"] for item in series],
            "values": [item["value"] for item in series],
            "orders": [item["orders"] for item in series],
        }
    )


@admin_bp.get("/api/stats")
@permission_required("dashboard.view")
def stats_api():
    stats = report_service.dashboard_stats()
    return jsonify(
        {
            "today": stats["today"],
            "month": stats["month"],
            "counts": stats["counts"],
        }
    )
