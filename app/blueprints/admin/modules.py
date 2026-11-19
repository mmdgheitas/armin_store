from __future__ import annotations

import csv
import io
import json
import math
from typing import Any

from flask import (
    Response,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from app.blueprints.admin import admin_bp
from app.db import get_db, transaction
from app.repositories.audit import list_audit_logs, log_audit
from app.repositories.catalog import ensure_unique_slug, list_categories
from app.repositories.cms import (
    create_or_update_redirect,
    list_all_banners,
    list_back_in_stock_requests,
    list_featured_collections,
    list_pages,
    list_redirects,
)
from app.repositories.orders import list_coupons, list_payments_admin
from app.repositories.settings import get_all_settings, set_settings
from app.repositories.users import list_customers, list_user_addresses
from app.services.auth import (
    ADMIN_ROLES,
    ROLE_LABELS,
    admin_required,
    hash_password,
    validate_password_policy,
)
from app.services.inventory import InsufficientStockError, adjust_variant_stock
from app.services.media import MediaValidationError, delete_media_file, save_uploaded_image
from app.services.orders import ORDER_STATUS_LABELS, PAYMENT_STATUS_LABELS
from app.services.reviews import recalculate_product_rating
from app.services.smsir import SmsService
from app.utils.persian import (
    jalali_input_to_utc_range,
    normalize_iran_mobile,
    now_utc_iso,
    sanitize_html,
    to_english_digits,
    to_jalali,
)


def _to_int(val: Any, default: int = 0) -> int:
    try:
        s = to_english_digits(str(val or "")).replace(",", "").strip()
        return int(s) if s != "" else default
    except ValueError:
        return default


def _audit(action: str, entity_type: str, entity_id: Any = "", before: Any = None, after: Any = None) -> None:
    log_audit(
        get_db(),
        admin_id=int(g.current_user["id"]),
        admin_name=g.current_user.get("username") or g.current_user["full_name"],
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        ip_address=request.remote_addr or "",
        user_agent=request.headers.get("User-Agent", ""),
    )


# ==================== CATEGORIES ====================
@admin_bp.route("/categories", methods=["GET", "POST"])
@admin_required("categories")
def categories_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            cid = _to_int(request.form.get("category_id"), 0)
            used = conn.execute("SELECT 1 FROM products WHERE category_id = ? LIMIT 1", (cid,)).fetchone()
            if used:
                flash("این دسته‌بندی دارای محصول است و قابل حذف نیست.", "danger")
            else:
                conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
                _audit("delete", "category", cid)
                flash("دسته‌بندی حذف شد.", "info")
            return redirect(url_for("admin.categories_manage"))

        cid = _to_int(request.form.get("category_id"), 0) or None
        name = (request.form.get("name") or "").strip()
        raw_slug = (request.form.get("slug") or "").strip() or name
        parent_id = _to_int(request.form.get("parent_id"), 0) or None
        if parent_id == cid:
            parent_id = None
        desc = (request.form.get("description") or "").strip()
        sort_order = _to_int(request.form.get("sort_order"), 0)
        is_active = 1 if request.form.get("is_active") == "1" else 0
        meta_title = (request.form.get("meta_title") or "").strip()
        meta_desc = (request.form.get("meta_description") or "").strip()
        canonical_url = (request.form.get("canonical_url") or "").strip()
        noindex = 1 if request.form.get("noindex") == "1" else 0

        if len(name) < 2:
            flash("نام دسته‌بندی الزامی است.", "danger")
            return redirect(url_for("admin.categories_manage"))

        img_path = None
        if cid:
            old_row = conn.execute("SELECT * FROM categories WHERE id = ?", (cid,)).fetchone()
            img_path = old_row["image_path"] if old_row else None
        else:
            old_row = None

        fs = request.files.get("image")
        if fs and fs.filename:
            try:
                res = save_uploaded_image(fs, subdir="categories", max_dim=800, thumb_dim=400)
                img_path = res["webp_path"]
            except MediaValidationError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("admin.categories_manage"))

        slug = ensure_unique_slug(conn, "categories", raw_slug, exclude_id=cid)
        ts = now_utc_iso()
        if cid:
            if old_row and old_row["slug"] != slug:
                create_or_update_redirect(conn, f"/category/{old_row['slug']}", f"/category/{slug}", 301)
            conn.execute(
                """
                UPDATE categories SET
                    parent_id = ?, name = ?, slug = ?, description = ?, image_path = ?,
                    sort_order = ?, is_active = ?, meta_title = ?, meta_description = ?,
                    canonical_url = ?, noindex = ?, updated_at = ?
                WHERE id = ?
                """,
                (parent_id, name, slug, desc, img_path, sort_order, is_active, meta_title, meta_desc, canonical_url, noindex, ts, cid),
            )
            _audit("update", "category", cid, after={"name": name, "slug": slug})
            flash("دسته‌بندی ویرایش شد.", "success")
        else:
            cur = conn.execute(
                """
                INSERT INTO categories (parent_id, name, slug, description, image_path, sort_order, is_active, meta_title, meta_description, canonical_url, noindex, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (parent_id, name, slug, desc, img_path, sort_order, is_active, meta_title, meta_desc, canonical_url, noindex, ts, ts),
            )
            _audit("create", "category", cur.lastrowid, after={"name": name, "slug": slug})
            flash("دسته‌بندی جدید ایجاد شد.", "success")
        return redirect(url_for("admin.categories_manage"))

    edit_id = _to_int(request.args.get("edit"), 0)
    edit_cat = conn.execute("SELECT * FROM categories WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    categories = list_categories(conn, active_only=False)
    return render_template("admin/categories.html", categories=categories, edit_cat=edit_cat)


# ==================== INVENTORY ====================
@admin_bp.route("/inventory", methods=["GET", "POST"])
@admin_required("inventory")
def inventory_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "adjust")
        if action == "adjust":
            vid = _to_int(request.form.get("variant_id"), 0)
            new_qty = _to_int(request.form.get("new_stock"), -1)
            reason_note = (request.form.get("note") or "").strip()
            if not reason_note:
                flash("وارد کردن دلیل تغییر موجودی الزامی است.", "danger")
                return redirect(url_for("admin.inventory_manage"))
            vrow = conn.execute("SELECT stock_quantity FROM product_variants WHERE id = ?", (vid,)).fetchone()
            if not vrow or new_qty < 0:
                flash("مقدار موجودی معتبر نیست.", "danger")
                return redirect(url_for("admin.inventory_manage"))
            delta = new_qty - int(vrow["stock_quantity"])
            if delta != 0:
                with transaction(conn, immediate=True):
                    adjust_variant_stock(
                        conn,
                        variant_id=vid,
                        delta=delta,
                        reason="manual_adjust",
                        admin_id=int(g.current_user["id"]),
                        note=reason_note,
                    )
                _audit("stock_adjust", "variant", vid, after={"delta": delta, "new_qty": new_qty, "note": reason_note})
                flash("موجودی با موفقیت به‌روزرسانی شد.", "success")
        elif action == "import_csv":
            fs = request.files.get("csv_file")
            if not fs or not fs.filename:
                flash("فایل CSV را انتخاب کنید.", "danger")
                return redirect(url_for("admin.inventory_manage"))
            text = fs.read().decode("utf-8-sig", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            updated = 0
            with transaction(conn, immediate=True):
                for row in reader:
                    sku = (row.get("sku") or row.get("SKU") or "").strip()
                    qty_raw = row.get("stock_quantity") or row.get("stock") or ""
                    if not sku or qty_raw == "":
                        continue
                    qty = max(0, _to_int(qty_raw, -1))
                    if qty < 0:
                        continue
                    v = conn.execute("SELECT id, stock_quantity FROM product_variants WHERE UPPER(sku) = UPPER(?)", (sku,)).fetchone()
                    if v:
                        delta = qty - int(v["stock_quantity"])
                        if delta != 0:
                            adjust_variant_stock(
                                conn,
                                variant_id=int(v["id"]),
                                delta=delta,
                                reason="csv_import",
                                admin_id=int(g.current_user["id"]),
                                note="به‌روزرسانی گروهی از فایل CSV",
                            )
                            updated += 1
            flash(f"موجودی {updated} تنوع از فایل CSV به‌روزرسانی شد.", "success")
        return redirect(url_for("admin.inventory_manage"))

    stock_filter = request.args.get("filter", "")
    q = (request.args.get("q") or "").strip()
    where = ["p.status != 'archived'"]
    params: list[Any] = []
    if stock_filter == "out":
        where.append("v.stock_quantity <= 0")
    elif stock_filter == "low":
        where.append("v.stock_quantity > 0 AND v.stock_quantity <= v.low_stock_threshold")
    if q:
        like = f"%{q}%"
        where.append("(p.title LIKE ? OR v.sku LIKE ? OR v.label LIKE ?)")
        params.extend([like, like, like])
    where_sql = "WHERE " + " AND ".join(where)

    variants = conn.execute(
        f"""
        SELECT v.*, p.title AS product_title, p.id AS product_id
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        {where_sql}
        ORDER BY v.stock_quantity ASC, p.title ASC
        """,
        params,
    ).fetchall()

    movements = conn.execute(
        """
        SELECT sm.*, v.sku, v.label AS variant_label, p.title AS product_title,
               u.full_name AS admin_name, o.order_number
        FROM stock_movements sm
        JOIN product_variants v ON v.id = sm.variant_id
        JOIN products p ON p.id = v.product_id
        LEFT JOIN users u ON u.id = sm.admin_id
        LEFT JOIN orders o ON o.id = sm.order_id
        ORDER BY sm.id DESC
        LIMIT 50
        """
    ).fetchall()

    return render_template(
        "admin/inventory.html",
        variants=variants,
        movements=movements,
        stock_filter=stock_filter,
        q=q,
    )


@admin_bp.route("/inventory/export.csv")
@admin_required("inventory")
def inventory_export_csv():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT v.sku, p.title, v.label, v.stock_quantity, v.low_stock_threshold, v.price_rial
        FROM product_variants v JOIN products p ON p.id = v.product_id
        WHERE p.status != 'archived'
        ORDER BY p.title, v.sort_order
        """
    ).fetchall()
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["sku", "product_title", "variant_label", "stock_quantity", "low_stock_threshold", "price_toman"])
    for r in rows:
        w.writerow([r["sku"], r["product_title"], r["variant_label"], r["stock_quantity"], r["low_stock_threshold"], r["price_rial"] // 10])
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="inventory.csv"'},
    )


# ==================== CUSTOMERS ====================
@admin_bp.route("/customers")
@admin_required("customers")
def customers_list():
    conn = get_db()
    q = (request.args.get("q") or "").strip()
    blocked = request.args.get("blocked", "")
    page = max(1, _to_int(request.args.get("page"), 1))
    rows, total = list_customers(conn, q=q, blocked=blocked, page=page, per_page=25)
    total_pages = max(1, math.ceil(total / 25))
    return render_template(
        "admin/customers_list.html",
        customers=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        q=q,
        blocked=blocked,
    )


@admin_bp.route("/customers/export.csv")
@admin_required("customers")
def customers_export_csv():
    conn = get_db()
    rows, _ = list_customers(conn, page=1, per_page=5000)
    buf = io.StringIO()
    buf.write("\ufeff")
    w = csv.writer(buf)
    w.writerow(["ID", "نام", "موبایل", "ایمیل", "تعداد سفارش", "مجموع خرید (تومان)", "وضعیت مسدودیت"])
    for c in rows:
        w.writerow([c["id"], c["full_name"], c["mobile"] or "", c["email"] or "", c["order_count"], c["total_spent_rial"] // 10, "مسدود" if c["is_blocked"] else "فعال"])
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="customers.csv"'},
    )


@admin_bp.route("/customers/<int:customer_id>", methods=["GET", "POST"])
@admin_required("customers")
def customer_detail(customer_id: int):
    conn = get_db()
    cust = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'customer'", (customer_id,)).fetchone()
    if not cust:
        abort(404)
    if request.method == "POST":
        action = request.form.get("action", "edit")
        if action == "toggle_block":
            new_b = 0 if cust["is_blocked"] else 1
            conn.execute("UPDATE users SET is_blocked = ?, updated_at = ? WHERE id = ?", (new_b, now_utc_iso(), customer_id))
            _audit("toggle_block", "customer", customer_id, after={"is_blocked": new_b})
            flash("وضعیت مسدودیت مشتری تغییر یافت.", "info")
        else:
            full_name = (request.form.get("full_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            mobile = normalize_iran_mobile(request.form.get("mobile"))
            conn.execute(
                "UPDATE users SET full_name = ?, email = ?, mobile = ?, updated_at = ? WHERE id = ?",
                (full_name, email or None, mobile or cust["mobile"], now_utc_iso(), customer_id),
            )
            _audit("update", "customer", customer_id, after={"full_name": full_name})
            flash("اطلاعات مشتری ذخیره شد.", "success")
        return redirect(url_for("admin.customer_detail", customer_id=customer_id))

    orders = conn.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC", (customer_id,)).fetchall()
    addresses = list_user_addresses(conn, customer_id)
    return render_template(
        "admin/customer_detail.html",
        customer=cust,
        orders=orders,
        addresses=addresses,
        status_labels=ORDER_STATUS_LABELS,
    )


# ==================== REVIEWS ====================
@admin_bp.route("/reviews", methods=["GET", "POST"])
@admin_required("reviews")
def reviews_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "bulk":
            bulk_act = request.form.get("bulk_action", "")
            rids = [_to_int(x, 0) for x in request.form.getlist("selected_ids[]") if _to_int(x, 0) > 0]
            if rids and bulk_act in {"approved", "rejected", "delete"}:
                affected_pids = set()
                for rid in rids:
                    r = conn.execute("SELECT product_id FROM reviews WHERE id = ?", (rid,)).fetchone()
                    if r:
                        affected_pids.add(int(r["product_id"]))
                ph = ",".join("?" for _ in rids)
                if bulk_act == "delete":
                    conn.execute(f"DELETE FROM reviews WHERE id IN ({ph})", rids)
                else:
                    conn.execute(f"UPDATE reviews SET status = ?, updated_at = ? WHERE id IN ({ph})", [bulk_act, now_utc_iso(), *rids])
                for pid in affected_pids:
                    recalculate_product_rating(conn, pid)
                flash("عملیات گروهی روی دیدگاه‌ها انجام شد.", "success")
        else:
            rid = _to_int(request.form.get("review_id"), 0)
            rev = conn.execute("SELECT * FROM reviews WHERE id = ?", (rid,)).fetchone()
            if rev:
                pid = int(rev["product_id"])
                if action in {"approved", "rejected"}:
                    conn.execute("UPDATE reviews SET status = ?, updated_at = ? WHERE id = ?", (action, now_utc_iso(), rid))
                elif action == "reply":
                    reply_txt = (request.form.get("admin_reply") or "").strip()
                    title_txt = (request.form.get("title") or rev["title"]).strip()
                    body_txt = (request.form.get("body") or rev["body"]).strip()
                    conn.execute(
                        "UPDATE reviews SET title = ?, body = ?, admin_reply = ?, admin_reply_at = ?, updated_at = ? WHERE id = ?",
                        (title_txt, body_txt, reply_txt, now_utc_iso() if reply_txt else None, now_utc_iso(), rid),
                    )
                elif action == "delete":
                    conn.execute("DELETE FROM reviews WHERE id = ?", (rid,))
                recalculate_product_rating(conn, pid)
                _audit(f"review_{action}", "review", rid)
                flash("تغییرات دیدگاه اعمال شد.", "success")
        return redirect(url_for("admin.reviews_manage"))

    status_filter = request.args.get("status", "")
    where_sql = "WHERE r.status = ?" if status_filter else ""
    params = [status_filter] if status_filter else []
    reviews = conn.execute(
        f"""
        SELECT r.*, p.title AS product_title, p.slug AS product_slug, u.full_name AS author_name, u.mobile AS author_mobile
        FROM reviews r
        JOIN products p ON p.id = r.product_id
        JOIN users u ON u.id = r.user_id
        {where_sql}
        ORDER BY r.status = 'pending' DESC, r.id DESC
        """,
        params,
    ).fetchall()
    return render_template("admin/reviews.html", reviews=reviews, status_filter=status_filter)


# ==================== COUPONS ====================
@admin_bp.route("/coupons", methods=["GET", "POST"])
@admin_required("coupons")
def coupons_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            cid = _to_int(request.form.get("coupon_id"), 0)
            conn.execute("DELETE FROM coupons WHERE id = ?", (cid,))
            _audit("delete", "coupon", cid)
            flash("کد تخفیف حذف شد.", "info")
            return redirect(url_for("admin.coupons_manage"))

        cid = _to_int(request.form.get("coupon_id"), 0) or None
        code = (request.form.get("code") or "").strip().upper()
        title = (request.form.get("title") or "").strip()
        d_type = request.form.get("discount_type", "percent")
        d_val_raw = _to_int(request.form.get("discount_value"), 0)
        d_val = d_val_raw if d_type == "percent" else d_val_raw * 10
        min_order_rial = max(0, _to_int(request.form.get("min_order_toman"), 0)) * 10
        max_disc_raw = (request.form.get("max_discount_toman") or "").strip()
        max_disc_rial = (max(0, _to_int(max_disc_raw, 0)) * 10) if max_disc_raw else None
        usage_limit = _to_int(request.form.get("usage_limit"), 0) or None
        per_user_limit = _to_int(request.form.get("per_user_limit"), 0) or None
        starts_at = jalali_input_to_utc_range(request.form.get("starts_at_jalali", ""), end_of_day=False) if request.form.get("starts_at_jalali") else None
        ends_at = jalali_input_to_utc_range(request.form.get("ends_at_jalali", ""), end_of_day=True) if request.form.get("ends_at_jalali") else None
        is_active = 1 if request.form.get("is_active") == "1" else 0

        if not code or d_val_raw <= 0:
            flash("کد تخفیف و مقدار تخفیف الزامی است.", "danger")
            return redirect(url_for("admin.coupons_manage"))

        ts = now_utc_iso()
        if cid:
            conn.execute(
                """
                UPDATE coupons SET
                    code = ?, title = ?, discount_type = ?, discount_value = ?,
                    min_order_rial = ?, max_discount_rial = ?, usage_limit = ?, per_user_limit = ?,
                    starts_at = ?, ends_at = ?, is_active = ?, updated_at = ?
                WHERE id = ?
                """,
                (code, title, d_type, d_val, min_order_rial, max_disc_rial, usage_limit, per_user_limit, starts_at, ends_at, is_active, ts, cid),
            )
            _audit("update", "coupon", cid, after={"code": code})
        else:
            cur = conn.execute(
                """
                INSERT INTO coupons (code, title, discount_type, discount_value, min_order_rial, max_discount_rial, usage_limit, per_user_limit, starts_at, ends_at, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (code, title, d_type, d_val, min_order_rial, max_disc_rial, usage_limit, per_user_limit, starts_at, ends_at, is_active, ts, ts),
            )
            _audit("create", "coupon", cur.lastrowid, after={"code": code})
        flash("کد تخفیف ذخیره شد.", "success")
        return redirect(url_for("admin.coupons_manage"))

    edit_id = _to_int(request.args.get("edit"), 0)
    edit_coupon = conn.execute("SELECT * FROM coupons WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    coupons = list_coupons(conn)
    return render_template("admin/coupons.html", coupons=coupons, edit_coupon=edit_coupon)


# ==================== HOMEPAGE BANNERS & COLLECTIONS ====================
@admin_bp.route("/homepage", methods=["GET", "POST"])
@admin_required("cms")
def homepage_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save_banner")
        ts = now_utc_iso()
        if action == "save_banner":
            bid = _to_int(request.form.get("banner_id"), 0) or None
            title = (request.form.get("title") or "").strip()
            subtitle = (request.form.get("subtitle") or "").strip()
            link_url = (request.form.get("link_url") or "").strip()
            button_text = (request.form.get("button_text") or "مشاهده").strip()
            placement = request.form.get("placement", "hero")
            sort_order = _to_int(request.form.get("sort_order"), 0)
            is_active = 1 if request.form.get("is_active") == "1" else 0
            starts_at = jalali_input_to_utc_range(request.form.get("starts_at_jalali", ""), end_of_day=False) if request.form.get("starts_at_jalali") else None
            ends_at = jalali_input_to_utc_range(request.form.get("ends_at_jalali", ""), end_of_day=True) if request.form.get("ends_at_jalali") else None

            img_path = ""
            if bid:
                old = conn.execute("SELECT image_path FROM banners WHERE id = ?", (bid,)).fetchone()
                if old:
                    img_path = old["image_path"]
            fs = request.files.get("image")
            if fs and fs.filename:
                try:
                    img_path = save_uploaded_image(fs, subdir="banners", max_dim=1600, thumb_dim=500)["webp_path"]
                except MediaValidationError as exc:
                    flash(str(exc), "danger")
                    return redirect(url_for("admin.homepage_manage"))
            if not img_path and not bid:
                flash("انتخاب تصویر بنر الزامی است.", "danger")
                return redirect(url_for("admin.homepage_manage"))

            if bid:
                conn.execute(
                    """
                    UPDATE banners SET title = ?, subtitle = ?, image_path = ?, link_url = ?, button_text = ?,
                        placement = ?, sort_order = ?, starts_at = ?, ends_at = ?, is_active = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (title, subtitle, img_path, link_url, button_text, placement, sort_order, starts_at, ends_at, is_active, ts, bid),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO banners (title, subtitle, image_path, link_url, button_text, placement, sort_order, starts_at, ends_at, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (title, subtitle, img_path, link_url, button_text, placement, sort_order, starts_at, ends_at, is_active, ts, ts),
                )
            flash("بنر ذخیره شد.", "success")
        elif action == "delete_banner":
            bid = _to_int(request.form.get("banner_id"), 0)
            old = conn.execute("SELECT image_path FROM banners WHERE id = ?", (bid,)).fetchone()
            conn.execute("DELETE FROM banners WHERE id = ?", (bid,))
            if old:
                delete_media_file(old["image_path"])
            flash("بنر حذف شد.", "info")
        elif action == "save_collection":
            title = (request.form.get("col_title") or "").strip()
            cat_id = _to_int(request.form.get("col_category_id"), 0) or None
            desc = (request.form.get("col_description") or "").strip()
            sort_order = _to_int(request.form.get("col_sort_order"), 0)
            slug = ensure_unique_slug(conn, "featured_collections", title)
            conn.execute(
                """
                INSERT INTO featured_collections (title, slug, description, category_id, sort_order, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (title, slug, desc, cat_id, sort_order, ts, ts),
            )
            flash("کالکشن ویژه اضافه شد.", "success")
        elif action == "delete_collection":
            col_id = _to_int(request.form.get("collection_id"), 0)
            conn.execute("DELETE FROM featured_collections WHERE id = ?", (col_id,))
            flash("کالکشن حذف شد.", "info")
        return redirect(url_for("admin.homepage_manage"))

    banners = list_all_banners(conn)
    collections = list_featured_collections(conn, active_only=False)
    categories = list_categories(conn, active_only=False)
    return render_template("admin/homepage.html", banners=banners, collections=collections, categories=categories)


# ==================== STATIC PAGES ====================
@admin_bp.route("/pages", methods=["GET", "POST"])
@admin_required("cms")
def pages_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            pid = _to_int(request.form.get("page_id"), 0)
            conn.execute("DELETE FROM pages WHERE id = ?", (pid,))
            flash("صفحه حذف شد.", "info")
            return redirect(url_for("admin.pages_manage"))

        pid = _to_int(request.form.get("page_id"), 0) or None
        title = (request.form.get("title") or "").strip()
        raw_slug = (request.form.get("slug") or "").strip() or title
        content_html = sanitize_html(request.form.get("content_html"))
        page_type = request.form.get("page_type", "standard")
        is_active = 1 if request.form.get("is_active") == "1" else 0
        meta_title = (request.form.get("meta_title") or "").strip()
        meta_desc = (request.form.get("meta_description") or "").strip()
        noindex = 1 if request.form.get("noindex") == "1" else 0

        fq_q = request.form.getlist("faq_q[]")
        fq_a = request.form.getlist("faq_a[]")
        faq_items = [{"q": q.strip(), "a": a.strip()} for q, a in zip(fq_q, fq_a) if q.strip() and a.strip()]
        faq_json = json.dumps(faq_items, ensure_ascii=False)

        slug = ensure_unique_slug(conn, "pages", raw_slug, exclude_id=pid)
        ts = now_utc_iso()
        if pid:
            old = conn.execute("SELECT slug FROM pages WHERE id = ?", (pid,)).fetchone()
            if old and old["slug"] != slug:
                create_or_update_redirect(conn, f"/page/{old['slug']}", f"/page/{slug}", 301)
            conn.execute(
                """
                UPDATE pages SET title = ?, slug = ?, content_html = ?, page_type = ?, faq_json = ?,
                    is_active = ?, meta_title = ?, meta_description = ?, noindex = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, slug, content_html, page_type, faq_json, is_active, meta_title, meta_desc, noindex, ts, pid),
            )
        else:
            conn.execute(
                """
                INSERT INTO pages (title, slug, content_html, page_type, faq_json, is_active, meta_title, meta_description, noindex, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, slug, content_html, page_type, faq_json, is_active, meta_title, meta_desc, noindex, ts, ts),
            )
        flash("صفحه با موفقیت ذخیره شد.", "success")
        return redirect(url_for("admin.pages_manage"))

    edit_id = _to_int(request.args.get("edit"), 0)
    edit_page = conn.execute("SELECT * FROM pages WHERE id = ?", (edit_id,)).fetchone() if edit_id else None
    faq_items = []
    if edit_page:
        try:
            faq_items = json.loads(edit_page["faq_json"] or "[]")
        except ValueError:
            faq_items = []
    pages = list_pages(conn, active_only=False)
    return render_template("admin/pages.html", pages=pages, edit_page=edit_page, faq_items=faq_items)


# ==================== REDIRECTS & BACK IN STOCK ====================
@admin_bp.route("/redirects", methods=["GET", "POST"])
@admin_required("redirects")
def redirects_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            rid = _to_int(request.form.get("redirect_id"), 0)
            conn.execute("DELETE FROM redirects WHERE id = ?", (rid,))
            flash("ریدایرکت حذف شد.", "info")
        else:
            old_p = "/" + (request.form.get("old_path") or "").strip().lstrip("/")
            new_p = (request.form.get("new_path") or "").strip()
            code = _to_int(request.form.get("status_code"), 301)
            if code not in {301, 302}:
                code = 301
            if old_p != "/" and new_p:
                create_or_update_redirect(conn, old_p, new_p, code)
                flash("ریدایرکت ذخیره شد.", "success")
        return redirect(url_for("admin.redirects_manage"))

    return render_template("admin/redirects.html", redirects=list_redirects(conn))


@admin_bp.route("/back-in-stock")
@admin_required("inventory")
def back_in_stock_list():
    conn = get_db()
    return render_template("admin/back_in_stock.html", rows=list_back_in_stock_requests(conn))


# ==================== SETTINGS, SHIPPING, PAYMENT, SMS ====================
@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required("settings")
def site_settings():
    conn = get_db()
    if request.method == "POST":
        keys = [
            "site_name", "site_tagline", "contact_phone", "contact_mobile", "contact_email",
            "contact_address", "support_hours", "instagram_url", "telegram_url", "whatsapp_url",
            "default_meta_title", "default_meta_description", "analytics_id", "robots_txt_extra",
            "trust_badge_html", "order_number_prefix", "low_stock_default_threshold",
            "reservation_timeout_minutes", "admin_alert_mobiles",
            "shipping_fee_toman", "free_shipping_threshold_toman",
        ]
        updates: dict[str, Any] = {}
        for k in keys:
            if k == "shipping_fee_toman":
                updates["shipping_fee_rial"] = max(0, _to_int(request.form.get(k), 0)) * 10
            elif k == "free_shipping_threshold_toman":
                updates["free_shipping_threshold_rial"] = max(0, _to_int(request.form.get(k), 0)) * 10
            elif k in request.form:
                updates[k] = request.form.get(k, "").strip()

        updates["shipping_enabled"] = "1" if request.form.get("shipping_enabled") == "1" else "0"
        updates["payment_bitpay_enabled"] = "1" if request.form.get("payment_bitpay_enabled") == "1" else "0"
        updates["payment_cod_enabled"] = "1" if request.form.get("payment_cod_enabled") == "1" else "0"
        updates["auto_approve_verified_reviews"] = "1" if request.form.get("auto_approve_verified_reviews") == "1" else "0"

        for file_key, setting_key, subdir in [
            ("site_logo", "site_logo_path", "site"),
            ("site_favicon", "site_favicon_path", "site"),
        ]:
            fs = request.files.get(file_key)
            if fs and fs.filename:
                try:
                    updates[setting_key] = save_uploaded_image(fs, subdir=subdir, max_dim=600, thumb_dim=128)["file_path"]
                except MediaValidationError as exc:
                    flash(str(exc), "danger")
                    return redirect(url_for("admin.site_settings"))

        set_settings(conn, updates)
        _audit("update_settings", "settings", "site")
        flash("تنظیمات فروشگاه ذخیره شد.", "success")
        return redirect(url_for("admin.site_settings"))

    return render_template("admin/settings.html", settings=get_all_settings(conn))


@admin_bp.route("/payments", methods=["GET", "POST"])
@admin_required("payments")
def payments_manage():
    conn = get_db()
    if request.method == "POST":
        set_settings(
            conn,
            {
                "bitpay_api_key": (request.form.get("bitpay_api_key") or "").strip(),
                "bitpay_test_mode": "1" if request.form.get("bitpay_test_mode") == "1" else "0",
            },
        )
        _audit("update_gateway", "settings", "bitpay")
        flash("تنظیمات درگاه پرداخت ذخیره شد.", "success")
        return redirect(url_for("admin.payments_manage"))

    status = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()
    page = max(1, _to_int(request.args.get("page"), 1))
    rows, total = list_payments_admin(conn, status=status, q=q, page=page, per_page=30)
    return render_template(
        "admin/payments.html",
        payments=rows,
        total=total,
        page=page,
        status=status,
        q=q,
        settings=get_all_settings(conn),
    )


@admin_bp.route("/sms", methods=["GET", "POST"])
@admin_required("sms")
def sms_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "test_send":
            mobile = normalize_iran_mobile(request.form.get("test_mobile"))
            msg = (request.form.get("test_message") or "پیامک آزمایشی فروشگاه آرمین").strip()
            if not mobile:
                flash("شماره موبایل آزمایشی معتبر نیست.", "danger")
            else:
                svc = SmsService.from_conn(conn)
                ok, msg_id, resp = svc.send_bulk(mobile=mobile, message_text=msg)
                conn.execute(
                    "INSERT INTO sms_logs (recipient, template_id, message_text, event_type, status, provider_message_id, provider_response, created_at) VALUES (?, NULL, ?, 'admin_test', ?, ?, ?, ?)",
                    (mobile, msg, "sent" if ok else "failed", msg_id, resp, now_utc_iso()),
                )
                flash("پیامک آزمایشی ارسال شد." if ok else f"خطا در ارسال پیامک: {resp}", "success" if ok else "danger")
        else:
            updates = {
                "smsir_api_key": (request.form.get("smsir_api_key") or "").strip(),
                "smsir_line_number": (request.form.get("smsir_line_number") or "").strip(),
                "smsir_otp_template_id": (request.form.get("smsir_otp_template_id") or "").strip(),
                "smsir_order_paid_template_id": (request.form.get("smsir_order_paid_template_id") or "").strip(),
                "smsir_order_status_template_id": (request.form.get("smsir_order_status_template_id") or "").strip(),
                "smsir_admin_order_template_id": (request.form.get("smsir_admin_order_template_id") or "").strip(),
                "smsir_low_stock_template_id": (request.form.get("smsir_low_stock_template_id") or "").strip(),
                "smsir_back_in_stock_template_id": (request.form.get("smsir_back_in_stock_template_id") or "").strip(),
                "sms_event_order_paid_customer": "1" if request.form.get("sms_event_order_paid_customer") == "1" else "0",
                "sms_event_order_status_customer": "1" if request.form.get("sms_event_order_status_customer") == "1" else "0",
                "sms_event_new_order_admin": "1" if request.form.get("sms_event_new_order_admin") == "1" else "0",
                "sms_event_low_stock_admin": "1" if request.form.get("sms_event_low_stock_admin") == "1" else "0",
                "sms_event_back_in_stock": "1" if request.form.get("sms_event_back_in_stock") == "1" else "0",
            }
            set_settings(conn, updates)
            _audit("update_sms", "settings", "smsir")
            flash("تنظیمات پیامک ذخیره شد.", "success")
        return redirect(url_for("admin.sms_manage"))

    logs = conn.execute("SELECT * FROM sms_logs ORDER BY id DESC LIMIT 60").fetchall()
    return render_template("admin/sms.html", settings=get_all_settings(conn), logs=logs)


# ==================== ADMIN USERS & ROLES + AUDIT LOG ====================
@admin_bp.route("/users", methods=["GET", "POST"])
@admin_required("users_roles")
def admin_users_manage():
    conn = get_db()
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            username = (request.form.get("username") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            mobile = normalize_iran_mobile(request.form.get("mobile"))
            full_name = (request.form.get("full_name") or "").strip()
            role = request.form.get("role", "order_operator")
            password = request.form.get("password") or ""
            otp_2fa = 1 if request.form.get("otp_2fa_enabled") == "1" else 0

            if role not in ADMIN_ROLES:
                role = "order_operator"
            ok_pw, msg_pw = validate_password_policy(password)
            if not username or not email or not ok_pw:
                flash(msg_pw if not ok_pw else "نام کاربری و ایمیل الزامی است.", "danger")
                return redirect(url_for("admin.admin_users_manage"))

            ts = now_utc_iso()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO users (username, email, mobile, password_hash, full_name, role, is_active, otp_2fa_enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (username, email, mobile, hash_password(password), full_name or username, role, otp_2fa, ts, ts),
                )
                _audit("create_admin_user", "user", cur.lastrowid, after={"username": username, "role": role})
                flash("کاربر مدیر ایجاد شد.", "success")
            except Exception:
                flash("نام کاربری، ایمیل یا موبایل تکراری است.", "danger")
        elif action == "update_role":
            uid = _to_int(request.form.get("user_id"), 0)
            role = request.form.get("role", "")
            is_active = 1 if request.form.get("is_active") == "1" else 0
            otp_2fa = 1 if request.form.get("otp_2fa_enabled") == "1" else 0
            if uid == int(g.current_user["id"]) and (role != "super_admin" or not is_active):
                flash("نمی‌توانید نقش یا وضعیت فعال بودن حساب خودتان را تنزل دهید.", "danger")
                return redirect(url_for("admin.admin_users_manage"))
            if role in ADMIN_ROLES:
                conn.execute(
                    "UPDATE users SET role = ?, is_active = ?, otp_2fa_enabled = ?, updated_at = ? WHERE id = ?",
                    (role, is_active, otp_2fa, now_utc_iso(), uid),
                )
                _audit("update_admin_role", "user", uid, after={"role": role, "is_active": is_active})
                flash("دسترسی کاربر به‌روزرسانی شد.", "success")
        return redirect(url_for("admin.admin_users_manage"))

    admins = conn.execute(
        "SELECT * FROM users WHERE role IN ('super_admin', 'manager', 'order_operator', 'content_editor') ORDER BY id ASC"
    ).fetchall()
    return render_template("admin/users.html", admins=admins, role_labels=ROLE_LABELS)


@admin_bp.route("/audit-logs")
@admin_required("audit")
def audit_logs_view():
    conn = get_db()
    entity_type = (request.args.get("entity_type") or "").strip()
    action = (request.args.get("action") or "").strip()
    page = max(1, _to_int(request.args.get("page"), 1))
    rows, total = list_audit_logs(conn, entity_type=entity_type, action=action, page=page, per_page=30)
    total_pages = max(1, math.ceil(total / 30))
    return render_template(
        "admin/audit_logs.html",
        logs=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        entity_type=entity_type,
        action=action,
    )


# ==================== REPORTS ====================
@admin_bp.route("/reports")
@admin_required("reports")
def reports_view():
    conn = get_db()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    where = ["o.payment_status = 'paid'"]
    params: list[Any] = []
    if date_from:
        uf = jalali_input_to_utc_range(date_from, end_of_day=False)
        if uf:
            where.append("o.created_at >= ?")
            params.append(uf)
    if date_to:
        ut = jalali_input_to_utc_range(date_to, end_of_day=True)
        if ut:
            where.append("o.created_at <= ?")
            params.append(ut)
    where_sql = "WHERE " + " AND ".join(where)

    summary = conn.execute(
        f"""
        SELECT COUNT(*) AS order_count,
               COALESCE(SUM(o.subtotal_rial), 0) AS subtotal_rial,
               COALESCE(SUM(o.shipping_fee_rial), 0) AS shipping_rial,
               COALESCE(SUM(o.discount_rial), 0) AS discount_rial,
               COALESCE(SUM(o.total_rial), 0) AS total_rial
        FROM orders o {where_sql}
        """,
        params,
    ).fetchone()

    by_product = conn.execute(
        f"""
        SELECT oi.product_title, oi.sku, SUM(oi.quantity) AS qty_sold, SUM(oi.line_total_rial) AS revenue_rial
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        {where_sql}
        GROUP BY oi.product_id, oi.sku
        ORDER BY revenue_rial DESC
        LIMIT 30
        """,
        params,
    ).fetchall()

    by_category = conn.execute(
        f"""
        SELECT c.name AS category_name, SUM(oi.quantity) AS qty_sold, SUM(oi.line_total_rial) AS revenue_rial
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN products p ON p.id = oi.product_id
        JOIN categories c ON c.id = p.category_id
        {where_sql}
        GROUP BY c.id
        ORDER BY revenue_rial DESC
        """,
        params,
    ).fetchall()

    by_coupon = conn.execute(
        """
        SELECT c.code, c.title, COUNT(cu.id) AS uses, COALESCE(SUM(cu.discount_rial), 0) AS total_discount_rial
        FROM coupons c
        LEFT JOIN coupon_usages cu ON cu.coupon_id = c.id
        GROUP BY c.id
        ORDER BY uses DESC
        """
    ).fetchall()

    if request.args.get("export") == "csv":
        buf = io.StringIO()
        buf.write("\ufeff")
        w = csv.writer(buf)
        w.writerow(["محصول", "SKU", "تعداد فروش", "درآمد (تومان)"])
        for r in by_product:
            w.writerow([r["product_title"], r["sku"], r["qty_sold"], r["revenue_rial"] // 10])
        return Response(
            buf.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="sales-report.csv"'},
        )

    return render_template(
        "admin/reports.html",
        summary=summary,
        by_product=by_product,
        by_category=by_category,
        by_coupon=by_coupon,
        date_from=date_from,
        date_to=date_to,
    )
