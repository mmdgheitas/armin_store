from __future__ import annotations

import sqlite3
from typing import Any

from app.utils.persian import now_utc_iso


def list_active_banners(conn: sqlite3.Connection, placement: str = "hero") -> list[sqlite3.Row]:
    now = now_utc_iso()
    return conn.execute(
        """
        SELECT * FROM banners
        WHERE is_active = 1
          AND placement = ?
          AND (starts_at IS NULL OR starts_at = '' OR starts_at <= ?)
          AND (ends_at IS NULL OR ends_at = '' OR ends_at >= ?)
        ORDER BY sort_order ASC, id DESC
        """,
        (placement, now, now),
    ).fetchall()


def list_all_banners(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM banners ORDER BY placement, sort_order, id DESC").fetchall()


def list_featured_collections(
    conn: sqlite3.Connection, *, active_only: bool = True
) -> list[sqlite3.Row]:
    sql = """
        SELECT fc.*, c.name AS category_name, c.slug AS category_slug
        FROM featured_collections fc
        LEFT JOIN categories c ON c.id = fc.category_id
    """
    if active_only:
        sql += " WHERE fc.is_active = 1"
    sql += " ORDER BY fc.sort_order ASC, fc.id ASC"
    return conn.execute(sql).fetchall()


def list_pages(conn: sqlite3.Connection, *, active_only: bool = True) -> list[sqlite3.Row]:
    sql = "SELECT * FROM pages"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY id ASC"
    return conn.execute(sql).fetchall()


def get_page_by_slug(
    conn: sqlite3.Connection, slug: str, *, active_only: bool = True
) -> sqlite3.Row | None:
    sql = "SELECT * FROM pages WHERE slug = ?"
    if active_only:
        sql += " AND is_active = 1"
    return conn.execute(sql, (slug,)).fetchone()


def find_redirect(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM redirects WHERE old_path = ? AND is_active = 1",
        (path,),
    ).fetchone()
    if row:
        conn.execute("UPDATE redirects SET hits = hits + 1 WHERE id = ?", (row["id"],))
    return row


def create_or_update_redirect(
    conn: sqlite3.Connection, old_path: str, new_path: str, status_code: int = 301
) -> None:
    if old_path == new_path:
        return
    ts = now_utc_iso()
    conn.execute(
        """
        INSERT INTO redirects (old_path, new_path, status_code, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(old_path) DO UPDATE SET
            new_path = excluded.new_path,
            status_code = excluded.status_code,
            is_active = 1,
            updated_at = excluded.updated_at
        """,
        (old_path, new_path, status_code, ts, ts),
    )


def list_redirects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM redirects ORDER BY id DESC").fetchall()


def add_back_in_stock_request(
    conn: sqlite3.Connection, *, product_id: int, variant_id: int, mobile: str
) -> bool:
    ts = now_utc_iso()
    conn.execute(
        """
        INSERT INTO back_in_stock_requests (product_id, variant_id, mobile, notified_at, created_at)
        VALUES (?, ?, ?, NULL, ?)
        ON CONFLICT(variant_id, mobile) DO UPDATE SET notified_at = NULL, created_at = excluded.created_at
        """,
        (product_id, variant_id, mobile, ts),
    )
    return True


def list_back_in_stock_requests(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT b.*, p.title AS product_title, p.slug AS product_slug, v.label AS variant_label, v.sku, v.stock_quantity
        FROM back_in_stock_requests b
        JOIN products p ON p.id = b.product_id
        JOIN product_variants v ON v.id = b.variant_id
        ORDER BY b.notified_at IS NOT NULL ASC, b.id DESC
        """
    ).fetchall()
