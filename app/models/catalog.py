"""محصولات، دسته‌بندی‌ها، تنوع‌ها، رسانه‌ها و دارایی‌های دیجیتال."""
from __future__ import annotations

from typing import Optional

from app.extensions import db
from app.models.base import SoftDeleteMixin, TimestampMixin
from app.utils.money import discount_percent


class Category(db.Model, TimestampMixin):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name = db.Column(db.String(150), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)

    parent = db.relationship("Category", remote_side=[id], backref=db.backref("children", lazy="selectin"))
    products = db.relationship("Product", back_populates="category", lazy="dynamic")

    __table_args__ = (
        db.Index("ix_categories_active_sort", "is_active", "sort_order"),
        db.Index("ix_categories_parent_sort", "parent_id", "sort_order"),
    )

    @property
    def image_url(self) -> Optional[str]:
        from app.services.media_service import public_url

        return public_url(self.image) if self.image else None

    @property
    def ancestors(self) -> list["Category"]:
        chain: list[Category] = []
        node = self.parent
        guard = 0
        while node is not None and guard < 10:
            chain.append(node)
            node = node.parent
            guard += 1
        return list(reversed(chain))

    @property
    def breadcrumb(self) -> list["Category"]:
        return self.ancestors + [self]

    @property
    def product_count(self) -> int:
        from app.models.catalog import Product

        return (
            db.session.query(db.func.count(Product.id))
            .filter(Product.category_id == self.id, Product.is_active.is_(True), Product.deleted_at.is_(None))
            .scalar()
            or 0
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Category {self.slug}>"


class Product(db.Model, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name = db.Column(db.String(220), nullable=False, index=True)
    slug = db.Column(db.String(240), unique=True, nullable=False, index=True)
    sku = db.Column(db.String(80), unique=True, nullable=True, index=True)
    description = db.Column(db.Text, nullable=True)
    short_description = db.Column(db.String(400), nullable=True)
    product_type = db.Column(db.String(20), default="physical", nullable=False, index=True)
    base_price = db.Column(db.Integer, default=0, nullable=False)
    base_cost = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    is_featured = db.Column(db.Boolean, default=False, nullable=False, index=True)
    specifications = db.Column(db.JSON, default=list, nullable=False)
    seo_title = db.Column(db.String(200), nullable=True)
    seo_description = db.Column(db.String(300), nullable=True)
    weight = db.Column(db.Integer, default=0, nullable=False)
    rating_sum = db.Column(db.Integer, default=0, nullable=False)
    rating_count = db.Column(db.Integer, default=0, nullable=False)
    sold_count = db.Column(db.Integer, default=0, nullable=False)
    view_count = db.Column(db.Integer, default=0, nullable=False)

    category = db.relationship("Category", back_populates="products", lazy="joined")
    variants = db.relationship(
        "ProductVariant",
        back_populates="product",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="ProductVariant.sort_order, ProductVariant.id",
    )
    media = db.relationship(
        "ProductMedia",
        back_populates="product",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="ProductMedia.sort_order, ProductMedia.id",
    )
    digital_assets = db.relationship(
        "DigitalAsset",
        back_populates="product",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.Index("ix_products_active_created", "is_active", "created_at"),
        db.Index("ix_products_category_active", "category_id", "is_active"),
        db.Index("ix_products_featured_active", "is_featured", "is_active"),
        db.Index("ix_products_sold", "sold_count"),
    )

    # --- کمکی‌ها ---
    @property
    def is_digital(self) -> bool:
        return self.product_type == "digital"

    @property
    def active_variants(self) -> list["ProductVariant"]:
        return [variant for variant in self.variants if variant.is_active]

    @property
    def images(self) -> list["ProductMedia"]:
        return [m for m in self.media if m.is_active and m.media_type == "image"]

    @property
    def videos(self) -> list["ProductMedia"]:
        return [m for m in self.media if m.is_active and m.media_type == "video"]

    @property
    def primary_image(self) -> Optional["ProductMedia"]:
        images = self.images
        return images[0] if images else None

    @property
    def image_url(self) -> Optional[str]:
        media = self.primary_image
        return media.display_url if media else None

    @property
    def default_variant(self) -> Optional["ProductVariant"]:
        variants = self.active_variants
        if not variants:
            return None
        for variant in variants:
            if variant.is_default:
                return variant
        return min(variants, key=lambda v: v.effective_price)

    @property
    def price(self) -> int:
        variant = self.default_variant
        return variant.effective_price if variant else int(self.base_price or 0)

    @property
    def compare_at_price(self) -> int:
        variant = self.default_variant
        return (variant.compare_at_price or 0) if variant else 0

    @property
    def discount_percent(self) -> int:
        return discount_percent(self.price, self.compare_at_price)

    @property
    def price_range(self) -> tuple[int, int]:
        prices = [v.effective_price for v in self.active_variants] or [int(self.base_price or 0)]
        return min(prices), max(prices)

    @property
    def total_stock(self) -> int:
        return sum(max(0, v.stock_quantity or 0) for v in self.active_variants)

    @property
    def in_stock(self) -> bool:
        return self.total_stock > 0 or self.is_digital

    @property
    def has_variants(self) -> bool:
        return len(self.active_variants) > 1

    @property
    def rating_avg(self) -> float:
        if not self.rating_count:
            return 0.0
        return round(self.rating_sum / self.rating_count, 1)

    @property
    def is_low_stock(self) -> bool:
        return 0 < self.total_stock <= 5

    @property
    def variant_attributes(self) -> dict:
        """یافتن نام ویژگی‌های مشترک بین تنوع‌ها برای فرم انتخاب."""
        names: list[str] = []
        for variant in self.active_variants:
            for key in (variant.attributes_json or {}):
                if key not in names:
                    names.append(key)
        return names

    @property
    def specifications_list(self) -> list[dict]:
        items = self.specifications or []
        return [item for item in items if isinstance(item, dict) and item.get("name")]

    @property
    def seo_title_value(self) -> str:
        return self.seo_title or self.name

    @property
    def seo_description_value(self) -> str:
        return self.seo_description or self.short_description or ""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.slug}>"


class ProductVariant(db.Model, TimestampMixin):
    __tablename__ = "product_variants"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sku = db.Column(db.String(80), unique=True, nullable=False, index=True)
    attributes_json = db.Column(db.JSON, default=dict, nullable=False)
    price = db.Column(db.Integer, default=0, nullable=False)
    compare_at_price = db.Column(db.Integer, default=0, nullable=False)
    cost_price = db.Column(db.Integer, default=0, nullable=False)
    stock_quantity = db.Column(db.Integer, default=0, nullable=False)
    low_stock_threshold = db.Column(db.Integer, default=3, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_default = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    product = db.relationship("Product", back_populates="variants")
    reservations = db.relationship("InventoryReservation", back_populates="variant", lazy="dynamic")

    @property
    def effective_price(self) -> int:
        if self.price:
            return int(self.price)
        return int(self.product.base_price or 0) if self.product else 0

    @property
    def discount_percent(self) -> int:
        return discount_percent(self.effective_price, self.compare_at_price)

    @property
    def label(self) -> str:
        attrs = self.attributes_json or {}
        if not attrs:
            return "پیش‌فرض"
        return " / ".join(f"{value}" for value in attrs.values())

    @property
    def label_with_names(self) -> str:
        attrs = self.attributes_json or {}
        return "، ".join(f"{key}: {value}" for key, value in attrs.items()) or "پیش‌فرض"

    @property
    def is_low_stock(self) -> bool:
        return (self.stock_quantity or 0) <= (self.low_stock_threshold or 0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Variant {self.sku} stock={self.stock_quantity}>"


class ProductMedia(db.Model, TimestampMixin):
    __tablename__ = "product_media"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_id = db.Column(
        db.Integer, db.ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    media_type = db.Column(db.String(20), default="image", nullable=False)
    storage_type = db.Column(db.String(20), default="local", nullable=False)
    file_path = db.Column(db.String(255), nullable=True)
    external_url = db.Column(db.String(500), nullable=True)
    thumbnail_path = db.Column(db.String(255), nullable=True)
    alt_text = db.Column(db.String(200), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    product = db.relationship("Product", back_populates="media")
    variant = db.relationship("ProductVariant")

    __table_args__ = (db.Index("ix_product_media_product_type", "product_id", "media_type", "sort_order"),)

    @property
    def display_url(self) -> Optional[str]:
        from app.services.media_service import public_url

        if self.storage_type == "external":
            return self.external_url or None
        return public_url(self.file_path)

    @property
    def thumbnail_url(self) -> Optional[str]:
        from app.services.media_service import public_url

        if self.thumbnail_path:
            return public_url(self.thumbnail_path)
        return self.display_url

    @property
    def video_embed_url(self) -> Optional[str]:
        """تبدیل لینک ویدئوی خارجی (آپارات/یوتیوب) به لینک قابل embed."""
        import re

        url = self.external_url or ""
        match = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{6,})", url)
        if match:
            return f"https://www.youtube.com/embed/{match.group(1)}"
        match = re.search(r"aparat\.com/v/([\w-]+)", url)
        if match:
            return f"https://www.aparat.com/video/video/embed/videohash/{match.group(1)}/vt/frame"
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Media {self.media_type} {self.id}>"


class DigitalAsset(db.Model, TimestampMixin):
    __tablename__ = "digital_assets"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_id = db.Column(
        db.Integer, db.ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    delivery_type = db.Column(db.String(20), default="file", nullable=False)
    file_path = db.Column(db.String(255), nullable=True)
    external_url = db.Column(db.String(500), nullable=True)
    license_code = db.Column(db.String(255), nullable=True)
    download_limit = db.Column(db.Integer, default=5, nullable=False)
    download_expiry_days = db.Column(db.Integer, default=0, nullable=False)
    title = db.Column(db.String(180), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    product = db.relationship("Product", back_populates="digital_assets")
    variant = db.relationship("ProductVariant")

    @property
    def delivery_label(self) -> str:
        return {"file": "دانلود فایل", "link": "لینک دانلود", "license": "کد لایسنس"}.get(
            self.delivery_type, self.delivery_type
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DigitalAsset {self.delivery_type} product={self.product_id}>"
