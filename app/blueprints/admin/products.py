from __future__ import annotations

import csv
import io
import math
import secrets
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
from app.repositories.audit import log_audit
from app.repositories.catalog import (
    ensure_unique_slug,
    get_product_detail_by_id,
    list_categories,
    list_products_admin,
    sync_product_tags,
)
from app.repositories.cms import create_or_update_redirect
from app.services.auth import admin_required
from app.services.inventory import adjust_variant_stock
from app.services.media import (
    MediaValidationError,
    delete_media_file,
    save_uploaded_image,
    save_uploaded_video,
)
from app.utils.persian import now_utc_iso, sanitize_html, to_english_digits


def _to_int(val: Any, default: int = 0) -> int:
    try:
        s = to_english_digits(str(val or "")).replace(",", "").strip()
        return int(s) if s != "" else default
    except ValueError:
        return default


def _parse_variants_from_form() -> tuple[list[dict[str, Any]], list[str]]:
    ids = request.form.getlist("variant_id[]")
    labels = request.form.getlist("variant_label[]")
    skus = request.form.getlist("variant_sku[]")
    prices_toman = request.form.getlist("variant_price_toman[]")
    compares_toman = request.form.getlist("variant_compare_toman[]")
    weights = request.form.getlist("variant_weight[]")
    stocks = request.form.getlist("variant_stock[]")
    thresholds = request.form.getlist("variant_threshold[]")
    actives = request.form.getlist("variant_active[]")

    variants: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_skus: set[str] = set()

    count = max(len(labels), len(skus), len(prices_toman))
    for i in range(count):
        lbl = (labels[i] if i < len(labels) else "").strip()
        sku = (skus[i] if i < len(skus) else "").strip().upper()
        p_toman = _to_int(prices_toman[i] if i < len(prices_toman) else 0, -1)
        c_raw = (compares_toman[i] if i < len(compares_toman) else "").strip()
        c_toman = _to_int(c_raw, 0) if c_raw else None
        weight = max(0, _to_int(weights[i] if i < len(weights) else 0, 0))
        stock = _to_int(stocks[i] if i < len(stocks) else 0, 0)
        thresh = max(0, _to_int(thresholds[i] if i < len(thresholds) else 5, 5))
        vid = _to_int(ids[i] if i < len(ids) else 0, 0) or None
        active_val = actives[i] if i < len(actives) else "1"
        is_active = 1 if str(active_val) == "1" else 0

        if not lbl and not sku:
            continue
        if not lbl:
            errors.append(f"عنوان تنوع ردیف {i + 1} الزامی است.")
        if not sku:
            errors.append(f"کد SKU تنوع ردیف {i + 1} الزامی است.")
        elif sku in seen_skus:
            errors.append(f"کد SKU «{sku}» در فرم تکراری است.")
        seen_skus.add(sku)
        if p_toman < 0:
            errors.append(f"قیمت تنوع ردیف {i + 1} معتبر نیست.")
        if stock < 0:
            errors.append(f"موجودی تنوع ردیف {i + 1} نمی‌تواند منفی باشد.")

        variants.append(
            {
                "id": vid,
                "label": lbl,
                "sku": sku,
                "price_rial": max(0, p_toman) * 10,
                "compare_at_price_rial": (c_toman * 10) if (c_toman and c_toman > 0) else None,
                "weight_grams": weight,
                "stock_quantity": max(0, stock),
                "low_stock_threshold": thresh,
                "is_active": is_active,
                "sort_order": i,
            }
        )

    if not variants:
        errors.append("حداقل یک تنوع (وزن/بسته‌بندی) با قیمت و SKU باید تعریف شود.")
    return variants, errors


@admin_bp.route("/products")
@admin_required("products")
def products_list():
    conn = get_db()
    q = (request.args.get("q") or "").strip()
    category_id = _to_int(request.args.get("category_id"), 0) or None
    status = (request.args.get("status") or "").strip()
    stock_state = (request.args.get("stock_state") or "").strip()
    sort = (request.args.get("sort") or "updated_desc").strip()
    page = max(1, _to_int(request.args.get("page"), 1))
    per_page = 25

    items, total = list_products_admin(
        conn,
        q=q,
        category_id=category_id,
        status=status,
        stock_state=stock_state,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    categories = list_categories(conn, active_only=False)
    total_pages = max(1, math.ceil(total / per_page))
    return render_template(
        "admin/products_list.html",
        items=items,
        total=total,
        page=page,
        total_pages=total_pages,
        categories=categories,
        q=q,
        category_id=category_id,
        status=status,
        stock_state=stock_state,
        sort=sort,
    )


@admin_bp.route("/products/export.csv")
@admin_required("products")
def products_export_csv():
    conn = get_db()
    items, _ = list_products_admin(
        conn,
        q=(request.args.get("q") or "").strip(),
        category_id=_to_int(request.args.get("category_id"), 0) or None,
        status=(request.args.get("status") or "").strip(),
        stock_state=(request.args.get("stock_state") or "").strip(),
        page=1,
        per_page=5000,
    )
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["ID", "عنوان", "اسلاگ", "دسته‌بندی", "SKU", "حداقل قیمت (تومان)", "موجودی کل", "وضعیت", "فروش کل"])
    for it in items:
        writer.writerow([
            it["id"],
            it["title"],
            it["slug"],
            it["category_name"],
            it["skus"],
            it["min_price_rial"] // 10,
            it["total_stock"],
            it["status"],
            it["total_sold"],
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="products.csv"'},
    )


@admin_bp.route("/products/bulk", methods=["POST"])
@admin_required("products")
def products_bulk():
    conn = get_db()
    action = request.form.get("bulk_action", "")
    raw_ids = request.form.getlist("selected_ids[]")
    pids = [_to_int(x, 0) for x in raw_ids if _to_int(x, 0) > 0]
    if not pids or not action:
        flash("موردی انتخاب نشده است.", "warning")
        return redirect(url_for("admin.products_list"))

    ts = now_utc_iso()
    count = 0
    with transaction(conn, immediate=True):
        if action in {"published", "draft", "archived"}:
            ph = ",".join("?" for _ in pids)
            conn.execute(
                f"UPDATE products SET status = ?, updated_at = ? WHERE id IN ({ph})",
                [action, ts, *pids],
            )
            count = len(pids)
        elif action == "change_category":
            new_cat = _to_int(request.form.get("bulk_category_id"), 0)
            if new_cat > 0:
                ph = ",".join("?" for _ in pids)
                conn.execute(
                    f"UPDATE products SET category_id = ?, updated_at = ? WHERE id IN ({ph})",
                    [new_cat, ts, *pids],
                )
                count = len(pids)
        elif action == "delete":
            for pid in pids:
                _delete_or_archive_product(conn, pid)
                count += 1
        log_audit(
            conn,
            admin_id=int(g.current_user["id"]),
            admin_name=g.current_user.get("username") or g.current_user["full_name"],
            action=f"bulk_{action}",
            entity_type="product",
            entity_id=",".join(map(str, pids)),
            after={"count": count},
            ip_address=request.remote_addr or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
    flash(f"عملیات گروهی روی {count} محصول انجام شد.", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/new", methods=["GET", "POST"])
@admin_required("products")
def product_create():
    return _product_form(product_id=None)


@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required("products")
def product_edit(product_id: int):
    return _product_form(product_id=product_id)


def _product_form(product_id: int | None):
    conn = get_db()
    categories = list_categories(conn, active_only=False)
    other_products = conn.execute(
        "SELECT id, title, slug FROM products WHERE id != ? AND status != 'archived' ORDER BY title",
        (product_id or 0,),
    ).fetchall()

    existing = get_product_detail_by_id(conn, product_id) if product_id else None
    if product_id and not existing:
        abort(404)

    stock_history = []
    if product_id:
        stock_history = conn.execute(
            """
            SELECT sm.*, v.label AS variant_label, v.sku, u.full_name AS admin_name
            FROM stock_movements sm
            JOIN product_variants v ON v.id = sm.variant_id
            LEFT JOIN users u ON u.id = sm.admin_id
            WHERE v.product_id = ?
            ORDER BY sm.id DESC
            LIMIT 30
            """,
            (product_id,),
        ).fetchall()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        raw_slug = (request.form.get("slug") or "").strip() or title
        category_id = _to_int(request.form.get("category_id"), 0)
        short_desc = (request.form.get("short_description") or "").strip()
        desc_html = sanitize_html(request.form.get("description_html"))
        brand_origin = (request.form.get("brand_origin") or "").strip()
        status = request.form.get("status", "draft")
        if status not in {"draft", "published", "archived"}:
            status = "draft"
        is_featured = 1 if request.form.get("is_featured") == "1" else 0
        tags_raw = request.form.get("tags", "")
        tag_list = [t.strip() for t in tags_raw.replace("،", ",").split(",") if t.strip()]

        meta_title = (request.form.get("meta_title") or "").strip()
        meta_description = (request.form.get("meta_description") or "").strip()
        canonical_url = (request.form.get("canonical_url") or "").strip()
        noindex = 1 if request.form.get("noindex") == "1" else 0

        variants_data, errors = _parse_variants_from_form()
        if len(title) < 2:
            errors.insert(0, "عنوان محصول باید حداقل ۲ کاراکتر باشد.")
        if category_id <= 0:
            errors.insert(0, "انتخاب دسته‌بندی الزامی است.")

        # Validate SKU uniqueness across other products in DB
        for vd in variants_data:
            dup = conn.execute(
                "SELECT id, product_id FROM product_variants WHERE UPPER(sku) = UPPER(?)",
                (vd["sku"],),
            ).fetchone()
            if dup and (not vd["id"] or int(dup["id"]) != int(vd["id"])):
                errors.append(f"کد SKU «{vd['sku']}» قبلاً برای تنوع دیگری ثبت شده است.")

        # Specs
        spec_keys = request.form.getlist("spec_key[]")
        spec_vals = request.form.getlist("spec_value[]")
        specs_data = []
        for idx, (sk, sv) in enumerate(zip(spec_keys, spec_vals)):
            if sk.strip() and sv.strip():
                specs_data.append((sk.strip(), sv.strip(), idx))

        # Related products
        rel_ids = [_to_int(x, 0) for x in request.form.getlist("related_ids[]") if _to_int(x, 0) > 0]

        # Pre-validate uploads before opening DB transaction
        uploaded_images: list[dict[str, Any]] = []
        for fs in request.files.getlist("new_images"):
            if fs and fs.filename:
                try:
                    uploaded_images.append(save_uploaded_image(fs, subdir="products"))
                except MediaValidationError as exc:
                    errors.append(f"تصویر ({fs.filename}): {exc}")

        new_og_path = existing["og_image_path"] if existing else None
        og_fs = request.files.get("og_image")
        if og_fs and og_fs.filename:
            try:
                og_res = save_uploaded_image(og_fs, subdir="seo", max_dim=1200, thumb_dim=400)
                new_og_path = og_res["file_path"]
            except MediaValidationError as exc:
                errors.append(f"تصویر OG: {exc}")

        # New video upload or embed
        new_video_payload = None
        v_type = request.form.get("new_video_source_type", "").strip()
        v_title = (request.form.get("new_video_title") or "").strip()
        v_desc = (request.form.get("new_video_description") or "").strip()
        v_embed = (request.form.get("new_video_embed_url") or "").strip()
        v_file = request.files.get("new_video_file")
        v_poster = request.files.get("new_video_poster")

        if v_type == "embed" and v_embed:
            if not (v_embed.startswith("https://") or v_embed.startswith("http://")):
                errors.append("لینک ویدیو باید با http یا https شروع شود.")
            else:
                poster_rel = None
                if v_poster and v_poster.filename:
                    try:
                        poster_rel = save_uploaded_image(v_poster, subdir="video_posters")["file_path"]
                    except MediaValidationError as exc:
                        errors.append(f"پوستر ویدیو: {exc}")
                new_video_payload = {
                    "source_type": "embed",
                    "embed_url": v_embed,
                    "file_path": None,
                    "poster_path": poster_rel,
                    "title": v_title,
                    "description": v_desc,
                }
        elif v_type == "upload" and v_file and v_file.filename:
            try:
                vid_path = save_uploaded_video(v_file, subdir="videos")
                poster_rel = None
                if v_poster and v_poster.filename:
                    poster_rel = save_uploaded_image(v_poster, subdir="video_posters")["file_path"]
                new_video_payload = {
                    "source_type": "upload",
                    "embed_url": None,
                    "file_path": vid_path,
                    "poster_path": poster_rel,
                    "title": v_title,
                    "description": v_desc,
                }
            except MediaValidationError as exc:
                errors.append(f"فایل ویدیو: {exc}")

        if errors:
            for e in errors:
                flash(e, "danger")
            draft = dict(existing or {})
            draft.update(
                {
                    "title": title,
                    "slug": raw_slug,
                    "category_id": category_id,
                    "short_description": short_desc,
                    "description_html": request.form.get("description_html", ""),
                    "brand_origin": brand_origin,
                    "status": status,
                    "is_featured": is_featured,
                    "meta_title": meta_title,
                    "meta_description": meta_description,
                    "canonical_url": canonical_url,
                    "noindex": noindex,
                    "variants": variants_data,
                    "specs": [{"spec_key": k, "spec_value": v} for k, v, _ in specs_data],
                    "tags": [{"name": t} for t in tag_list],
                    "related_products": [{"id": rid} for rid in rel_ids],
                }
            )
            return render_template(
                "admin/product_form.html",
                product=draft,
                is_edit=bool(product_id),
                categories=categories,
                other_products=other_products,
                stock_history=stock_history,
            )

        ts = now_utc_iso()
        with transaction(conn, immediate=True):
            slug = ensure_unique_slug(conn, "products", raw_slug, exclude_id=product_id)

            if product_id:
                old_slug = existing["slug"]
                conn.execute(
                    """
                    UPDATE products SET
                        category_id = ?, title = ?, slug = ?, short_description = ?, description_html = ?,
                        brand_origin = ?, status = ?, is_featured = ?,
                        meta_title = ?, meta_description = ?, canonical_url = ?, noindex = ?, og_image_path = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        category_id,
                        title,
                        slug,
                        short_desc,
                        desc_html,
                        brand_origin,
                        status,
                        is_featured,
                        meta_title,
                        meta_description,
                        canonical_url,
                        noindex,
                        new_og_path,
                        ts,
                        product_id,
                    ),
                )
                if old_slug != slug:
                    create_or_update_redirect(
                        conn, f"/product/{old_slug}", f"/product/{slug}", 301
                    )
                pid = product_id
            else:
                cur = conn.execute(
                    """
                    INSERT INTO products (
                        category_id, title, slug, short_description, description_html,
                        brand_origin, status, is_featured,
                        meta_title, meta_description, canonical_url, noindex, og_image_path,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category_id,
                        title,
                        slug,
                        short_desc,
                        desc_html,
                        brand_origin,
                        status,
                        is_featured,
                        meta_title,
                        meta_description,
                        canonical_url,
                        noindex,
                        new_og_path,
                        ts,
                        ts,
                    ),
                )
                pid = int(cur.lastrowid)

            # Sync variants safely (preserve referenced variants, adjust stock via stock_movements)
            existing_variants = {
                int(v["id"]): v
                for v in conn.execute(
                    "SELECT * FROM product_variants WHERE product_id = ?", (pid,)
                ).fetchall()
            }
            kept_ids: set[int] = set()
            for vd in variants_data:
                vid = vd["id"]
                if vid and vid in existing_variants:
                    kept_ids.add(vid)
                    old_v = existing_variants[vid]
                    conn.execute(
                        """
                        UPDATE product_variants SET
                            label = ?, sku = ?, price_rial = ?, compare_at_price_rial = ?,
                            weight_grams = ?, low_stock_threshold = ?, is_active = ?, sort_order = ?, updated_at = ?
                        WHERE id = ? AND product_id = ?
                        """,
                        (
                            vd["label"],
                            vd["sku"],
                            vd["price_rial"],
                            vd["compare_at_price_rial"],
                            vd["weight_grams"],
                            vd["low_stock_threshold"],
                            vd["is_active"],
                            vd["sort_order"],
                            ts,
                            vid,
                            pid,
                        ),
                    )
                    delta = int(vd["stock_quantity"]) - int(old_v["stock_quantity"])
                    if delta != 0:
                        adjust_variant_stock(
                            conn,
                            variant_id=vid,
                            delta=delta,
                            reason="admin_product_edit",
                            admin_id=int(g.current_user["id"]),
                            note="تغییر موجودی در فرم ویرایش محصول",
                        )
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO product_variants (
                            product_id, label, sku, price_rial, compare_at_price_rial,
                            weight_grams, stock_quantity, low_stock_threshold, is_active, sort_order, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                        """,
                        (
                            pid,
                            vd["label"],
                            vd["sku"],
                            vd["price_rial"],
                            vd["compare_at_price_rial"],
                            vd["weight_grams"],
                            vd["low_stock_threshold"],
                            vd["is_active"],
                            vd["sort_order"],
                            ts,
                            ts,
                        ),
                    )
                    new_vid = int(cur.lastrowid)
                    kept_ids.add(new_vid)
                    if vd["stock_quantity"] > 0:
                        adjust_variant_stock(
                            conn,
                            variant_id=new_vid,
                            delta=int(vd["stock_quantity"]),
                            reason="initial_stock",
                            admin_id=int(g.current_user["id"]),
                            note="موجودی اولیه تنوع جدید",
                        )

            for old_vid in existing_variants:
                if old_vid not in kept_ids:
                    in_orders = conn.execute(
                        "SELECT 1 FROM order_items WHERE variant_id = ? LIMIT 1", (old_vid,)
                    ).fetchone()
                    if in_orders:
                        conn.execute(
                            "UPDATE product_variants SET is_active = 0, updated_at = ? WHERE id = ?",
                            (ts, old_vid),
                        )
                    else:
                        conn.execute("DELETE FROM product_variants WHERE id = ?", (old_vid,))

            # Existing images metadata / deletion / cover selection
            cover_id = _to_int(request.form.get("cover_image_id"), 0)
            del_img_ids = {_to_int(x, 0) for x in request.form.getlist("delete_image_ids[]")}
            for img_row in conn.execute(
                "SELECT * FROM product_images WHERE product_id = ?", (pid,)
            ).fetchall():
                iid = int(img_row["id"])
                if iid in del_img_ids:
                    conn.execute("DELETE FROM product_images WHERE id = ?", (iid,))
                    delete_media_file(img_row["file_path"])
                    delete_media_file(img_row["thumb_path"])
                    delete_media_file(img_row["webp_path"])
                else:
                    alt = (request.form.get(f"image_alt_{iid}") or "").strip()
                    sord = _to_int(request.form.get(f"image_sort_{iid}"), 0)
                    is_cov = 1 if iid == cover_id else 0
                    conn.execute(
                        "UPDATE product_images SET alt_text = ?, sort_order = ?, is_cover = ? WHERE id = ?",
                        (alt, sord, is_cov, iid),
                    )

            existing_img_count = conn.execute(
                "SELECT COUNT(*) FROM product_images WHERE product_id = ?", (pid,)
            ).fetchone()[0]
            for idx, im in enumerate(uploaded_images):
                is_cov = 1 if (existing_img_count == 0 and idx == 0 and cover_id == 0) else 0
                conn.execute(
                    """
                    INSERT INTO product_images (product_id, file_path, thumb_path, webp_path, alt_text, is_cover, sort_order, width, height, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        im["file_path"],
                        im["thumb_path"],
                        im["webp_path"],
                        title,
                        is_cov,
                        existing_img_count + idx,
                        im["width"],
                        im["height"],
                        ts,
                    ),
                )

            # Ensure at least one cover image if images exist
            has_cov = conn.execute(
                "SELECT 1 FROM product_images WHERE product_id = ? AND is_cover = 1", (pid,)
            ).fetchone()
            if not has_cov:
                first_im = conn.execute(
                    "SELECT id FROM product_images WHERE product_id = ? ORDER BY sort_order ASC, id ASC LIMIT 1",
                    (pid,),
                ).fetchone()
                if first_im:
                    conn.execute(
                        "UPDATE product_images SET is_cover = 1 WHERE id = ?", (first_im["id"],)
                    )

            # Existing videos update/delete + new video insert
            del_vid_ids = {_to_int(x, 0) for x in request.form.getlist("delete_video_ids[]")}
            for vrow in conn.execute(
                "SELECT * FROM product_videos WHERE product_id = ?", (pid,)
            ).fetchall():
                vid_id = int(vrow["id"])
                if vid_id in del_vid_ids:
                    conn.execute("DELETE FROM product_videos WHERE id = ?", (vid_id,))
                    delete_media_file(vrow["file_path"])
                    delete_media_file(vrow["poster_path"])
                else:
                    vt = (request.form.get(f"video_title_{vid_id}") or "").strip()
                    vs = _to_int(request.form.get(f"video_sort_{vid_id}"), 0)
                    conn.execute(
                        "UPDATE product_videos SET title = ?, sort_order = ? WHERE id = ?",
                        (vt, vs, vid_id),
                    )
            if new_video_payload:
                v_cnt = conn.execute(
                    "SELECT COUNT(*) FROM product_videos WHERE product_id = ?", (pid,)
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO product_videos (product_id, source_type, file_path, embed_url, poster_path, title, description, sort_order, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pid,
                        new_video_payload["source_type"],
                        new_video_payload["file_path"],
                        new_video_payload["embed_url"],
                        new_video_payload["poster_path"],
                        new_video_payload["title"] or title,
                        new_video_payload["description"],
                        v_cnt,
                        ts,
                    ),
                )

            # Specs
            conn.execute("DELETE FROM product_specs WHERE product_id = ?", (pid,))
            for sk, sv, idx in specs_data:
                conn.execute(
                    "INSERT INTO product_specs (product_id, spec_key, spec_value, sort_order) VALUES (?, ?, ?, ?)",
                    (pid, sk, sv, idx),
                )

            # Tags
            sync_product_tags(conn, pid, tag_list)

            # Related products
            conn.execute("DELETE FROM related_products WHERE product_id = ?", (pid,))
            for idx, rid in enumerate(rel_ids):
                if rid != pid:
                    conn.execute(
                        "INSERT OR IGNORE INTO related_products (product_id, related_product_id, sort_order) VALUES (?, ?, ?)",
                        (pid, rid, idx),
                    )

            log_audit(
                conn,
                admin_id=int(g.current_user["id"]),
                admin_name=g.current_user.get("username") or g.current_user["full_name"],
                action="update" if product_id else "create",
                entity_type="product",
                entity_id=pid,
                before={"title": existing["title"], "slug": existing["slug"]} if existing else None,
                after={"title": title, "slug": slug, "status": status},
                ip_address=request.remote_addr or "",
                user_agent=request.headers.get("User-Agent", ""),
            )

        flash("محصول با موفقیت ذخیره شد.", "success")
        return redirect(url_for("admin.product_edit", product_id=pid))

    return render_template(
        "admin/product_form.html",
        product=existing,
        is_edit=bool(product_id),
        categories=categories,
        other_products=other_products,
        stock_history=stock_history,
    )


def _delete_or_archive_product(conn, product_id: int) -> str:
    in_orders = conn.execute(
        "SELECT 1 FROM order_items WHERE product_id = ? LIMIT 1", (product_id,)
    ).fetchone()
    if in_orders:
        conn.execute(
            "UPDATE products SET status = 'archived', updated_at = ? WHERE id = ?",
            (now_utc_iso(), product_id),
        )
        return "archived"

    imgs = conn.execute(
        "SELECT file_path, thumb_path, webp_path FROM product_images WHERE product_id = ?",
        (product_id,),
    ).fetchall()
    vids = conn.execute(
        "SELECT file_path, poster_path FROM product_videos WHERE product_id = ?",
        (product_id,),
    ).fetchall()
    prod = conn.execute("SELECT og_image_path FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    for im in imgs:
        delete_media_file(im["file_path"])
        delete_media_file(im["thumb_path"])
        delete_media_file(im["webp_path"])
    for vd in vids:
        delete_media_file(vd["file_path"])
        delete_media_file(vd["poster_path"])
    if prod and prod["og_image_path"]:
        delete_media_file(prod["og_image_path"])
    return "deleted"


@admin_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@admin_required("products")
def product_delete(product_id: int):
    conn = get_db()
    prod = conn.execute("SELECT id, title FROM products WHERE id = ?", (product_id,)).fetchone()
    if not prod:
        abort(404)
    with transaction(conn, immediate=True):
        mode = _delete_or_archive_product(conn, product_id)
        log_audit(
            conn,
            admin_id=int(g.current_user["id"]),
            admin_name=g.current_user.get("username") or g.current_user["full_name"],
            action=mode,
            entity_type="product",
            entity_id=product_id,
            before={"title": prod["title"]},
            ip_address=request.remote_addr or "",
            user_agent=request.headers.get("User-Agent", ""),
        )
    if mode == "archived":
        flash("این محصول در سفارش‌ها استفاده شده بود؛ بنابراین به حالت «بایگانی» تغییر یافت.", "info")
    else:
        flash("محصول و فایل‌های رسانه آن به طور کامل حذف شدند.", "success")
    return redirect(url_for("admin.products_list"))


@admin_bp.route("/products/<int:product_id>/duplicate", methods=["POST"])
@admin_required("products")
def product_duplicate(product_id: int):
    conn = get_db()
    src = get_product_detail_by_id(conn, product_id)
    if not src:
        abort(404)
    ts = now_utc_iso()
    with transaction(conn, immediate=True):
        new_title = f"{src['title']} (کپی)"
        new_slug = ensure_unique_slug(conn, "products", f"{src['slug']}-copy")
        cur = conn.execute(
            """
            INSERT INTO products (
                category_id, title, slug, short_description, description_html,
                brand_origin, status, is_featured,
                meta_title, meta_description, canonical_url, noindex, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'draft', 0, ?, ?, '', ?, ?, ?)
            """,
            (
                src["category_id"],
                new_title,
                new_slug,
                src["short_description"],
                src["description_html"],
                src["brand_origin"],
                src["meta_title"],
                src["meta_description"],
                src["noindex"],
                ts,
                ts,
            ),
        )
        new_pid = int(cur.lastrowid)
        for idx, v in enumerate(src["variants"]):
            new_sku = f"{v['sku']}-CP{secrets.token_hex(2).upper()}"
            conn.execute(
                """
                INSERT INTO product_variants (
                    product_id, label, sku, price_rial, compare_at_price_rial,
                    weight_grams, stock_quantity, low_stock_threshold, is_active, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    new_pid,
                    v["label"],
                    new_sku,
                    v["price_rial"],
                    v["compare_at_price_rial"],
                    v["weight_grams"],
                    v["low_stock_threshold"],
                    v["is_active"],
                    idx,
                    ts,
                    ts,
                ),
            )
        for s in src["specs"]:
            conn.execute(
                "INSERT INTO product_specs (product_id, spec_key, spec_value, sort_order) VALUES (?, ?, ?, ?)",
                (new_pid, s["spec_key"], s["spec_value"], s["sort_order"]),
            )
        sync_product_tags(conn, new_pid, [t["name"] for t in src["tags"]])

    flash("کپی محصول با وضعیت پیش‌نویس ساخته شد.", "success")
    return redirect(url_for("admin.product_edit", product_id=new_pid))
