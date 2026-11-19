"""سرویس محصول: Slug یکتا، مشخصات، تنوع‌ها و امتیازدهی."""
from __future__ import annotations

import re

from sqlalchemy import func

from app.extensions import db
from app.models import Category, Product, ProductVariant, Review
from app.utils.security import slugify


def unique_slug(model, name: str, current_id: int | None = None, max_length: int = 200) -> str:
    base = slugify(name, max_length=max_length)
    candidate = base
    counter = 2
    while True:
        query = db.session.query(model.id).filter(model.slug == candidate)
        if current_id:
            query = query.filter(model.id != current_id)
        if query.first() is None:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


def unique_sku(model, base: str, current_id: int | None = None) -> str:
    base = (base or "SKU").strip().upper()[:60] or "SKU"
    candidate = base
    counter = 2
    while True:
        query = db.session.query(model.id).filter(model.sku == candidate)
        if current_id:
            query = query.filter(model.id != current_id)
        if query.first() is None:
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


def parse_specifications(text: str | None) -> list[dict]:
    """
    ورودی هر خط: «نام ویژگی: مقدار» یا «نام ویژگی=مقدار».
    خروجی: [{"name": ..., "value": ...}]
    """
    specs: list[dict] = []
    if not text:
        return specs
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            name, _, value = line.partition(":")
        elif "=" in line:
            name, _, value = line.partition("=")
        else:
            continue
        name = name.strip()[:80]
        value = value.strip()[:200]
        if name and value:
            specs.append({"name": name, "value": value})
        if len(specs) >= 40:
            break
    return specs


def specifications_to_text(specs) -> str:
    if not specs:
        return ""
    lines = []
    for item in specs:
        if isinstance(item, dict) and item.get("name"):
            lines.append(f"{item['name']}: {item.get('value', '')}")
    return "\n".join(lines)


def parse_attributes(text: str | None) -> dict:
    """ورودی: «رنگ=مشکی، سایز=L» یا «رنگ:مشکی|سایز:L»."""
    attributes: dict[str, str] = {}
    if not text:
        return attributes
    raw = str(text).replace("،", ",").replace("|", ",")
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, _, value = part.partition("=")
        elif ":" in part:
            name, _, value = part.partition(":")
        else:
            continue
        name = name.strip()[:40]
        value = value.strip()[:80]
        if name and value:
            attributes[name] = value
        if len(attributes) >= 8:
            break
    return attributes


def attributes_to_text(attributes) -> str:
    if not attributes:
        return ""
    return "، ".join(f"{key}={value}" for key, value in attributes.items())


def slug_is_reserved(slug: str) -> bool:
    reserved = {
        "admin",
        "account",
        "api",
        "auth",
        "cart",
        "checkout",
        "payment",
        "media",
        "setup",
        "static",
        "health",
        "download",
        "products",
        "category",
        "search",
        "blog",
        "page",
        "compare",
        "wishlist",
        "tickets",
    }
    return slug in reserved


def recalc_rating(product_id: int) -> None:
    """به‌روزرسانی میانگین امتیاز محصول پس از تأیید/رد نظر."""
    product = db.session.get(Product, product_id)
    if product is None:
        return
    row = (
        db.session.query(func.coalesce(func.sum(Review.rating), 0), func.count(Review.id))
        .filter(Review.product_id == product_id, Review.status == "approved")
        .first()
    )
    product.rating_sum = int(row[0] or 0)
    product.rating_count = int(row[1] or 0)
    db.session.commit()


def default_variant_for(product: Product) -> ProductVariant | None:
    return product.default_variant if product else None


def stock_status_label(product: Product) -> tuple[str, str]:
    """(کلاس CSS، متن) برای نمایش وضعیت موجودی."""
    if product.is_digital:
        return "in-stock", "موجود — تحویل فوری"
    total = product.total_stock
    if total <= 0:
        return "out-of-stock", "ناموجود"
    if total <= 5:
        return "low-stock", f"تنها {total} عدد در انبار"
    return "in-stock", "موجود در انبار"


def generate_variant_sku(product: Product, index: int) -> str:
    base = product.sku or f"P{product.id}"
    return unique_sku(ProductVariant, f"{base}-{index:02d}")


def category_options(exclude_id: int | None = None, parent_id: int | None = None) -> list[Category]:
    query = db.session.query(Category).order_by(Category.sort_order, Category.name)
    if exclude_id:
        query = query.filter(Category.id != exclude_id)
    if parent_id is not None:
        query = query.filter(Category.parent_id == (parent_id or None))
    return query.all()


def sanitize_seo(text: str | None, max_length: int = 300) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()[:max_length]
