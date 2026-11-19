from __future__ import annotations

import sqlite3
from typing import Any

from app.utils.persian import jalali_input_to_utc_range, now_utc_iso


def get_order_by_id(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_order_by_number(conn: sqlite3.Connection, order_number: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM orders WHERE order_number = ?", (order_number,)).fetchone()


def get_order_items(conn: sqlite3.Connection, order_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT oi.*, p.slug AS product_slug,
               COALESCE((SELECT pi.thumb_path FROM product_images pi WHERE pi.product_id = oi.product_id ORDER BY pi.is_cover DESC, pi.sort_order ASC LIMIT 1), '') AS thumb_path
        FROM order_items oi
        LEFT JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = ?
        ORDER BY oi.id ASC
        """,
        (order_id,),
    ).fetchall()


def get_order_history(conn: sqlite3.Connection, order_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT h.*, u.full_name AS actor_name, u.username AS actor_username
        FROM order_status_history h
        LEFT JOIN users u ON u.id = h.changed_by_user_id
        WHERE h.order_id = ?
        ORDER BY h.id ASC
        """,
        (order_id,),
    ).fetchall()


def get_order_payments(conn: sqlite3.Connection, order_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC",
        (order_id,),
    ).fetchall()


def list_user_orders(
    conn: sqlite3.Connection, user_id: int, *, page: int = 1, per_page: int = 15
) -> tuple[list[sqlite3.Row], int]:
    total = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    offset = max(0, (page - 1) * per_page)
    rows = conn.execute(
        """
        SELECT o.*,
               (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id = o.id) AS item_count
        FROM orders o
        WHERE o.user_id = ?
        ORDER BY o.id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, per_page, offset),
    ).fetchall()
    return rows, total


def list_orders_admin(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    order_status: str = "",
    payment_status: str = "",
    date_from: str = "",
    date_to: str = "",
    customer_id: int | None = None,
    coupon_code: str = "",
    min_amount_toman: int | None = None,
    max_amount_toman: int | None = None,
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[sqlite3.Row], int]:
    where = []
    params: list[Any] = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(
            "(o.order_number LIKE ? OR o.customer_mobile LIKE ? OR o.customer_name LIKE ? OR o.tracking_code LIKE ?)"
        )
        params.extend([like, like, like, like])
    if order_status:
        where.append("o.order_status = ?")
        params.append(order_status)
    if payment_status:
        where.append("o.payment_status = ?")
        params.append(payment_status)
    if customer_id:
        where.append("o.user_id = ?")
        params.append(customer_id)
    if coupon_code:
        where.append("UPPER(o.coupon_code) = UPPER(?)")
        params.append(coupon_code.strip())
    if min_amount_toman is not None:
        where.append("o.total_rial >= ?")
        params.append(min_amount_toman * 10)
    if max_amount_toman is not None:
        where.append("o.total_rial <= ?")
        params.append(max_amount_toman * 10)
    if date_from:
        utc_from = jalali_input_to_utc_range(date_from, end_of_day=False)
        if utc_from:
            where.append("o.created_at >= ?")
            params.append(utc_from)
    if date_to:
        utc_to = jalali_input_to_utc_range(date_to, end_of_day=True)
        if utc_to:
            where.append("o.created_at <= ?")
            params.append(utc_to)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM orders o {where_sql}", params
    ).fetchone()[0]
    offset = max(0, (page - 1) * per_page)
    rows = conn.execute(
        f"""
        SELECT o.*,
               (SELECT COALESCE(SUM(oi.quantity), 0) FROM order_items oi WHERE oi.order_id = o.id) AS total_qty
        FROM orders o
        {where_sql}
        ORDER BY o.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()
    return rows, total


def get_coupon_by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM coupons WHERE UPPER(code) = UPPER(?)", (code.strip(),)
    ).fetchone()


def list_coupons(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.*,
               COALESCE((SELECT SUM(cu.discount_rial) FROM coupon_usages cu WHERE cu.coupon_id = c.id), 0) AS total_discount_rial
        FROM coupons c
        ORDER BY c.id DESC
        """
    ).fetchall()


def list_payments_admin(
    conn: sqlite3.Connection, *, status: str = "", q: str = "", page: int = 1, per_page: int = 30
) -> tuple[list[sqlite3.Row], int]:
    where = []
    params: list[Any] = []
    if status:
        where.append("p.status = ?")
        params.append(status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append("(o.order_number LIKE ? OR p.id_get LIKE ? OR p.trans_id LIKE ?)")
        params.extend([like, like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM payments p JOIN orders o ON o.id = p.order_id {where_sql}",
        params,
    ).fetchone()[0]
    offset = max(0, (page - 1) * per_page)
    rows = conn.execute(
        f"""
        SELECT p.*, o.order_number, o.customer_name, o.customer_mobile
        FROM payments p
        JOIN orders o ON o.id = p.order_id
        {where_sql}
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()
    return rows, total
