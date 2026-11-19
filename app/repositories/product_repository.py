"""کوئری‌های محصول: جستجو، فیلتر، مرتب‌سازی و صفحه‌بندی در سطح دیتابیس."""
from __future__ import annotations

from sqlalchemy import and_, exists, func, or_, select

from app.extensions import db
from app.models import Category, Product, ProductVariant, Review
from app.utils.pagination import paginate
from app.utils.security import persian_search_normalize

SORT_OPTIONS = {
    "newest": "جدیدترین",
    "cheapest": "ارزان‌ترین",
    "expensive": "گران‌ترین",
    "bestseller": "پرفروش‌ترین",
    "discount": "بیشترین تخفیف",
    "rating": "محبوب‌ترین",
}


def category_ids_with_children(category: Category | None) -> list[int]:
    if category is None:
        return []
    ids = [category.id]
    frontier = [category.id]
    for _ in range(6):
        children = (
            db.session.query(Category.id)
            .filter(Category.parent_id.in_(frontier), Category.is_active.is_(True))
            .all()
        )
        frontier = [row[0] for row in children]
        if not frontier:
            break
        ids.extend(frontier)
    return ids


def _available_condition():
    """شرط وجود حداقلی موجودی یا دیجیتال بودن."""
    variant_available = exists().where(
        and_(
            ProductVariant.product_id == Product.id,
            ProductVariant.is_active.is_(True),
            or_(ProductVariant.stock_quantity > 0, Product.product_type == "digital"),
        )
    )
    return variant_available


def build_query(
    *,
    query: str | None = None,
    category: Category | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    in_stock: bool = False,
    featured: bool | None = None,
    discounted: bool = False,
    product_type: str | None = None,
    sort: str = "newest",
    include_inactive: bool = False,
):
    stmt = select(Product).where(Product.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))

    if query:
        normalized = persian_search_normalize(query)
        like = f"%{normalized}%"
        stmt = stmt.where(
            or_(
                func.replace(Product.name, "ي", "ی").like(like),
                Product.sku.like(like),
                Product.short_description.like(like),
                Product.description.like(like),
            )
        )

    if category is not None:
        stmt = stmt.where(Product.category_id.in_(category_ids_with_children(category)))

    if product_type in {"physical", "digital"}:
        stmt = stmt.where(Product.product_type == product_type)

    if featured is not None:
        stmt = stmt.where(Product.is_featured.is_(featured))

    if in_stock:
        stmt = stmt.where(_available_condition())

    if discounted:
        stmt = stmt.where(
            exists().where(
                and_(
                    ProductVariant.product_id == Product.id,
                    ProductVariant.is_active.is_(True),
                    ProductVariant.compare_at_price > ProductVariant.price,
                )
            )
        )

    # فیلتر قیمت بر مبنای قیمت تنوع‌ها
    if min_price is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    ProductVariant.product_id == Product.id,
                    ProductVariant.is_active.is_(True),
                    ProductVariant.price >= min_price,
                )
            )
        )
    if max_price is not None:
        stmt = stmt.where(
            exists().where(
                and_(
                    ProductVariant.product_id == Product.id,
                    ProductVariant.is_active.is_(True),
                    ProductVariant.price <= max_price,
                )
            )
        )

    if sort == "cheapest":
        min_price_subq = (
            select(func.min(ProductVariant.price))
            .where(ProductVariant.product_id == Product.id, ProductVariant.is_active.is_(True))
            .scalar_subquery()
        )
        stmt = stmt.order_by(min_price_subq.asc().nulls_last(), Product.id.desc())
    elif sort == "expensive":
        max_price_subq = (
            select(func.max(ProductVariant.price))
            .where(ProductVariant.product_id == Product.id, ProductVariant.is_active.is_(True))
            .scalar_subquery()
        )
        stmt = stmt.order_by(max_price_subq.desc().nulls_last(), Product.id.desc())
    elif sort == "bestseller":
        stmt = stmt.order_by(Product.sold_count.desc(), Product.created_at.desc())
    elif sort == "rating":
        stmt = stmt.order_by(Product.rating_count.desc(), Product.rating_sum.desc(), Product.created_at.desc())
    elif sort == "discount":
        stmt = stmt.order_by(Product.is_featured.desc(), Product.sold_count.desc())
    else:
        stmt = stmt.order_by(Product.created_at.desc(), Product.id.desc())
    return stmt


def search_page(args, per_page: int = 12):
    """صفحه‌بندی نتایج فیلتر/جستجو."""
    category = None
    category_slug = (args.get("category") or "").strip()
    if category_slug:
        category = db.session.query(Category).filter(Category.slug == category_slug).first()

    def _int(value):
        try:
            return int(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    stmt = build_query(
        query=(args.get("q") or "").strip() or None,
        category=category,
        min_price=_int(args.get("min_price")),
        max_price=_int(args.get("max_price")),
        in_stock=args.get("in_stock") in {"1", "on", "true"},
        featured=True if args.get("featured") in {"1", "on"} else None,
        discounted=args.get("discounted") in {"1", "on"},
        product_type=args.get("type") if args.get("type") in {"physical", "digital"} else None,
        sort=(args.get("sort") or "newest"),
    )
    page = paginate(stmt, per_page=per_page)
    return page, category


def featured_products(limit: int = 8) -> list[Product]:
    return list(
        db.session.execute(
            build_query(featured=True, in_stock=False, sort="newest").limit(limit)
        ).scalars()
    )


def newest_products(limit: int = 8) -> list[Product]:
    return list(
        db.session.execute(build_query(sort="newest").limit(limit)).scalars()
    )


def bestseller_products(limit: int = 8) -> list[Product]:
    return list(
        db.session.execute(build_query(sort="bestseller").limit(limit)).scalars()
    )


def discounted_products(limit: int = 8) -> list[Product]:
    return list(
        db.session.execute(build_query(discounted=True, sort="newest").limit(limit)).scalars()
    )


def related_products(product: Product, limit: int = 4) -> list[Product]:
    if product is None:
        return []
    stmt = (
        select(Product)
        .where(
            Product.deleted_at.is_(None),
            Product.is_active.is_(True),
            Product.id != product.id,
        )
    )
    if product.category_id:
        stmt = stmt.where(Product.category_id == product.category_id)
    stmt = stmt.order_by(Product.sold_count.desc(), Product.created_at.desc()).limit(limit)
    rows = list(db.session.execute(stmt).scalars())
    if len(rows) >= limit:
        return rows
    extra = list(
        db.session.execute(
            select(Product)
            .where(
                Product.deleted_at.is_(None),
                Product.is_active.is_(True),
                Product.id != product.id,
                Product.id.notin_([p.id for p in rows] or [0]),
            )
            .order_by(Product.sold_count.desc())
            .limit(limit - len(rows))
        ).scalars()
    )
    return rows + extra


def get_by_slug(slug: str, include_inactive: bool = False) -> Product | None:
    stmt = select(Product).where(Product.slug == slug, Product.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    return db.session.execute(stmt).scalars().first()


def products_by_ids(ids: list[int]) -> list[Product]:
    if not ids:
        return []
    rows = db.session.execute(
        select(Product).where(
            Product.id.in_(ids), Product.deleted_at.is_(None), Product.is_active.is_(True)
        )
    ).scalars().all()
    mapping = {p.id: p for p in rows}
    return [mapping[i] for i in ids if i in mapping]


def approved_reviews(product_id: int, limit: int = 20):
    return (
        db.session.query(Review)
        .filter(Review.product_id == product_id, Review.status == "approved")
        .order_by(Review.created_at.desc())
        .limit(limit)
        .all()
    )


def review_stats(product_id: int) -> dict:
    rows = (
        db.session.query(Review.rating, func.count(Review.id))
        .filter(Review.product_id == product_id, Review.status == "approved")
        .group_by(Review.rating)
        .all()
    )
    distribution = {rating: count for rating, count in rows}
    total = sum(distribution.values())
    average = (
        round(sum(rating * count for rating, count in distribution.items()) / total, 1) if total else 0
    )
    return {"total": total, "average": average, "distribution": distribution}


def price_bounds() -> tuple[int, int]:
    row = db.session.query(func.min(ProductVariant.price), func.max(ProductVariant.price)).filter(
        ProductVariant.is_active.is_(True)
    ).first()
    return int(row[0] or 0), int(row[1] or 0)
