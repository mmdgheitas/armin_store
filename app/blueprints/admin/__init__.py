from __future__ import annotations

from flask import Blueprint, g

from app.db import get_db
from app.services.auth import has_permission

admin_bp = Blueprint("admin", __name__)


@admin_bp.context_processor
def inject_admin_context():
    counts = {"pending_reviews": 0, "failed_payments": 0, "pending_orders": 0, "low_stock": 0}
    if getattr(g, "current_user", None):
        conn = get_db()
        counts["pending_reviews"] = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE status = 'pending'"
        ).fetchone()[0]
        counts["failed_payments"] = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE status IN ('failed', 'amount_mismatch')"
        ).fetchone()[0]
        counts["pending_orders"] = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE order_status IN ('paid', 'processing')"
        ).fetchone()[0]
        counts["low_stock"] = conn.execute(
            "SELECT COUNT(*) FROM product_variants WHERE is_active = 1 AND stock_quantity <= low_stock_threshold"
        ).fetchone()[0]
    return {
        "admin_counts": counts,
        "has_permission": lambda perm: has_permission(getattr(g, "current_user", None), perm),
    }


from app.blueprints.admin import dashboard, modules, orders, products  # noqa: E402, F401
