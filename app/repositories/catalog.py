from __future__ import annotations

import sqlite3
from typing import Any

from app.utils.persian import normalize_persian, now_utc_iso, slugify_persian


def list_categories(
    conn: sqlite3.Connection, *, active_only: bool = True
) -> list[sqlite3.Row]:
    sql = """
        SELECT c.*,
               (SELECT COUNT(*) FROM products p WHERE p.category_id = c.id AND p.status = 'published') AS product_count
        FROM categories c
    """
    if active_only:
        sql += " WHERE c.is_active = 1"
    sql += " ORDER BY c.sort_order ASC, c.id ASC"
    return conn.execute(sql).fetchall()


def get_category_by_slug(
    conn: sqlite3.Connection, slug: str, *, active_only: bool = True
) -> sqlite3.Row | None:
    sql = "SELECT * FROM categories WHERE slug = ?"
    if active_only:
        sql += " AND is_active = 1"
    return conn.execute(sql, (slug,)).fetchone()


def get_category_by_id(conn: sqlite3.Connection, cat_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()


def get_category_and_descendant_ids(conn: sqlite3.Connection, category_id: int) -> list[int]:
    rows = conn.execute("SELECT id, parent_id FROM categories WHERE is_active = 1").fetchall()
    children: dict[int | None, list[int]] = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r["id"])
    ids = [category_id]
    queue = [category_id]
    while queue:
        cur = queue.pop(0)
        for ch in children.get(cur, []):
            if ch not in ids:
                ids.append(ch)
                queue.append(ch)
    return ids


def _product_card_base_select() -> str:
    return """
        SELECT
            p.*,
            c.name AS category_name,
            c.slug AS category_slug,
            COALESCE((
                SELECT pi.thumb_path FROM product_images pi
                WHERE pi.product_id = p.id
                ORDER BY pi.is_cover DESC, pi.sort_order ASC, pi.id ASC
                LIMIT 1
            ), '') AS cover_thumb,
            COALESCE((
                SELECT pi.webp_path FROM product_images pi
                WHERE pi.product_id = p.id
                ORDER BY pi.is_cover DESC, pi.sort_order ASC, pi.id ASC
                LIMIT 1
            ), '') AS cover_webp,
            COALESCE((
                SELECT pi.file_path FROM product_images pi
                WHERE pi.product_id = p.id
                ORDER BY pi.is_cover DESC, pi.sort_order ASC, pi.id ASC
                LIMIT 1
            ), '') AS cover_image,
            COALESCE((
                SELECT pi.alt_text FROM product_images pi
                WHERE pi.product_id = p.id
                ORDER BY pi.is_cover DESC, pi.sort_order ASC, pi.id ASC
                LIMIT 1
            ), p.title) AS cover_alt,
            COALESCE((
                SELECT MIN(v.price_rial) FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1 AND v.stock_quantity > 0
            ), (
                SELECT MIN(v.price_rial) FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1
            ), 0) AS min_price_rial,
            COALESCE((
                SELECT MAX(v.price_rial) FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1
            ), 0) AS max_price_rial,
            (
                SELECT v.compare_at_price_rial FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1 AND v.compare_at_price_rial IS NOT NULL AND v.compare_at_price_rial > v.price_rial
                ORDER BY v.stock_quantity > 0 DESC, v.price_rial ASC
                LIMIT 1
            ) AS compare_at_price_rial,
            COALESCE((
                SELECT SUM(v.stock_quantity) FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1
            ), 0) AS total_stock,
            (
                SELECT v.id FROM product_variants v
                WHERE v.product_id = p.id AND v.is_active = 1 AND v.stock_quantity > 0
                ORDER BY v.sort_order ASC, v.price_rial ASC
                LIMIT 1
            ) AS default_variant_id
        FROM products p
        JOIN categories c ON c.id = p.category_id
    """


def list_products_storefront(
    conn: sqlite3.Connection,
    *,
    category_ids: list[int] | None = None,
    q: str = "",
    tag_slug: str = "",
    min_price_rial: int | None = None,
    max_price_rial: int | None = None,
    in_stock_only: bool = False,
    featured_only: bool = False,
    offers_only: bool = False,
    sort: str = "newest",
    page: int = 1,
    per_page: int = 12,
) -> tuple[list[dict[str, Any]], int]:
    where = ["p.status = 'published'", "c.is_active = 1"]
    params: list[Any] = []

    if category_ids:
        placeholders = ",".join("?" for _ in category_ids)
        where.append(f"p.category_id IN ({placeholders})")
        params.extend(category_ids)

    if featured_only:
        where.append("p.is_featured = 1")

    if tag_slug:
        where.append(
            "EXISTS (SELECT 1 FROM product_tags pt JOIN tags t ON t.id = pt.tag_id WHERE pt.product_id = p.id AND t.slug = ?)"
        )
        params.append(tag_slug)

    if in_stock_only:
        where.append(
            "EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1 AND v.stock_quantity > 0)"
        )

    if offers_only:
        where.append(
            "EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1 AND v.compare_at_price_rial IS NOT NULL AND v.compare_at_price_rial > v.price_rial)"
        )

    if min_price_rial is not None:
        where.append(
            "EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1 AND v.price_rial >= ?)"
        )
        params.append(min_price_rial)

    if max_price_rial is not None:
        where.append(
            "EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1 AND v.price_rial <= ?)"
        )
        params.append(max_price_rial)

    order_map = {
        "newest": "total_stock > 0 DESC, p.created_at DESC, p.id DESC",
        "price_asc": "total_stock > 0 DESC, min_price_rial ASC, p.id DESC",
        "price_desc": "total_stock > 0 DESC, min_price_rial DESC, p.id DESC",
        "best_selling": "total_stock > 0 DESC, p.total_sold DESC, p.id DESC",
        "top_rated": "total_stock > 0 DESC, p.avg_rating DESC, p.review_count DESC, p.id DESC",
    }
    order_by = order_map.get(sort, order_map["newest"])

    where_sql = "WHERE " + " AND ".join(where)
    rows = conn.execute(
        f"""
        {_product_card_base_select()}
        {where_sql}
        ORDER BY {order_by}
        """,
        params,
    ).fetchall()

    items = [dict(r) for r in rows]

    # Persian-normalized search over title, short_description, description_html, brand_origin, tags, SKUs
    if q and q.strip():
        nq = normalize_persian(q)
        tokens = [t for t in nq.split(" ") if t]
        if tokens:
            pid_list = [it["id"] for it in items]
            sku_map: dict[int, str] = {}
            tag_map: dict[int, str] = {}
            if pid_list:
                ph = ",".join("?" for _ in pid_list)
                for vr in conn.execute(
                    f"SELECT product_id, sku, label FROM product_variants WHERE product_id IN ({ph})",
                    pid_list,
                ).fetchall():
                    sku_map[vr["product_id"]] = (
                        sku_map.get(vr["product_id"], "") + " " + vr["sku"] + " " + vr["label"]
                    )
                for tr in conn.execute(
                    f"SELECT pt.product_id, t.name FROM product_tags pt JOIN tags t ON t.id = pt.tag_id WHERE pt.product_id IN ({ph})",
                    pid_list,
                ).fetchall():
                    tag_map[tr["product_id"]] = tag_map.get(tr["product_id"], "") + " " + tr["name"]

            filtered = []
            for it in items:
                haystack = normalize_persian(
                    " ".join(
                        [
                            it.get("title", ""),
                            it.get("short_description", ""),
                            it.get("description_html", ""),
                            it.get("brand_origin", ""),
                            it.get("category_name", ""),
                            sku_map.get(it["id"], ""),
                            tag_map.get(it["id"], ""),
                        ]
                    )
                )
                if all(tok in haystack for tok in tokens):
                    filtered.append(it)
            items = filtered

    total = len(items)
    start = max(0, (page - 1) * per_page)
    end = start + per_page
    return items[start:end], total


def get_product_detail_by_slug(
    conn: sqlite3.Connection, slug: str, *, include_unpublished: bool = False
) -> dict[str, Any] | None:
    sql = """
        SELECT p.*, c.name AS category_name, c.slug AS category_slug, c.parent_id AS category_parent_id
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.slug = ?
    """
    if not include_unpublished:
        sql += " AND p.status = 'published'"
    row = conn.execute(sql, (slug,)).fetchone()
    if not row:
        return None
    return enrich_product_detail(conn, dict(row), active_variants_only=not include_unpublished)


def get_product_detail_by_id(conn: sqlite3.Connection, product_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT p.*, c.name AS category_name, c.slug AS category_slug
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.id = ?
        """,
        (product_id,),
    ).fetchone()
    if not row:
        return None
    return enrich_product_detail(conn, dict(row), active_variants_only=False)


def enrich_product_detail(
    conn: sqlite3.Connection, product: dict[str, Any], *, active_variants_only: bool = True
) -> dict[str, Any]:
    pid = product["id"]
    v_sql = "SELECT * FROM product_variants WHERE product_id = ?"
    if active_variants_only:
        v_sql += " AND is_active = 1"
    v_sql += " ORDER BY sort_order ASC, id ASC"
    variants = [dict(v) for v in conn.execute(v_sql, (pid,)).fetchall()]
    for v in variants:
        v["in_stock"] = v["stock_quantity"] > 0
        v["is_low_stock"] = 0 < v["stock_quantity"] <= v["low_stock_threshold"]

    images = [
        dict(i)
        for i in conn.execute(
            "SELECT * FROM product_images WHERE product_id = ? ORDER BY is_cover DESC, sort_order ASC, id ASC",
            (pid,),
        ).fetchall()
    ]
    videos = [
        dict(v)
        for v in conn.execute(
            "SELECT * FROM product_videos WHERE product_id = ? ORDER BY sort_order ASC, id ASC",
            (pid,),
        ).fetchall()
    ]
    specs = [
        dict(s)
        for s in conn.execute(
            "SELECT * FROM product_specs WHERE product_id = ? ORDER BY sort_order ASC, id ASC",
            (pid,),
        ).fetchall()
    ]
    tags = [
        dict(t)
        for t in conn.execute(
            "SELECT t.* FROM tags t JOIN product_tags pt ON pt.tag_id = t.id WHERE pt.product_id = ? ORDER BY t.name",
            (pid,),
        ).fetchall()
    ]
    related = [
        dict(r)
        for r in conn.execute(
            f"""
            {_product_card_base_select()}
            JOIN related_products rp ON rp.related_product_id = p.id
            WHERE rp.product_id = ? AND p.status = 'published'
            ORDER BY rp.sort_order ASC
            LIMIT 8
            """,
            (pid,),
        ).fetchall()
    ]
    if not related:
        related = [
            dict(r)
            for r in conn.execute(
                f"""
                {_product_card_base_select()}
                WHERE p.category_id = ? AND p.id != ? AND p.status = 'published'
                ORDER BY p.total_sold DESC, p.id DESC
                LIMIT 6
                """,
                (product["category_id"], pid),
            ).fetchall()
        ]

    total_stock = sum(v["stock_quantity"] for v in variants if v["is_active"])
    product["variants"] = variants
    product["images"] = images
    product["videos"] = videos
    product["specs"] = specs
    product["tags"] = tags
    product["related_products"] = related
    product["total_stock"] = total_stock
    product["in_stock"] = any(v["stock_quantity"] > 0 and v["is_active"] for v in variants)
    return product


def list_all_tags(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.*, COUNT(pt.product_id) AS product_count
        FROM tags t
        LEFT JOIN product_tags pt ON pt.tag_id = t.id
        GROUP BY t.id
        ORDER BY t.name ASC
        """
    ).fetchall()


def sync_product_tags(conn: sqlite3.Connection, product_id: int, tag_names: list[str]) -> None:
    conn.execute("DELETE FROM product_tags WHERE product_id = ?", (product_id,))
    seen = set()
    for raw in tag_names:
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        slug = slugify_persian(name)
        row = conn.execute("SELECT id FROM tags WHERE name = ? OR slug = ?", (name, slug)).fetchone()
        if row:
            tag_id = row["id"]
        else:
            cur = conn.execute("INSERT INTO tags (name, slug) VALUES (?, ?)", (name, slug))
            tag_id = int(cur.lastrowid)
        conn.execute(
            "INSERT OR IGNORE INTO product_tags (product_id, tag_id) VALUES (?, ?)",
            (product_id, tag_id),
        )


def list_products_admin(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    category_id: int | None = None,
    status: str = "",
    stock_state: str = "",
    sort: str = "updated_desc",
    page: int = 1,
    per_page: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    where = []
    params: list[Any] = []
    if category_id:
        where.append("p.category_id = ?")
        params.append(category_id)
    if status:
        where.append("p.status = ?")
        params.append(status)
    if q and q.strip():
        like = f"%{q.strip()}%"
        where.append(
            "(p.title LIKE ? OR p.slug LIKE ? OR p.brand_origin LIKE ? OR EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.sku LIKE ?))"
        )
        params.extend([like, like, like, like])
    if stock_state == "out":
        where.append(
            "COALESCE((SELECT SUM(v.stock_quantity) FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1), 0) <= 0"
        )
    elif stock_state == "low":
        where.append(
            "EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1 AND v.stock_quantity > 0 AND v.stock_quantity <= v.low_stock_threshold)"
        )
    elif stock_state == "in":
        where.append(
            "EXISTS (SELECT 1 FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1 AND v.stock_quantity > 0)"
        )

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_map = {
        "updated_desc": "p.updated_at DESC, p.id DESC",
        "created_desc": "p.id DESC",
        "title_asc": "p.title ASC",
        "stock_asc": "total_stock ASC, p.id DESC",
        "sold_desc": "p.total_sold DESC",
    }
    order_by = order_map.get(sort, order_map["updated_desc"])
    total = conn.execute(
        f"SELECT COUNT(*) FROM products p {where_sql}", params
    ).fetchone()[0]
    offset = max(0, (page - 1) * per_page)
    rows = conn.execute(
        f"""
        SELECT
            p.*,
            c.name AS category_name,
            c.slug AS category_slug,
            COALESCE((
                SELECT pi.thumb_path FROM product_images pi
                WHERE pi.product_id = p.id
                ORDER BY pi.is_cover DESC, pi.sort_order ASC, pi.id ASC
                LIMIT 1
            ), '') AS cover_thumb,
            COALESCE((
                SELECT MIN(v.price_rial) FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1
            ), 0) AS min_price_rial,
            COALESCE((
                SELECT MAX(v.price_rial) FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1
            ), 0) AS max_price_rial,
            COALESCE((
                SELECT SUM(v.stock_quantity) FROM product_variants v WHERE v.product_id = p.id AND v.is_active = 1
            ), 0) AS total_stock,
            COALESCE((
                SELECT GROUP_CONCAT(v.sku, '، ') FROM product_variants v WHERE v.product_id = p.id
            ), '') AS skus,
            (SELECT COUNT(*) FROM order_items oi WHERE oi.product_id = p.id) AS order_ref_count
        FROM products p
        JOIN categories c ON c.id = p.category_id
        {where_sql}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    ).fetchall()
    return [dict(r) for r in rows], total


def ensure_unique_slug(
    conn: sqlite3.Connection, table: str, desired_slug: str, exclude_id: int | None = None
) -> str:
    base = slugify_persian(desired_slug)
    slug = base
    idx = 2
    while True:
        if exclude_id:
            row = conn.execute(
                f"SELECT id FROM {table} WHERE slug = ? AND id != ?", (slug, exclude_id)
            ).fetchone()
        else:
            row = conn.execute(f"SELECT id FROM {table} WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return slug
        slug = f"{base}-{idx}"
        idx += 1
