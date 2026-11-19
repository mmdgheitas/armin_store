"""robots.txt و sitemap.xml تولیدشده از داده واقعی."""
from __future__ import annotations

from flask import Blueprint, Response, current_app, request, url_for

from app.extensions import db
from app.models import BlogPost, Category, Page, Product
from app.services import settings_service

seo_bp = Blueprint("seo", __name__)


@seo_bp.get("/robots.txt")
def robots_txt():
    content = settings_service.get_str("robots_txt") or "User-agent: *\nAllow: /"
    sitemap_line = f"\nSitemap: {url_for('seo.sitemap_xml', _external=True)}"
    if "sitemap" not in content.lower():
        content = content.rstrip() + sitemap_line
    return Response(content, mimetype="text/plain")


@seo_bp.get("/sitemap.xml")
def sitemap_xml():
    if not settings_service.get_bool("sitemap_enabled", True):
        return Response("<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset/>", mimetype="application/xml")

    entries: list[tuple[str, str | None, str]] = [
        (url_for("storefront.home", _external=True), None, "daily"),
        (url_for("storefront.product_list", _external=True), None, "daily"),
        (url_for("storefront.blog_list", _external=True), None, "weekly"),
    ]
    try:
        for product in (
            db.session.query(Product)
            .filter(Product.is_active.is_(True), Product.deleted_at.is_(None))
            .order_by(Product.updated_at.desc())
            .limit(2000)
            .all()
        ):
            entries.append(
                (
                    url_for("storefront.product_detail", slug=product.slug, _external=True),
                    product.updated_at.strftime("%Y-%m-%d") if product.updated_at else None,
                    "weekly",
                )
            )
        for category in db.session.query(Category).filter(Category.is_active.is_(True)).all():
            entries.append(
                (
                    url_for("storefront.category_page", slug=category.slug, _external=True),
                    None,
                    "weekly",
                )
            )
        for post in (
            db.session.query(BlogPost)
            .filter(BlogPost.status == "published")
            .order_by(BlogPost.published_at.desc())
            .limit(500)
            .all()
        ):
            entries.append(
                (
                    url_for("storefront.blog_detail", slug=post.slug, _external=True),
                    post.published_at.strftime("%Y-%m-%d") if post.published_at else None,
                    "monthly",
                )
            )
        for page in db.session.query(Page).filter(Page.is_published.is_(True)).all():
            entries.append(
                (url_for("storefront.static_page", slug=page.slug, _external=True), None, "monthly")
            )
    except Exception:  # pragma: no cover - جداول هنوز ساخته نشده‌اند
        db.session.rollback()

    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, changefreq in entries:
        xml.append("<url>")
        xml.append(f"<loc>{loc}</loc>")
        if lastmod:
            xml.append(f"<lastmod>{lastmod}</lastmod>")
        xml.append(f"<changefreq>{changefreq}</changefreq>")
        xml.append("</url>")
    xml.append("</urlset>")
    return Response("\n".join(xml), mimetype="application/xml")
