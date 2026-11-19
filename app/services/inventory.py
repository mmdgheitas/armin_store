from __future__ import annotations

import logging
import sqlite3
from typing import Iterable

from app.services.smsir import send_admin_alert_sms, send_event_sms
from app.utils.persian import now_utc_iso

logger = logging.getLogger("armin_store.inventory")


class InsufficientStockError(Exception):
    def __init__(
        self,
        *,
        variant_id: int,
        product_title: str,
        variant_label: str,
        available: int,
        requested: int,
    ) -> None:
        self.variant_id = variant_id
        self.product_title = product_title
        self.variant_label = variant_label
        self.available = available
        self.requested = requested
        super().__init__(
            f"موجودی «{product_title} - {variant_label}» کافی نیست (موجود: {available}، درخواستی: {requested})."
        )


def adjust_variant_stock(
    conn: sqlite3.Connection,
    *,
    variant_id: int,
    delta: int,
    reason: str,
    order_id: int | None = None,
    admin_id: int | None = None,
    note: str = "",
    notify: bool = True,
) -> tuple[int, int]:
    """
    Atomically adjust stock for a single variant inside an active transaction.
    Returns (before_qty, after_qty).
    """
    row = conn.execute(
        """
        SELECT v.id, v.stock_quantity, v.low_stock_threshold, v.label, v.sku,
               p.id AS product_id, p.title AS product_title, p.slug AS product_slug
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = ?
        """,
        (variant_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"تنوع محصول با شناسه {variant_id} یافت نشد.")

    before_qty = int(row["stock_quantity"])
    ts = now_utc_iso()

    if delta < 0:
        cur = conn.execute(
            """
            UPDATE product_variants
            SET stock_quantity = stock_quantity + ?, updated_at = ?
            WHERE id = ? AND stock_quantity + ? >= 0
            """,
            (delta, ts, variant_id, delta),
        )
        if cur.rowcount == 0:
            raise InsufficientStockError(
                variant_id=variant_id,
                product_title=row["product_title"],
                variant_label=row["label"],
                available=before_qty,
                requested=abs(delta),
            )
    elif delta > 0:
        conn.execute(
            """
            UPDATE product_variants
            SET stock_quantity = stock_quantity + ?, updated_at = ?
            WHERE id = ?
            """,
            (delta, ts, variant_id),
        )
    else:
        return before_qty, before_qty

    after_row = conn.execute(
        "SELECT stock_quantity FROM product_variants WHERE id = ?", (variant_id,)
    ).fetchone()
    after_qty = int(after_row["stock_quantity"])

    conn.execute(
        """
        INSERT INTO stock_movements
            (variant_id, delta, reason, order_id, admin_id, note, before_qty, after_qty, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (variant_id, delta, reason, order_id, admin_id, note[:250], before_qty, after_qty, ts),
    )

    if notify:
        threshold = int(row["low_stock_threshold"])
        if delta < 0 and after_qty <= threshold and (before_qty > threshold or after_qty == 0):
            try:
                send_admin_alert_sms(
                    conn,
                    toggle_key="sms_event_low_stock_admin",
                    template_setting_key="smsir_low_stock_template_id",
                    event_type="low_stock_alert",
                    params={
                        "SKU": row["sku"],
                        "QTY": str(after_qty),
                    },
                    fallback_text=f"هشدار کمبود موجودی: {row['product_title']} ({row['label']}) - موجودی فعلی: {after_qty}",
                )
            except Exception as exc:
                logger.warning("Low stock SMS alert error: %s", exc)

        if before_qty <= 0 < after_qty:
            try:
                _notify_back_in_stock(
                    conn,
                    variant_id=variant_id,
                    product_title=row["product_title"],
                    variant_label=row["label"],
                    sku=row["sku"],
                )
            except Exception as exc:
                logger.warning("Back in stock notify error: %s", exc)

    return before_qty, after_qty


def _notify_back_in_stock(
    conn: sqlite3.Connection,
    *,
    variant_id: int,
    product_title: str,
    variant_label: str,
    sku: str,
) -> None:
    reqs = conn.execute(
        "SELECT id, mobile FROM back_in_stock_requests WHERE variant_id = ? AND notified_at IS NULL",
        (variant_id,),
    ).fetchall()
    if not reqs:
        return
    ts = now_utc_iso()
    for req in reqs:
        send_event_sms(
            conn,
            toggle_key="sms_event_back_in_stock",
            template_setting_key="smsir_back_in_stock_template_id",
            recipient=req["mobile"],
            event_type="back_in_stock",
            params={"PRODUCT": product_title[:25], "SKU": sku[:25]},
            fallback_text=f"مژده! «{product_title} ({variant_label})» در فروشگاه آرمین موجود شد.",
        )
        conn.execute(
            "UPDATE back_in_stock_requests SET notified_at = ? WHERE id = ?",
            (ts, req["id"]),
        )


def reserve_order_stock(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    lines: Iterable[tuple[int, int]],
    admin_id: int | None = None,
) -> None:
    """Deduct stock for (variant_id, quantity) lines and mark stock_reserved=1."""
    for variant_id, qty in lines:
        adjust_variant_stock(
            conn,
            variant_id=variant_id,
            delta=-int(qty),
            reason="order_reserve",
            order_id=order_id,
            admin_id=admin_id,
            note="رزرو موجودی هنگام ثبت سفارش",
        )
    conn.execute(
        "UPDATE orders SET stock_reserved = 1, stock_restocked = 0, updated_at = ? WHERE id = ?",
        (now_utc_iso(), order_id),
    )


def restock_order_exactly_once(
    conn: sqlite3.Connection,
    *,
    order_id: int,
    reason: str = "order_cancel_restock",
    admin_id: int | None = None,
    note: str = "",
) -> bool:
    """
    Idempotently return stock for an order that previously reserved stock.
    Returns True if restock actually happened in this call, False if already restocked or never reserved.
    """
    ts = now_utc_iso()
    cur = conn.execute(
        """
        UPDATE orders
        SET stock_restocked = 1, updated_at = ?
        WHERE id = ? AND stock_reserved = 1 AND stock_restocked = 0
        """,
        (ts, order_id),
    )
    if cur.rowcount == 0:
        return False

    items = conn.execute(
        "SELECT variant_id, quantity FROM order_items WHERE order_id = ?", (order_id,)
    ).fetchall()
    for it in items:
        adjust_variant_stock(
            conn,
            variant_id=int(it["variant_id"]),
            delta=int(it["quantity"]),
            reason=reason,
            order_id=order_id,
            admin_id=admin_id,
            note=note or "بازگشت موجودی سفارش",
        )
    return True


def expire_unpaid_orders(conn: sqlite3.Connection) -> int:
    """Expire pending_payment orders past expires_at and restock them idempotently."""
    now = now_utc_iso()
    rows = conn.execute(
        """
        SELECT id, order_status FROM orders
        WHERE order_status = 'pending_payment'
          AND payment_status = 'pending'
          AND expires_at IS NOT NULL
          AND expires_at <= ?
        """,
        (now,),
    ).fetchall()
    count = 0
    for r in rows:
        oid = int(r["id"])
        cur = conn.execute(
            """
            UPDATE orders
            SET order_status = 'failed', payment_status = 'failed', updated_at = ?
            WHERE id = ? AND order_status = 'pending_payment'
            """,
            (now, oid),
        )
        if cur.rowcount == 0:
            continue
        conn.execute(
            """
            INSERT INTO order_status_history (order_id, from_status, to_status, note, changed_by_user_id, created_at)
            VALUES (?, 'pending_payment', 'failed', 'انقضای مهلت پرداخت و آزادسازی موجودی', NULL, ?)
            """,
            (oid, now),
        )
        restock_order_exactly_once(
            conn,
            order_id=oid,
            reason="order_expired_restock",
            note="آزادسازی موجودی به علت انقضای مهلت پرداخت",
        )
        count += 1
    return count
