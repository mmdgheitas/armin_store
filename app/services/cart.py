from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from flask import session

from app.repositories.orders import get_coupon_by_code
from app.repositories.settings import get_bool_setting, get_int_setting
from app.utils.persian import now_utc_iso, parse_utc_iso

SESSION_CART_KEY = "guest_cart"
SESSION_COUPON_KEY = "guest_coupon_code"


@dataclass
class CartLine:
    variant_id: int
    product_id: int
    product_title: str
    product_slug: str
    variant_label: str
    sku: str
    unit_price_rial: int
    compare_at_price_rial: int | None
    quantity: int
    available_stock: int
    line_total_rial: int
    thumb_path: str
    warning: str = ""


@dataclass
class CartSummary:
    lines: list[CartLine] = field(default_factory=list)
    item_count: int = 0
    subtotal_rial: int = 0
    shipping_fee_rial: int = 0
    free_shipping_threshold_rial: int = 0
    shipping_enabled: bool = True
    coupon_code: str = ""
    coupon_title: str = ""
    discount_rial: int = 0
    coupon_error: str = ""
    total_rial: int = 0
    warnings: list[str] = field(default_factory=list)
    has_stock_issues: bool = False


def _get_guest_dict() -> dict[str, int]:
    raw = session.get(SESSION_CART_KEY)
    if not isinstance(raw, dict):
        raw = {}
    cleaned: dict[str, int] = {}
    for k, v in raw.items():
        try:
            vid = str(int(k))
            qty = int(v)
            if qty > 0:
                cleaned[vid] = qty
        except (TypeError, ValueError):
            continue
    return cleaned


def _save_guest_dict(data: dict[str, int]) -> None:
    session[SESSION_CART_KEY] = data
    session.modified = True


def _get_or_create_user_cart_id(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return int(row["id"])
    ts = now_utc_iso()
    cur = conn.execute(
        "INSERT INTO carts (user_id, created_at, updated_at) VALUES (?, ?, ?)",
        (user_id, ts, ts),
    )
    return int(cur.lastrowid)


def merge_guest_cart_into_user(conn: sqlite3.Connection, user_id: int) -> None:
    guest_items = _get_guest_dict()
    guest_coupon = session.pop(SESSION_COUPON_KEY, None)
    if not guest_items and not guest_coupon:
        return
    cart_id = _get_or_create_user_cart_id(conn, user_id)
    ts = now_utc_iso()
    for vid_str, qty in guest_items.items():
        vid = int(vid_str)
        vrow = conn.execute(
            "SELECT stock_quantity, is_active FROM product_variants WHERE id = ?",
            (vid,),
        ).fetchone()
        if not vrow or not vrow["is_active"] or int(vrow["stock_quantity"]) <= 0:
            continue
        max_qty = int(vrow["stock_quantity"])
        existing = conn.execute(
            "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND variant_id = ?",
            (cart_id, vid),
        ).fetchone()
        if existing:
            new_qty = min(max_qty, int(existing["quantity"]) + qty)
            conn.execute(
                "UPDATE cart_items SET quantity = ?, updated_at = ? WHERE id = ?",
                (new_qty, ts, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO cart_items (cart_id, variant_id, quantity, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (cart_id, vid, min(max_qty, qty), ts, ts),
            )
    if guest_coupon:
        conn.execute(
            "UPDATE carts SET coupon_code = ?, updated_at = ? WHERE id = ?",
            (guest_coupon, ts, cart_id),
        )
    session.pop(SESSION_CART_KEY, None)
    session.modified = True


def add_to_cart(
    conn: sqlite3.Connection, *, user_id: int | None, variant_id: int, quantity: int = 1
) -> tuple[bool, str]:
    if quantity <= 0:
        return False, "تعداد باید حداقل ۱ باشد."
    vrow = conn.execute(
        """
        SELECT v.*, p.title AS product_title, p.status AS product_status
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id = ?
        """,
        (variant_id,),
    ).fetchone()
    if not vrow or vrow["product_status"] != "published" or not vrow["is_active"]:
        return False, "این محصول در حال حاضر قابل خرید نیست."
    stock = int(vrow["stock_quantity"])
    if stock <= 0:
        return False, "این کالا در حال حاضر ناموجود است."

    if user_id:
        cart_id = _get_or_create_user_cart_id(conn, user_id)
        existing = conn.execute(
            "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND variant_id = ?",
            (cart_id, variant_id),
        ).fetchone()
        current_qty = int(existing["quantity"]) if existing else 0
        target_qty = current_qty + quantity
        if target_qty > stock:
            return False, f"حداکثر موجودی قابل سفارش برای این محصول {stock} عدد است."
        ts = now_utc_iso()
        if existing:
            conn.execute(
                "UPDATE cart_items SET quantity = ?, updated_at = ? WHERE id = ?",
                (target_qty, ts, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO cart_items (cart_id, variant_id, quantity, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (cart_id, variant_id, target_qty, ts, ts),
            )
    else:
        gcart = _get_guest_dict()
        current_qty = gcart.get(str(variant_id), 0)
        target_qty = current_qty + quantity
        if target_qty > stock:
            return False, f"حداکثر موجودی قابل سفارش برای این محصول {stock} عدد است."
        gcart[str(variant_id)] = target_qty
        _save_guest_dict(gcart)

    return True, "محصول به سبد خرید اضافه شد."


def update_cart_quantity(
    conn: sqlite3.Connection, *, user_id: int | None, variant_id: int, quantity: int
) -> tuple[bool, str]:
    if quantity <= 0:
        return remove_from_cart(conn, user_id=user_id, variant_id=variant_id)

    vrow = conn.execute(
        "SELECT stock_quantity, is_active FROM product_variants WHERE id = ?",
        (variant_id,),
    ).fetchone()
    if not vrow or not vrow["is_active"]:
        remove_from_cart(conn, user_id=user_id, variant_id=variant_id)
        return False, "این تنوع دیگر فعال نیست و از سبد حذف شد."
    stock = int(vrow["stock_quantity"])
    if stock <= 0:
        return False, "موجودی این کالا به اتمام رسیده است."
    if quantity > stock:
        quantity = stock
        msg = f"تعداد به حداکثر موجودی انبار ({stock} عدد) تنظیم شد."
    else:
        msg = "سبد خرید به‌روزرسانی شد."

    if user_id:
        cart_id = _get_or_create_user_cart_id(conn, user_id)
        ts = now_utc_iso()
        conn.execute(
            "UPDATE cart_items SET quantity = ?, updated_at = ? WHERE cart_id = ? AND variant_id = ?",
            (quantity, ts, cart_id, variant_id),
        )
    else:
        gcart = _get_guest_dict()
        if str(variant_id) in gcart:
            gcart[str(variant_id)] = quantity
            _save_guest_dict(gcart)
    return True, msg


def remove_from_cart(
    conn: sqlite3.Connection, *, user_id: int | None, variant_id: int
) -> tuple[bool, str]:
    if user_id:
        row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            conn.execute(
                "DELETE FROM cart_items WHERE cart_id = ? AND variant_id = ?",
                (row["id"], variant_id),
            )
    else:
        gcart = _get_guest_dict()
        gcart.pop(str(variant_id), None)
        _save_guest_dict(gcart)
    return True, "کالا از سبد خرید حذف شد."


def clear_cart(conn: sqlite3.Connection, *, user_id: int | None) -> None:
    if user_id:
        row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (row["id"],))
            conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = ?", (row["id"],))
    else:
        session.pop(SESSION_CART_KEY, None)
        session.pop(SESSION_COUPON_KEY, None)
        session.modified = True


def set_cart_coupon(
    conn: sqlite3.Connection, *, user_id: int | None, code: str | None
) -> tuple[bool, str]:
    clean = (code or "").strip().upper()
    if not clean:
        if user_id:
            row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                conn.execute("UPDATE carts SET coupon_code = NULL WHERE id = ?", (row["id"],))
        else:
            session.pop(SESSION_COUPON_KEY, None)
            session.modified = True
        return True, "کد تخفیف حذف شد."

    summary = build_cart_summary(conn, user_id=user_id, override_coupon=clean)
    if summary.coupon_error:
        return False, summary.coupon_error

    if user_id:
        cart_id = _get_or_create_user_cart_id(conn, user_id)
        conn.execute(
            "UPDATE carts SET coupon_code = ?, updated_at = ? WHERE id = ?",
            (clean, now_utc_iso(), cart_id),
        )
    else:
        session[SESSION_COUPON_KEY] = clean
        session.modified = True
    return True, f"کد تخفیف «{clean}» با موفقیت اعمال شد."


def validate_and_calculate_coupon(
    conn: sqlite3.Connection,
    *,
    code: str,
    subtotal_rial: int,
    user_id: int | None = None,
    mobile: str = "",
) -> tuple[int, str, str]:
    """Returns (discount_rial, coupon_title, error_message)."""
    if not code:
        return 0, "", ""
    coupon = get_coupon_by_code(conn, code)
    if not coupon or not coupon["is_active"]:
        return 0, "", "کد تخفیف وارد شده معتبر یا فعال نیست."

    now = now_utc_iso()
    if coupon["starts_at"] and coupon["starts_at"] > now:
        return 0, "", "زمان استفاده از این کد تخفیف هنوز آغاز نشده است."
    if coupon["ends_at"] and coupon["ends_at"] < now:
        return 0, "", "مهلت استفاده از این کد تخفیف به پایان رسیده است."

    if coupon["usage_limit"] is not None and int(coupon["used_count"]) >= int(coupon["usage_limit"]):
        return 0, "", "ظرفیت استفاده از این کد تخفیف تکمیل شده است."

    if coupon["per_user_limit"] is not None:
        used_by_user = 0
        if user_id:
            used_by_user = conn.execute(
                "SELECT COUNT(*) FROM coupon_usages WHERE coupon_id = ? AND user_id = ?",
                (coupon["id"], user_id),
            ).fetchone()[0]
        elif mobile:
            used_by_user = conn.execute(
                "SELECT COUNT(*) FROM coupon_usages WHERE coupon_id = ? AND mobile = ?",
                (coupon["id"], mobile),
            ).fetchone()[0]
        if used_by_user >= int(coupon["per_user_limit"]):
            return 0, "", "شما قبلاً به سقف مجاز از این کد تخفیف استفاده کرده‌اید."

    min_order = int(coupon["min_order_rial"] or 0)
    if subtotal_rial < min_order:
        return 0, "", f"حداقل مبلغ سبد خرید برای این کد تخفیف {min_order // 10:,} تومان است."

    if coupon["discount_type"] == "percent":
        discount = (subtotal_rial * int(coupon["discount_value"])) // 100
        if coupon["max_discount_rial"] is not None:
            discount = min(discount, int(coupon["max_discount_rial"]))
    else:
        discount = int(coupon["discount_value"])

    discount = max(0, min(discount, subtotal_rial))
    return discount, coupon["title"] or coupon["code"], ""


def calculate_shipping_rial(conn: sqlite3.Connection, subtotal_rial: int) -> tuple[int, bool, int]:
    """Returns (shipping_fee_rial, shipping_enabled, free_shipping_threshold_rial)."""
    enabled = get_bool_setting(conn, "shipping_enabled", True)
    fee = get_int_setting(conn, "shipping_fee_rial", 650000)
    threshold = get_int_setting(conn, "free_shipping_threshold_rial", 15000000)
    if not enabled or subtotal_rial <= 0:
        return 0, enabled, threshold
    if threshold > 0 and subtotal_rial >= threshold:
        return 0, enabled, threshold
    return max(0, fee), enabled, threshold


def build_cart_summary(
    conn: sqlite3.Connection,
    *,
    user_id: int | None,
    override_coupon: str | None = None,
    mobile: str = "",
) -> CartSummary:
    raw_items: list[tuple[int, int]] = []
    coupon_code = ""

    if user_id:
        crow = conn.execute("SELECT id, coupon_code FROM carts WHERE user_id = ?", (user_id,)).fetchone()
        if crow:
            coupon_code = crow["coupon_code"] or ""
            rows = conn.execute(
                "SELECT variant_id, quantity FROM cart_items WHERE cart_id = ? ORDER BY id ASC",
                (crow["id"],),
            ).fetchall()
            raw_items = [(int(r["variant_id"]), int(r["quantity"])) for r in rows]
    else:
        coupon_code = session.get(SESSION_COUPON_KEY, "") or ""
        gcart = _get_guest_dict()
        raw_items = [(int(k), int(v)) for k, v in gcart.items()]

    if override_coupon is not None:
        coupon_code = override_coupon

    summary = CartSummary(coupon_code=coupon_code)
    if not raw_items:
        _, shipping_enabled, threshold = calculate_shipping_rial(conn, 0)
        summary.shipping_enabled = shipping_enabled
        summary.free_shipping_threshold_rial = threshold
        return summary

    v_ids = [vid for vid, _ in raw_items]
    ph = ",".join("?" for _ in v_ids)
    vrows = conn.execute(
        f"""
        SELECT v.*, p.title AS product_title, p.slug AS product_slug, p.status AS product_status,
               COALESCE((SELECT pi.thumb_path FROM product_images pi WHERE pi.product_id = p.id ORDER BY pi.is_cover DESC, pi.sort_order ASC LIMIT 1), '') AS thumb_path
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        WHERE v.id IN ({ph})
        """,
        v_ids,
    ).fetchall()
    vmap = {int(r["id"]): r for r in vrows}

    for vid, qty in raw_items:
        r = vmap.get(vid)
        if not r or r["product_status"] != "published" or not r["is_active"]:
            summary.warnings.append("یکی از کالاهای سبد خرید دیگر در دسترس نیست.")
            summary.has_stock_issues = True
            continue
        stock = int(r["stock_quantity"])
        warning = ""
        effective_qty = qty
        if stock <= 0:
            warning = "ناموجود"
            summary.has_stock_issues = True
            summary.warnings.append(f"«{r['product_title']} - {r['label']}» ناموجود شده است.")
            effective_qty = 0
        elif qty > stock:
            warning = f"تنها {stock} عدد موجود است"
            summary.has_stock_issues = True
            summary.warnings.append(f"موجودی «{r['product_title']} - {r['label']}» کمتر از تعداد درخواستی است.")
            effective_qty = stock

        unit_price = int(r["price_rial"])
        line_total = unit_price * effective_qty
        summary.lines.append(
            CartLine(
                variant_id=vid,
                product_id=int(r["product_id"]),
                product_title=r["product_title"],
                product_slug=r["product_slug"],
                variant_label=r["label"],
                sku=r["sku"],
                unit_price_rial=unit_price,
                compare_at_price_rial=r["compare_at_price_rial"],
                quantity=qty,
                available_stock=stock,
                line_total_rial=line_total,
                thumb_path=r["thumb_path"],
                warning=warning,
            )
        )
        summary.subtotal_rial += line_total
        summary.item_count += qty

    shipping_fee, shipping_enabled, threshold = calculate_shipping_rial(conn, summary.subtotal_rial)
    summary.shipping_fee_rial = shipping_fee
    summary.shipping_enabled = shipping_enabled
    summary.free_shipping_threshold_rial = threshold

    if coupon_code and summary.subtotal_rial > 0:
        disc, c_title, err = validate_and_calculate_coupon(
            conn,
            code=coupon_code,
            subtotal_rial=summary.subtotal_rial,
            user_id=user_id,
            mobile=mobile,
        )
        summary.discount_rial = disc
        summary.coupon_title = c_title
        summary.coupon_error = err

    summary.total_rial = max(0, summary.subtotal_rial - summary.discount_rial + summary.shipping_fee_rial)
    return summary
