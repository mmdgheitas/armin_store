from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.repositories.settings import get_bool_setting
from app.utils.persian import now_utc_iso, sha256_hex

_LINK_RE = re.compile(r"(https?://|www\.|[a-zA-Z0-9-]+\.(com|ir|net|org|io)/)", re.IGNORECASE)


def has_user_purchased_product(
    conn: sqlite3.Connection, *, user_id: int, product_id: int
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE o.user_id = ?
          AND oi.product_id = ?
          AND (o.payment_status = 'paid' OR o.order_status IN ('paid', 'processing', 'shipped', 'delivered'))
        LIMIT 1
        """,
        (user_id, product_id),
    ).fetchone()
    return row is not None


def recalculate_product_rating(conn: sqlite3.Connection, product_id: int) -> None:
    rows = conn.execute(
        """
        SELECT rating, COUNT(*) AS cnt
        FROM reviews
        WHERE product_id = ? AND status = 'approved'
        GROUP BY rating
        """,
        (product_id,),
    ).fetchall()
    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    total_cnt = 0
    total_score = 0
    for r in rows:
        rating = int(r["rating"])
        cnt = int(r["cnt"])
        if rating in counts:
            counts[rating] = cnt
            total_cnt += cnt
            total_score += rating * cnt
    avg = round(total_score / total_cnt, 2) if total_cnt > 0 else 0.0
    conn.execute(
        """
        UPDATE products
        SET avg_rating = ?, review_count = ?,
            rating_1_count = ?, rating_2_count = ?, rating_3_count = ?,
            rating_4_count = ?, rating_5_count = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            avg,
            total_cnt,
            counts[1],
            counts[2],
            counts[3],
            counts[4],
            counts[5],
            now_utc_iso(),
            product_id,
        ),
    )


def submit_review(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    user_id: int,
    rating: int,
    title: str,
    body: str,
    honeypot: str = "",
) -> tuple[bool, str]:
    if honeypot.strip():
        return False, "درخواست نامعتبر است."
    if rating < 1 or rating > 5:
        return False, "امتیاز باید بین ۱ تا ۵ باشد."
    clean_title = (title or "").strip()
    clean_body = (body or "").strip()
    if len(clean_body) < 5:
        return False, "متن دیدگاه باید حداقل ۵ کاراکتر باشد."
    if _LINK_RE.search(clean_title) or _LINK_RE.search(clean_body):
        return False, "ارسال لینک یا آدرس وب‌سایت در متن دیدگاه مجاز نیست."

    existing = conn.execute(
        "SELECT id FROM reviews WHERE product_id = ? AND user_id = ?",
        (product_id, user_id),
    ).fetchone()
    if existing:
        return False, "شما قبلاً برای این محصول دیدگاه ثبت کرده‌اید."

    verified = has_user_purchased_product(conn, user_id=user_id, product_id=product_id)
    auto_approve = verified and get_bool_setting(conn, "auto_approve_verified_reviews", False)
    status = "approved" if auto_approve else "pending"
    ts = now_utc_iso()

    conn.execute(
        """
        INSERT INTO reviews
            (product_id, user_id, rating, title, body, status, is_verified_buyer, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            user_id,
            rating,
            clean_title[:120],
            clean_body[:2000],
            status,
            1 if verified else 0,
            ts,
            ts,
        ),
    )
    if status == "approved":
        recalculate_product_rating(conn, product_id)
        return True, "دیدگاه شما با موفقیت ثبت و منتشر شد."
    return True, "دیدگاه شما ثبت شد و پس از تایید مدیر منتشر خواهد شد."


def list_product_reviews(
    conn: sqlite3.Connection, product_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, u.full_name AS author_name
        FROM reviews r
        JOIN users u ON u.id = r.user_id
        WHERE r.product_id = ? AND r.status = 'approved'
        ORDER BY r.is_verified_buyer DESC, r.helpful_count DESC, r.id DESC
        """,
        (product_id,),
    ).fetchall()


def vote_review_helpful(
    conn: sqlite3.Connection, *, review_id: int, user_id: int | None, ip_address: str
) -> tuple[bool, int]:
    ip_hash = sha256_hex(f"{ip_address}:{user_id or 'anon'}")
    try:
        conn.execute(
            "INSERT INTO review_votes (review_id, user_id, ip_hash, created_at) VALUES (?, ?, ?, ?)",
            (review_id, user_id, ip_hash, now_utc_iso()),
        )
        conn.execute(
            "UPDATE reviews SET helpful_count = helpful_count + 1 WHERE id = ?",
            (review_id,),
        )
        voted = True
    except sqlite3.IntegrityError:
        voted = False

    row = conn.execute(
        "SELECT helpful_count FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()
    return voted, int(row["helpful_count"]) if row else 0
