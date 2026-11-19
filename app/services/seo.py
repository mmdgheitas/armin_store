from __future__ import annotations

import json
import sqlite3
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

from app.utils.persian import now_utc_iso


def build_organization_ld(base_url: str, settings: dict[str, str]) -> dict[str, Any]:
    same_as = [
        u
        for u in [
            settings.get("instagram_url", ""),
            settings.get("telegram_url", ""),
            settings.get("whatsapp_url", ""),
        ]
        if u.strip()
    ]
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": settings.get("site_name", "فروشگاه آرمین"),
        "url": base_url,
    }
    if settings.get("site_logo_path"):
        data["logo"] = f"{base_url}/media/{settings['site_logo_path']}"
    if settings.get("contact_phone"):
        data["telephone"] = settings["contact_phone"]
    if same_as:
        data["sameAs"] = same_as
    return data


def build_website_ld(base_url: str, settings: dict[str, str]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": settings.get("site_name", "فروشگاه آرمین"),
        "url": base_url,
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{base_url}/search?q={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }


def build_breadcrumb_ld(items: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": idx + 1,
                "name": name,
                "item": url,
            }
            for idx, (name, url) in enumerate(items)
        ],
    }


def build_product_ld(
    base_url: str,
    product: dict[str, Any],
    reviews: list[sqlite3.Row],
    settings: dict[str, str],
) -> list[dict[str, Any]]:
    prod_url = f"{base_url}/product/{quote(product['slug'])}"
    images = [
        f"{base_url}/media/{img['webp_path'] or img['file_path']}"
        for img in product.get("images", [])
    ]
    variants = [v for v in product.get("variants", []) if v["is_active"]]
    prices = [int(v["price_rial"]) for v in variants] or [0]
    in_stock = any(int(v["stock_quantity"]) > 0 for v in variants)
    availability = "https://schema.org/InStock" if in_stock else "https://schema.org/OutOfStock"

    offers: dict[str, Any]
    if len(prices) > 1 and min(prices) != max(prices):
        offers = {
            "@type": "AggregateOffer",
            "priceCurrency": "IRR",
            "lowPrice": min(prices),
            "highPrice": max(prices),
            "offerCount": len(variants),
            "availability": availability,
            "url": prod_url,
        }
    else:
        offers = {
            "@type": "Offer",
            "priceCurrency": "IRR",
            "price": min(prices),
            "availability": availability,
            "url": prod_url,
            "itemCondition": "https://schema.org/NewCondition",
        }

    product_ld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product["title"],
        "description": product.get("meta_description") or product.get("short_description") or product["title"],
        "url": prod_url,
        "sku": variants[0]["sku"] if variants else product["slug"],
        "category": product.get("category_name", ""),
        "brand": {
            "@type": "Brand",
            "name": product.get("brand_origin") or settings.get("site_name", "آرمین"),
        },
        "offers": offers,
    }
    if images:
        product_ld["image"] = images

    if int(product.get("review_count") or 0) > 0:
        product_ld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": float(product["avg_rating"]),
            "reviewCount": int(product["review_count"]),
            "bestRating": 5,
            "worstRating": 1,
        }
    if reviews:
        product_ld["review"] = [
            {
                "@type": "Review",
                "author": {"@type": "Person", "name": r["author_name"] or "کاربر فروشگاه"},
                "datePublished": (r["created_at"] or "")[:10],
                "reviewBody": r["body"],
                "name": r["title"] or product["title"],
                "reviewRating": {
                    "@type": "Rating",
                    "ratingValue": int(r["rating"]),
                    "bestRating": 5,
                    "worstRating": 1,
                },
            }
            for r in reviews[:5]
        ]

    schemas: list[dict[str, Any]] = [product_ld]
    for vid in product.get("videos", []):
        v_ld: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            "name": vid.get("title") or product["title"],
            "description": vid.get("description") or product.get("short_description") or product["title"],
            "uploadDate": (vid.get("created_at") or now_utc_iso())[:10],
        }
        if vid.get("poster_path"):
            v_ld["thumbnailUrl"] = f"{base_url}/media/{vid['poster_path']}"
        elif images:
            v_ld["thumbnailUrl"] = images[0]
        if vid.get("source_type") == "upload" and vid.get("file_path"):
            v_ld["contentUrl"] = f"{base_url}/media/{vid['file_path']}"
        elif vid.get("embed_url"):
            v_ld["embedUrl"] = vid["embed_url"]
        schemas.append(v_ld)

    return schemas


def build_faq_ld(faq_items: list[dict[str, str]]) -> dict[str, Any] | None:
    if not faq_items:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": item.get("q", ""),
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": item.get("a", ""),
                },
            }
            for item in faq_items
            if item.get("q") and item.get("a")
        ],
    }


def generate_sitemap_xml(conn: sqlite3.Connection, base_url: str) -> str:
    urls: list[tuple[str, str, str, str]] = [
        (f"{base_url}/", now_utc_iso()[:10], "daily", "1.0"),
        (f"{base_url}/products", now_utc_iso()[:10], "daily", "0.9"),
    ]
    for c in conn.execute(
        "SELECT slug, updated_at FROM categories WHERE is_active = 1 AND noindex = 0 ORDER BY id"
    ).fetchall():
        urls.append(
            (
                f"{base_url}/category/{quote(c['slug'])}",
                (c["updated_at"] or now_utc_iso())[:10],
                "daily",
                "0.8",
            )
        )
    for p in conn.execute(
        "SELECT slug, updated_at FROM products WHERE status = 'published' AND noindex = 0 ORDER BY id DESC"
    ).fetchall():
        urls.append(
            (
                f"{base_url}/product/{quote(p['slug'])}",
                (p["updated_at"] or now_utc_iso())[:10],
                "weekly",
                "0.8",
            )
        )
    for pg in conn.execute(
        "SELECT slug, updated_at FROM pages WHERE is_active = 1 AND noindex = 0 ORDER BY id"
    ).fetchall():
        urls.append(
            (
                f"{base_url}/page/{quote(pg['slug'])}",
                (pg["updated_at"] or now_utc_iso())[:10],
                "monthly",
                "0.5",
            )
        )

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, changefreq, priority in urls:
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(loc)}</loc>")
        lines.append(f"    <lastmod>{escape(lastmod)}</lastmod>")
        lines.append(f"    <changefreq>{changefreq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines)


def generate_robots_txt(
    base_url: str, admin_prefix: str, extra: str = ""
) -> str:
    lines = [
        "User-agent: *",
        f"Disallow: {admin_prefix}/",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /account",
        "Disallow: /auth",
        "Disallow: /payment",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    if extra.strip():
        lines.append(extra.strip())
    return "\n".join(lines) + "\n"
