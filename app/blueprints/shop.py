from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import quote

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from app import limiter
from app.db import get_db
from app.repositories.catalog import (
    get_category_and_descendant_ids,
    get_category_by_slug,
    get_product_detail_by_slug,
    list_all_tags,
    list_categories,
    list_products_storefront,
)
from app.repositories.cms import (
    add_back_in_stock_request,
    get_page_by_slug,
    list_active_banners,
    list_featured_collections,
)
from app.repositories.settings import get_all_settings
from app.repositories.users import list_cities_by_province
from app.services.inventory import expire_unpaid_orders
from app.services.reviews import list_product_reviews, submit_review, vote_review_helpful
from app.services.seo import (
    build_breadcrumb_ld,
    build_faq_ld,
    build_organization_ld,
    build_product_ld,
    build_website_ld,
    generate_robots_txt,
    generate_sitemap_xml,
)
from app.utils.persian import normalize_iran_mobile, now_utc_iso, to_english_digits

shop_bp = Blueprint("shop", __name__)


def _parse_int(val: str | None, default: int | None = None) -> int | None:
    if not val:
        return default
    try:
        return int(to_english_digits(val).strip())
    except ValueError:
        return default


@shop_bp.route("/")
def home():
    conn = get_db()
    expire_unpaid_orders(conn)
    settings = get_all_settings(conn)
    base_url = current_app.config["BASE_URL"]

    hero_banners = list_active_banners(conn, "hero")
    promo_banners = list_active_banners(conn, "promo")
    categories = list_categories(conn, active_only=True)
    collections = list_featured_collections(conn, active_only=True)

    best_sellers, _ = list_products_storefront(conn, sort="best_selling", page=1, per_page=8)
    newest_products, _ = list_products_storefront(conn, sort="newest", page=1, per_page=8)
    special_offers, _ = list_products_storefront(conn, offers_only=True, sort="newest", page=1, per_page=8)

    json_ld = [
        build_organization_ld(base_url, settings),
        build_website_ld(base_url, settings),
    ]

    return render_template(
        "shop/home.html",
        hero_banners=hero_banners,
        promo_banners=promo_banners,
        categories=categories,
        collections=collections,
        best_sellers=best_sellers,
        newest_products=newest_products,
        special_offers=special_offers,
        meta_title=settings.get("default_meta_title"),
        meta_description=settings.get("default_meta_description"),
        canonical_url=f"{base_url}/",
        json_ld=json_ld,
    )


@shop_bp.route("/products")
def products_list():
    return _render_catalog_listing(category=None, is_search=False)


@shop_bp.route("/category/<slug>")
def category_view(slug: str):
    conn = get_db()
    cat = get_category_by_slug(conn, slug, active_only=True)
    if not cat:
        abort(404)
    return _render_catalog_listing(category=dict(cat), is_search=False)


@shop_bp.route("/search")
@limiter.limit("60/minute")
def search():
    return _render_catalog_listing(category=None, is_search=True)


def _render_catalog_listing(*, category: dict | None, is_search: bool):
    conn = get_db()
    expire_unpaid_orders(conn)
    settings = get_all_settings(conn)
    base_url = current_app.config["BASE_URL"]

    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "newest")
    if sort not in {"newest", "price_asc", "price_desc", "best_selling", "top_rated"}:
        sort = "newest"
    tag_slug = (request.args.get("tag") or "").strip()
    in_stock_only = request.args.get("in_stock") == "1"
    offers_only = request.args.get("offers") == "1"
    min_toman = _parse_int(request.args.get("min_price"))
    max_toman = _parse_int(request.args.get("max_price"))
    page = max(1, _parse_int(request.args.get("page"), 1) or 1)
    per_page = 12

    cat_ids = None
    if category:
        cat_ids = get_category_and_descendant_ids(conn, int(category["id"]))
    elif request.args.get("category"):
        c_row = get_category_by_slug(conn, request.args.get("category", ""), active_only=True)
        if c_row:
            cat_ids = get_category_and_descendant_ids(conn, int(c_row["id"]))

    items, total = list_products_storefront(
        conn,
        category_ids=cat_ids,
        q=q,
        tag_slug=tag_slug,
        min_price_rial=(min_toman * 10) if min_toman is not None else None,
        max_price_rial=(max_toman * 10) if max_toman is not None else None,
        in_stock_only=in_stock_only,
        offers_only=offers_only,
        sort=sort,
        page=page,
        per_page=per_page,
    )
    total_pages = max(1, math.ceil(total / per_page))
    categories = list_categories(conn, active_only=True)
    tags = list_all_tags(conn)

    has_filters = any(
        [
            q,
            tag_slug,
            in_stock_only,
            offers_only,
            min_toman is not None,
            max_toman is not None,
            request.args.get("sort") not in (None, "", "newest"),
            request.args.get("category"),
        ]
    )

    if category:
        page_title = category.get("meta_title") or f"{category['name']} | {settings.get('site_name')}"
        meta_desc = category.get("meta_description") or category.get("description") or settings.get("default_meta_description")
        canonical = category.get("canonical_url") or f"{base_url}/category/{quote(category['slug'])}"
        noindex = bool(category.get("noindex")) or has_filters
        breadcrumbs = [
            ("خانه", url_for("shop.home")),
            (category["name"], url_for("shop.category_view", slug=category["slug"])),
        ]
        ld_crumbs = [
            ("خانه", f"{base_url}/"),
            (category["name"], f"{base_url}/category/{quote(category['slug'])}"),
        ]
    elif is_search:
        page_title = f"جستجو: {q} | {settings.get('site_name')}" if q else f"جستجو در محصولات | {settings.get('site_name')}"
        meta_desc = settings.get("default_meta_description")
        canonical = f"{base_url}/search"
        noindex = True
        breadcrumbs = [("خانه", url_for("shop.home")), ("جستجو", url_for("shop.search"))]
        ld_crumbs = [("خانه", f"{base_url}/"), ("جستجو", f"{base_url}/search")]
    else:
        page_title = f"همه محصولات | {settings.get('site_name')}"
        meta_desc = settings.get("default_meta_description")
        canonical = f"{base_url}/products"
        noindex = has_filters
        breadcrumbs = [("خانه", url_for("shop.home")), ("محصولات", url_for("shop.products_list"))]
        ld_crumbs = [("خانه", f"{base_url}/"), ("محصولات", f"{base_url}/products")]

    if page > 1 and not noindex:
        canonical = f"{canonical}?page={page}"

    return render_template(
        "shop/listing.html",
        category=category,
        is_search=is_search,
        items=items,
        total=total,
        page=page,
        total_pages=total_pages,
        categories=categories,
        tags=tags,
        q=q,
        sort=sort,
        tag_slug=tag_slug,
        in_stock_only=in_stock_only,
        offers_only=offers_only,
        min_price=min_toman if min_toman is not None else "",
        max_price=max_toman if max_toman is not None else "",
        breadcrumbs=breadcrumbs,
        meta_title=page_title,
        meta_description=meta_desc,
        canonical_url=canonical,
        noindex=noindex,
        json_ld=[build_breadcrumb_ld(ld_crumbs)],
    )


@shop_bp.route("/product/<slug>")
def product_detail(slug: str):
    conn = get_db()
    expire_unpaid_orders(conn)
    product = get_product_detail_by_slug(conn, slug)
    if not product:
        # Check if archived (return 410 Gone if explicitly archived and requested? Wait: 404 or 410)
        archived = conn.execute(
            "SELECT status FROM products WHERE slug = ?", (slug,)
        ).fetchone()
        if archived and archived["status"] == "archived":
            return render_template("errors/410.html"), 410
        abort(404)

    settings = get_all_settings(conn)
    base_url = current_app.config["BASE_URL"]
    reviews = list_product_reviews(conn, int(product["id"]))

    in_wishlist = False
    if g.current_user:
        w = conn.execute(
            "SELECT 1 FROM wishlists WHERE user_id = ? AND product_id = ?",
            (g.current_user["id"], product["id"]),
        ).fetchone()
        in_wishlist = w is not None

    breadcrumbs = [
        ("خانه", url_for("shop.home")),
        (product["category_name"], url_for("shop.category_view", slug=product["category_slug"])),
        (product["title"], url_for("shop.product_detail", slug=product["slug"])),
    ]
    ld_crumbs = [
        ("خانه", f"{base_url}/"),
        (product["category_name"], f"{base_url}/category/{quote(product['category_slug'])}"),
        (product["title"], f"{base_url}/product/{quote(product['slug'])}"),
    ]

    og_img = None
    if product.get("og_image_path"):
        og_img = f"{base_url}/media/{product['og_image_path']}"
    elif product["images"]:
        og_img = f"{base_url}/media/{product['images'][0]['webp_path'] or product['images'][0]['file_path']}"

    json_ld = [build_breadcrumb_ld(ld_crumbs)] + build_product_ld(base_url, product, reviews, settings)

    return render_template(
        "shop/product_detail.html",
        product=product,
        reviews=reviews,
        in_wishlist=in_wishlist,
        breadcrumbs=breadcrumbs,
        meta_title=product.get("meta_title") or f"{product['title']} | {settings.get('site_name')}",
        meta_description=product.get("meta_description") or product.get("short_description") or settings.get("default_meta_description"),
        canonical_url=product.get("canonical_url") or f"{base_url}/product/{quote(product['slug'])}",
        noindex=bool(product.get("noindex")),
        og_type="product",
        og_image=og_img,
        json_ld=json_ld,
    )


@shop_bp.route("/product/<slug>/review", methods=["POST"])
@limiter.limit("10/hour")
def post_review(slug: str):
    conn = get_db()
    if not g.current_user:
        flash("برای ثبت دیدگاه ابتدا وارد حساب کاربری خود شوید.", "warning")
        return redirect(url_for("auth.login", next=url_for("shop.product_detail", slug=slug)))

    product = get_product_detail_by_slug(conn, slug)
    if not product:
        abort(404)

    rating = _parse_int(request.form.get("rating"), 0) or 0
    title = request.form.get("title", "")
    body = request.form.get("body", "")
    honeypot = request.form.get("website_url", "")

    ok, msg = submit_review(
        conn,
        product_id=int(product["id"]),
        user_id=int(g.current_user["id"]),
        rating=rating,
        title=title,
        body=body,
        honeypot=honeypot,
    )
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("shop.product_detail", slug=slug) + "#reviews")


@shop_bp.route("/review/<int:review_id>/helpful", methods=["POST"])
@limiter.limit("30/hour")
def helpful_vote(review_id: int):
    conn = get_db()
    uid = int(g.current_user["id"]) if g.current_user else None
    voted, count = vote_review_helpful(
        conn,
        review_id=review_id,
        user_id=uid,
        ip_address=request.remote_addr or "0.0.0.0",
    )
    return jsonify({"ok": True, "voted": voted, "helpful_count": count})


@shop_bp.route("/notify-stock", methods=["POST"])
@limiter.limit("15/hour")
def notify_stock():
    conn = get_db()
    variant_id = _parse_int(request.form.get("variant_id"), 0) or 0
    mobile = normalize_iran_mobile(request.form.get("mobile"))
    if not mobile:
        return jsonify({"ok": False, "message": "شماره موبایل معتبر وارد کنید."}), 400
    vrow = conn.execute(
        "SELECT id, product_id, stock_quantity FROM product_variants WHERE id = ? AND is_active = 1",
        (variant_id,),
    ).fetchone()
    if not vrow:
        return jsonify({"ok": False, "message": "محصول یافت نشد."}), 404
    if int(vrow["stock_quantity"]) > 0:
        return jsonify({"ok": False, "message": "این محصول هم‌اکنون موجود است و می‌توانید سفارش دهید."}), 400

    add_back_in_stock_request(
        conn,
        product_id=int(vrow["product_id"]),
        variant_id=int(vrow["id"]),
        mobile=mobile,
    )
    return jsonify({"ok": True, "message": "شماره شما ثبت شد. به محض موجود شدن پیامک ارسال می‌کنیم."})


@shop_bp.route("/wishlist/toggle", methods=["POST"])
def toggle_wishlist():
    if not g.current_user:
        return jsonify({"ok": False, "login_required": True, "message": "ابتدا وارد حساب کاربری شوید."}), 401
    conn = get_db()
    pid = _parse_int(request.form.get("product_id"), 0) or 0
    uid = int(g.current_user["id"])
    exists = conn.execute(
        "SELECT id FROM wishlists WHERE user_id = ? AND product_id = ?", (uid, pid)
    ).fetchone()
    if exists:
        conn.execute("DELETE FROM wishlists WHERE id = ?", (exists["id"],))
        return jsonify({"ok": True, "in_wishlist": False, "message": "از علاقه‌مندی‌ها حذف شد."})
    conn.execute(
        "INSERT OR IGNORE INTO wishlists (user_id, product_id, created_at) VALUES (?, ?, ?)",
        (uid, pid, now_utc_iso()),
    )
    return jsonify({"ok": True, "in_wishlist": True, "message": "به علاقه‌مندی‌ها اضافه شد."})


@shop_bp.route("/newsletter/subscribe", methods=["POST"])
@limiter.limit("10/hour")
def subscribe_newsletter():
    conn = get_db()
    mobile = normalize_iran_mobile(request.form.get("mobile"))
    consent = request.form.get("consent") in {"1", "on", "true", "yes"}
    if not consent:
        flash("لطفاً موافقت خود با دریافت پیامک تخفیف‌ها را تایید کنید.", "warning")
        return redirect(request.referrer or url_for("shop.home"))
    if not mobile:
        flash("شماره موبایل معتبر نیست.", "danger")
        return redirect(request.referrer or url_for("shop.home"))
    conn.execute(
        """
        INSERT INTO newsletter_subscribers (mobile, consent, source, created_at)
        VALUES (?, 1, 'footer', ?)
        ON CONFLICT(mobile) DO UPDATE SET consent = 1
        """,
        (mobile, now_utc_iso()),
    )
    flash("شماره شما با موفقیت در خبرنامه پیامکی ثبت شد.", "success")
    return redirect(request.referrer or url_for("shop.home"))


@shop_bp.route("/page/<slug>")
def static_page(slug: str):
    conn = get_db()
    page = get_page_by_slug(conn, slug, active_only=True)
    if not page:
        abort(404)
    settings = get_all_settings(conn)
    base_url = current_app.config["BASE_URL"]

    faq_items = []
    json_ld = [
        build_breadcrumb_ld(
            [("خانه", f"{base_url}/"), (page["title"], f"{base_url}/page/{quote(page['slug'])}")]
        )
    ]
    if page["page_type"] == "faq":
        try:
            faq_items = json.loads(page["faq_json"] or "[]")
        except ValueError:
            faq_items = []
        faq_ld = build_faq_ld(faq_items)
        if faq_ld:
            json_ld.append(faq_ld)

    return render_template(
        "shop/page.html",
        page=dict(page),
        faq_items=faq_items,
        breadcrumbs=[("خانه", url_for("shop.home")), (page["title"], url_for("shop.static_page", slug=page["slug"]))],
        meta_title=page["meta_title"] or f"{page['title']} | {settings.get('site_name')}",
        meta_description=page["meta_description"] or settings.get("default_meta_description"),
        canonical_url=page["canonical_url"] or f"{base_url}/page/{quote(page['slug'])}",
        noindex=bool(page["noindex"]),
        json_ld=json_ld,
    )


@shop_bp.route("/api/provinces/<int:province_id>/cities")
def province_cities_api(province_id: int):
    conn = get_db()
    rows = list_cities_by_province(conn, province_id)
    return jsonify([{"id": r["id"], "name": r["name"]} for r in rows])


@shop_bp.route("/media/<path:filename>")
def serve_media(filename: str):
    upload_root = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    clean = filename.strip().lstrip("/")
    if ".." in clean:
        abort(404)
    target = (upload_root / clean).resolve()
    if upload_root not in target.parents or not target.is_file():
        abort(404)
    resp = send_from_directory(upload_root, clean, max_age=2592000)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@shop_bp.route("/sitemap.xml")
def sitemap_xml():
    conn = get_db()
    xml_text = generate_sitemap_xml(conn, current_app.config["BASE_URL"])
    return Response(xml_text, mimetype="application/xml; charset=utf-8")


@shop_bp.route("/robots.txt")
def robots_txt():
    conn = get_db()
    settings = get_all_settings(conn)
    txt = generate_robots_txt(
        current_app.config["BASE_URL"],
        current_app.config["ADMIN_URL_PREFIX"],
        settings.get("robots_txt_extra", ""),
    )
    return Response(txt, mimetype="text/plain; charset=utf-8")
