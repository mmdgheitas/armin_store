from __future__ import annotations

import sqlite3
from typing import Any

from app.utils.persian import now_utc_iso


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_mobile(conn: sqlite3.Connection, mobile: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE mobile = ?", (mobile,)).fetchone()


def get_user_by_email(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email,)).fetchone()


def get_user_by_username_or_email(conn: sqlite3.Connection, identifier: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE LOWER(email) = LOWER(?) OR LOWER(username) = LOWER(?)",
        (identifier, identifier),
    ).fetchone()


def create_user(
    conn: sqlite3.Connection,
    *,
    mobile: str | None = None,
    email: str | None = None,
    username: str | None = None,
    password_hash: str | None = None,
    full_name: str = "",
    role: str = "customer",
    mobile_verified: int = 0,
    email_verified: int = 0,
    otp_2fa_enabled: int = 0,
) -> int:
    ts = now_utc_iso()
    cur = conn.execute(
        """
        INSERT INTO users
            (mobile, email, username, password_hash, full_name, role, mobile_verified, email_verified, otp_2fa_enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mobile or None,
            email.lower() if email else None,
            username or None,
            password_hash,
            full_name.strip(),
            role,
            mobile_verified,
            email_verified,
            otp_2fa_enabled,
            ts,
            ts,
        ),
    )
    return int(cur.lastrowid)


def update_user_last_login(conn: sqlite3.Connection, user_id: int) -> None:
    ts = now_utc_iso()
    conn.execute(
        "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
        (ts, ts, user_id),
    )


def list_provinces(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM provinces ORDER BY sort_order, name").fetchall()


def list_cities_by_province(conn: sqlite3.Connection, province_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cities WHERE province_id = ? ORDER BY sort_order, name",
        (province_id,),
    ).fetchall()


def get_province_and_city_names(
    conn: sqlite3.Connection, province_id: int | None, city_id: int | None
) -> tuple[str, str]:
    p_name = ""
    c_name = ""
    if province_id:
        prow = conn.execute("SELECT name FROM provinces WHERE id = ?", (province_id,)).fetchone()
        if prow:
            p_name = prow["name"]
    if city_id:
        crow = conn.execute(
            "SELECT name FROM cities WHERE id = ? AND (? IS NULL OR province_id = ?)",
            (city_id, province_id, province_id),
        ).fetchone()
        if crow:
            c_name = crow["name"]
    return p_name, c_name


def list_user_addresses(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id DESC",
        (user_id,),
    ).fetchall()


def get_user_address(conn: sqlite3.Connection, address_id: int, user_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM addresses WHERE id = ? AND user_id = ?",
        (address_id, user_id),
    ).fetchone()


def save_address(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    address_id: int | None = None,
    title: str,
    recipient_name: str,
    mobile: str,
    province_id: int | None,
    province_name: str,
    city_id: int | None,
    city_name: str,
    address_line: str,
    postal_code: str,
    is_default: bool = False,
) -> int:
    ts = now_utc_iso()
    if is_default:
        conn.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
    if address_id:
        conn.execute(
            """
            UPDATE addresses SET
                title = ?, recipient_name = ?, mobile = ?,
                province_id = ?, province_name = ?, city_id = ?, city_name = ?,
                address_line = ?, postal_code = ?, is_default = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                title,
                recipient_name,
                mobile,
                province_id,
                province_name,
                city_id,
                city_name,
                address_line,
                postal_code,
                1 if is_default else 0,
                ts,
                address_id,
                user_id,
            ),
        )
        return address_id
    cur = conn.execute(
        """
        INSERT INTO addresses
            (user_id, title, recipient_name, mobile, province_id, province_name, city_id, city_name, address_line, postal_code, is_default, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            title,
            recipient_name,
            mobile,
            province_id,
            province_name,
            city_id,
            city_name,
            address_line,
            postal_code,
            1 if is_default else 0,
            ts,
            ts,
        ),
    )
    return int(cur.lastrowid)


def delete_user_address(conn: sqlite3.Connection, address_id: int, user_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM addresses WHERE id = ? AND user_id = ?",
        (address_id, user_id),
    )
    return cur.rowcount > 0


def list_customers(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    blocked: str = "",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[sqlite3.Row], int]:
    where = ["u.role = 'customer'"]
    params: list[Any] = []
    if q:
        like = f"%{q.strip()}%"
        where.append("(u.full_name LIKE ? OR u.mobile LIKE ? OR u.email LIKE ?)")
        params.extend([like, like, like])
    if blocked == "1":
        where.append("u.is_blocked = 1")
    elif blocked == "0":
        where.append("u.is_blocked = 0")
    where_sql = "WHERE " + " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM users u {where_sql}", params).fetchone()[0]
    offset = max(0, (page - 1) * per_page)
    rows = conn.execute(
        f"""
        SELECT u.*,
               (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS order_count,
               (SELECT COALESCE(SUM(o.total_rial), 0) FROM orders o WHERE o.user_id = u.id AND o.payment_status = 'paid') AS total_spent_rial
        FROM users u
        {where_sql}
        ORDER BY u.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()
    return rows, total
