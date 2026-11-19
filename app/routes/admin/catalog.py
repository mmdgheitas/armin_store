"""مدیریت محصولات، تنوع‌ها، رسانه‌ها، دارایی‌های دیجیتال، دسته‌بندی‌ها و انبار."""
from __future__ import annotations

from sqlalchemy import or_

from flask import (
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from app.constants import DIGITAL_DELIVERY_TYPES, INVENTORY_MOVEMENT_TYPES, PRODUCT_TYPES
from app.decorators import permission_required
from app.extensions import db
from app.models import (
    Category,
    DigitalAsset,
    InventoryMovement,
    OrderItem,
    Product,
    ProductMedia,
    ProductVariant,
)
from app.repositories import product_repository as repo
from app.routes.admin import admin_bp
from app.routes.admin.helpers import bool_field, int_field, log_admin, text_field
from app.services import inventory_service, media_service, product_service, settings_service
from app.utils.dates import utcnow
from app.utils.files import FileValidationError
from app.utils.pagination import paginate
from app.utils.security import slugify

# ---------------------------------------------------------------------------
# محصولات
# ---------------------------------------------------------------------------


@admin_bp.get("/products")
@permission_required("products.view")
def products():
    query_text = (request.args.get("q") or "").strip()
    status = request.args.get("status", "all")
    category_id = request.args.get("category", type=int)

    stmt = db.session.query(Product).filter(Product.deleted_at.is_(None))
    if query_text:
        like = f"%{query_text}%"
        stmt = stmt.filter(or_(Product.name.like(like), Product.sku.like(like)))
    if status == "active":
        stmt = stmt.filter(Product.is_active.is_(True))
    elif status == "inactive":
        stmt = stmt.filter(Product.is_active.is_(False))
    elif status == "featured":
        stmt = stmt.filter(Product.is_featured.is_(True))
    elif status == "low_stock":
        stmt = stmt.filter(
            Product.id.in_(
                db.session.query(ProductVariant.product_id).filter(
                    ProductVariant.is_active.is_(True),
                    ProductVariant.stock_quantity <= ProductVariant.low_stock_threshold,
                )
            )
        )
    if category_id:
        stmt = stmt.filter(Product.category_id == category_id)

    page = paginate(stmt.order_by(Product.created_at.desc()), per_page=20)
    categories = db.session.query(Category).order_by(Category.name).all()
    return render_template(
        "admin/products/list.html",
        page=page,
        categories=categories,
        q=query_text,
        status=status,
        category_id=category_id,
    )


def _product_form_data(product: Product | None = None) -> dict:
    return {
        "name": product.name if product else "",
        "slug": product.slug if product else "",
        "sku": product.sku if product else "",
        "category_id": product.category_id if product else None,
        "product_type": product.product_type if product else "physical",
        "short_description": product.short_description if product else "",
        "description": product.description if product else "",
        "base_price": product.base_price if product else 0,
        "base_cost": product.base_cost if product else 0,
        "is_active": product.is_active if product else True,
        "is_featured": product.is_featured if product else False,
        "seo_title": product.seo_title if product else "",
        "seo_description": product.seo_description if product else "",
        "specifications": product_service.specifications_to_text(product.specifications if product else []),
        "weight": product.weight if product else 0,
    }


@admin_bp.route("/products/create", methods=["GET", "POST"])
@permission_required("products.manage")
def product_create():
    categories = db.session.query(Category).order_by(Category.sort_order, Category.name).all()
    if request.method == "POST":
        product, errors = _save_product(None)
        if product is not None:
            flash("محصول با موفقیت ایجاد شد.", "success")
            return redirect(url_for("admin.product_edit", product_id=product.id))
        for message in errors:
            flash(message, "error")
    return render_template(
        "admin/products/form.html",
        product=None,
        form=_product_form_data(),
        categories=categories,
        product_types=PRODUCT_TYPES,
        variants_form=None,
        media_items=[],
        assets=[],
        digital_types=DIGITAL_DELIVERY_TYPES,
    )


@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@permission_required("products.manage")
def product_edit(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        abort(404)
    if request.method == "POST":
        updated, errors = _save_product(product)
        if updated is not None:
            flash("محصول به‌روزرسانی شد.", "success")
            return redirect(url_for("admin.product_edit", product_id=product.id))
        for message in errors:
            flash(message, "error")
    categories = db.session.query(Category).order_by(Category.sort_order, Category.name).all()
    form = _product_form_data(product)
    if request.method == "POST":
        for key in form:
            if key in request.form:
                form[key] = request.form.get(key)
        form["is_active"] = bool_field("is_active")
        form["is_featured"] = bool_field("is_featured")
    return render_template(
        "admin/products/form.html",
        product=product,
        form=form,
        categories=categories,
        product_types=PRODUCT_TYPES,
        variants_form=product.variants,
        media_items=product.media,
        assets=product.digital_assets,
        digital_types=DIGITAL_DELIVERY_TYPES,
    )


def _save_product(product: Product | None):
    """اعتبارسنجی و ذخیره محصول همراه با تنوع‌ها."""
    form = request.form
    errors: list[str] = []
    name = text_field("name", max_length=220)
    if len(name) < 2:
        errors.append("نام محصول را وارد کنید.")
    category_id = int_field("category_id") or None
    if category_id and db.session.get(Category, category_id) is None:
        errors.append("دسته‌بندی انتخاب‌شده معتبر نیست.")
    product_type = form.get("product_type", "physical")
    if product_type not in {"physical", "digital"}:
        product_type = "physical"

    base_price = int_field("base_price")
    base_cost = int_field("base_cost")
    if base_price < 0 or base_cost < 0:
        errors.append("قیمت نمی‌تواند منفی باشد.")

    slug_input = text_field("slug")
    slug = slug_input or slugify(name)
    base_slug = slug
    existing_slug = (
        db.session.query(Product.id)
        .filter(Product.slug == slug, Product.id != (product.id if product else 0))
        .first()
    )
    if existing_slug:
        slug = product_service.unique_slug(Product, base_slug, product.id if product else None)
    if product_service.slug_is_reserved(slug) and product is None:
        slug = f"{slug}-product"

    sku = text_field("sku").upper()
    if not sku:
        sku = product_service.unique_sku(Product, f"P-{slug[:20].upper()}", product.id if product else None)
    else:
        duplicate = (
            db.session.query(Product.id)
            .filter(Product.sku == sku, Product.id != (product.id if product else 0))
            .first()
        )
        if duplicate:
            errors.append("این SKU قبلاً برای محصول دیگری ثبت شده است.")

    if errors:
        return None, errors

    from app.utils.security import sanitize_html

    specs = product_service.parse_specifications(form.get("specifications"))
    if product is None:
        product = Product(created_at=utcnow(), updated_at=utcnow())
        db.session.add(product)
        log_action = "product.create"
    else:
        log_action = "product.update"
    old_snapshot = {
        "name": product.name,
        "price": product.base_price,
        "is_active": product.is_active,
    }
    product.name = name
    product.slug = slug
    product.sku = sku
    product.category_id = category_id
    product.product_type = product_type
    product.short_description = text_field("short_description", max_length=400) or None
    product.description = sanitize_html(form.get("description"))
    product.base_price = base_price
    product.base_cost = base_cost
    product.is_active = bool_field("is_active")
    product.is_featured = bool_field("is_featured")
    product.seo_title = text_field("seo_title", max_length=200) or None
    product.seo_description = text_field("seo_description", max_length=300) or None
    product.specifications = specs
    product.weight = int_field("weight")
    product.updated_at = utcnow()
    db.session.flush()

    errors.extend(_save_variants(product, base_price, base_cost))
    if errors:
        db.session.rollback()
        return None, errors

    db.session.commit()
    log_admin(
        log_action,
        "product",
        product.id,
        old=old_snapshot,
        new={"name": product.name, "slug": product.slug, "price": product.base_price},
    )
    return product, []


def _save_variants(product: Product, base_price: int, base_cost: int) -> list[str]:
    """ذخیره تنوع‌ها از فرم داینامیک (آرایه‌های هم‌اندیس)."""
    form = request.form
    ids = form.getlist("variant_id[]")
    skus = form.getlist("variant_sku[]")
    attrs = form.getlist("variant_attrs[]")
    prices = form.getlist("variant_price[]")
    compares = form.getlist("variant_compare[]")
    costs = form.getlist("variant_cost[]")
    stocks = form.getlist("variant_stock[]")
    thresholds = form.getlist("variant_threshold[]")

    if not ids:
        # محصول بدون تنوع: یک تنوع پیش‌فرض با قیمت پایه ساخته می‌شود
        if not product.variants:
            variant = ProductVariant(
                product_id=product.id,
                sku=product_service.unique_sku(ProductVariant, product.sku or f"P{product.id}"),
                attributes_json={},
                price=base_price,
                compare_at_price=0,
                cost_price=base_cost,
                stock_quantity=0 if product.is_digital else int_field("initial_stock"),
                low_stock_threshold=settings_service.get_int("low_stock_threshold", 3),
                is_active=True,
                is_default=True,
                sort_order=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.session.add(variant)
            db.session.flush()
            if variant.stock_quantity:
                inventory_service.record_movement(
                    variant, "adjust", variant.stock_quantity, 0, variant.stock_quantity,
                    reason="ثبت اولیه موجودی هنگام ایجاد محصول", admin_id=g.user.id,
                )
        return []

    errors: list[str] = []
    seen_skus: set[str] = set()
    kept_ids: set[int] = set()
    seen_attrs: set[str] = set()

    for index, variant_id in enumerate(ids):
        def _get(list_values, default=""):
            return list_values[index] if index < len(list_values) else default

        attributes = product_service.parse_attributes(_get(attrs))
        attr_key = "|".join(sorted(f"{k}={v}" for k, v in attributes.items()))
        if attr_key and attr_key in seen_attrs:
            errors.append("ترکیب ویژگی‌های تکراری در تنوع‌ها مجاز نیست.")
        seen_attrs.add(attr_key)

        sku = (_get(skus) or "").strip().upper()
        try:
            price = int(_get(prices, "0") or 0)
            compare = int(_get(compares, "0") or 0)
            cost = int(_get(costs, "0") or 0)
            stock = int(_get(stocks, "0") or 0)
            threshold = int(_get(thresholds, "3") or 3)
        except ValueError:
            errors.append("مقادیر عددی تنوع معتبر نیست.")
            continue
        if price < 0 or compare < 0 or cost < 0 or stock < 0:
            errors.append("مقادیر تنوع نمی‌توانند منفی باشند.")
            continue

        variant = db.session.get(ProductVariant, int(variant_id)) if variant_id.isdigit() else None
        if variant is not None and variant.product_id != product.id:
            variant = None

        if not sku:
            sku = product_service.unique_sku(
                ProductVariant, f"{product.sku or 'P'}-{index + 1:02d}", variant.id if variant else None
            )
        if sku in seen_skus:
            errors.append(f"SKU تکراری «{sku}» در تنوع‌ها.")
        duplicate_sku = (
            db.session.query(ProductVariant.id)
            .filter(ProductVariant.sku == sku, ProductVariant.id != (variant.id if variant else 0))
            .first()
        )
        if duplicate_sku:
            errors.append(f"SKU «{sku}» قبلاً استفاده شده است.")
        seen_skus.add(sku)

        if variant is None:
            variant = ProductVariant(
                product_id=product.id,
                created_at=utcnow(),
                updated_at=utcnow(),
                is_active=True,
                stock_quantity=0,
            )
            db.session.add(variant)
            previous_stock = 0
        else:
            previous_stock = variant.stock_quantity

        variant.sku = sku
        variant.attributes_json = attributes
        variant.price = price
        variant.compare_at_price = compare
        variant.cost_price = cost
        variant.low_stock_threshold = threshold
        variant.is_active = True
        variant.sort_order = index
        variant.updated_at = utcnow()
        if product.is_digital:
            variant.stock_quantity = stock
        else:
            variant.stock_quantity = stock
        db.session.flush()
        kept_ids.add(variant.id)

        if previous_stock != stock:
            inventory_service.record_movement(
                variant,
                "adjust",
                stock - previous_stock,
                previous_stock,
                stock,
                reason="ویرایش موجودی از فرم محصول",
                admin_id=g.user.id,
                unit_cost=cost,
            )

    # حذف تنوع‌هایی که از فرم حذف شده‌اند (فقط اگر در سفارش استفاده نشده باشند)
    for variant in list(product.variants):
        if variant.id not in kept_ids:
            used = (
                db.session.query(OrderItem.id)
                .filter(OrderItem.variant_id == variant.id)
                .first()
            )
            if used:
                variant.is_active = False
            else:
                db.session.delete(variant)

    defaults = form.getlist("variant_default[]")
    default_index = 0
    if defaults:
        for value in defaults:
            if value.isdigit():
                default_index = int(value)
                break
    active_variants = [v for v in product.variants if v.id in kept_ids]
    for variant in active_variants:
        variant.is_default = variant.id == (active_variants[default_index].id if active_variants and default_index < len(active_variants) else None)
    db.session.flush()
    return errors


@admin_bp.post("/products/<int:product_id>/toggle")
@permission_required("products.manage")
def product_toggle(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    product.is_active = not product.is_active
    product.updated_at = utcnow()
    db.session.commit()
    log_admin("product.toggle", "product", product.id, new={"is_active": product.is_active})
    flash("وضعیت انتشار محصول تغییر کرد.", "success")
    return redirect(request.referrer or url_for("admin.products"))


@admin_bp.post("/products/<int:product_id>/featured")
@permission_required("products.manage")
def product_featured(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    product.is_featured = not product.is_featured
    db.session.commit()
    log_admin("product.featured", "product", product.id, new={"is_featured": product.is_featured})
    flash("وضعیت «ویژه» تغییر کرد.", "success")
    return redirect(request.referrer or url_for("admin.products"))


@admin_bp.post("/products/<int:product_id>/delete")
@permission_required("products.manage")
def product_delete(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    product.soft_delete()
    product.is_active = False
    for variant in product.variants:
        variant.is_active = False
    db.session.commit()
    log_admin("product.delete", "product", product.id, old={"name": product.name})
    flash("محصول آرشیو شد. سوابق سفارش‌ها حفظ شده است.", "success")
    return redirect(url_for("admin.products"))


# --- رسانه محصول ---


@admin_bp.post("/products/<int:product_id>/media")
@permission_required("products.manage")
def product_media_upload(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    media_type = request.form.get("media_type", "image")
    if media_type not in {"image", "video"}:
        media_type = "image"
    external_url = text_field("external_url", max_length=500)
    file = request.files.get("file")
    sort_order = int_field("sort_order")

    if media_type == "video" and external_url:
        db.session.add(
            ProductMedia(
                product_id=product.id,
                media_type="video",
                storage_type="external",
                external_url=external_url,
                thumbnail_path=None,
                alt_text=text_field("alt_text", max_length=200) or None,
                sort_order=sort_order,
                is_active=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
        db.session.commit()
        log_admin("product.media_add", "product", product.id, new={"external_url": external_url})
        flash("ویدئوی خارجی اضافه شد.", "success")
        return redirect(url_for("admin.product_edit", product_id=product.id))

    if file is None or not file.filename:
        flash("فایل یا لینک خارجی را وارد کنید.", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))

    max_mb = (
        settings_service.get_int("image_max_mb", 5)
        if media_type == "image"
        else settings_service.get_int("video_max_mb", 50)
    )
    try:
        saved = media_service.save_upload(
            file, f"products/{product.id}", kind=media_type, max_mb=max_mb
        )
    except FileValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))

    db.session.add(
        ProductMedia(
            product_id=product.id,
            media_type=media_type,
            storage_type="local",
            file_path=saved["path"],
            alt_text=text_field("alt_text", max_length=200) or product.name[:200],
            sort_order=sort_order,
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.session.commit()
    log_admin("product.media_add", "product", product.id, new={"path": saved["path"]})
    flash("فایل با موفقیت اضافه شد.", "success")
    return redirect(url_for("admin.product_edit", product_id=product.id))


@admin_bp.post("/products/<int:product_id>/media/<int:media_id>/delete")
@permission_required("products.manage")
def product_media_delete(product_id: int, media_id: int):
    media = db.session.get(ProductMedia, media_id)
    if media is None or media.product_id != product_id:
        abort(404)
    if media.storage_type == "local" and media.file_path:
        media_service.delete_public(media.file_path)
    db.session.delete(media)
    db.session.commit()
    log_admin("product.media_delete", "product", product_id, old={"media_id": media_id})
    flash("رسانه حذف شد.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


@admin_bp.post("/products/<int:product_id>/media/<int:media_id>/sort")
@permission_required("products.manage")
def product_media_sort(product_id: int, media_id: int):
    media = db.session.get(ProductMedia, media_id)
    if media is None or media.product_id != product_id:
        abort(404)
    media.sort_order = int_field("sort_order")
    db.session.commit()
    flash("ترتیب رسانه به‌روزرسانی شد.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


# --- دارایی دیجیتال ---


@admin_bp.post("/products/<int:product_id>/assets")
@permission_required("products.manage")
def product_asset_create(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    delivery_type = request.form.get("delivery_type", "file")
    if delivery_type not in {"file", "link", "license"}:
        delivery_type = "file"
    variant_id = int_field("variant_id") or None
    file = request.files.get("file")
    file_path = None
    if file is not None and file.filename:
        try:
            saved = media_service.save_upload(
                file,
                f"digital/{product.id}",
                kind="asset",
                max_mb=max(settings_service.get_int("video_max_mb", 50), 50),
                private=True,
            )
            file_path = saved["path"]
        except FileValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("admin.product_edit", product_id=product.id))

    external_url = text_field("external_url", max_length=500)
    license_code = request.form.get("license_code", "")
    if delivery_type == "file" and not file_path:
        flash("برای تحویل فایلی، فایل را بارگذاری کنید.", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))
    if delivery_type == "link" and not external_url:
        flash("لینک دانلود خارجی را وارد کنید.", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))
    if delivery_type == "license" and not license_code.strip():
        flash("کد(های) لایسنس را وارد کنید (هر کد در یک خط).", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))

    db.session.add(
        DigitalAsset(
            product_id=product.id,
            variant_id=variant_id,
            delivery_type=delivery_type,
            file_path=file_path,
            external_url=external_url or None,
            license_code=license_code[:4000] or None,
            download_limit=max(1, int_field("download_limit", 5)),
            download_expiry_days=max(0, int_field("download_expiry_days", 0)),
            title=text_field("title", max_length=180) or None,
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )
    db.session.commit()
    log_admin("product.asset_create", "product", product.id, new={"type": delivery_type})
    flash("دارایی دیجیتال اضافه شد.", "success")
    return redirect(url_for("admin.product_edit", product_id=product.id))


@admin_bp.post("/products/<int:product_id>/assets/<int:asset_id>/delete")
@permission_required("products.manage")
def product_asset_delete(product_id: int, asset_id: int):
    asset = db.session.get(DigitalAsset, asset_id)
    if asset is None or asset.product_id != product_id:
        abort(404)
    if asset.file_path:
        media_service.delete_private(asset.file_path)
    db.session.delete(asset)
    db.session.commit()
    log_admin("product.asset_delete", "product", product_id, old={"asset_id": asset_id})
    flash("دارایی دیجیتال حذف شد.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


# ---------------------------------------------------------------------------
# دسته‌بندی‌ها
# ---------------------------------------------------------------------------


@admin_bp.get("/categories")
@permission_required("categories.manage")
def categories():
    items = (
        db.session.query(Category)
        .order_by(Category.sort_order, Category.name)
        .all()
    )
    return render_template("admin/categories/list.html", categories=items)


@admin_bp.route("/categories/create", methods=["POST"])
@admin_bp.route("/categories/<int:category_id>/edit", methods=["POST"])
@permission_required("categories.manage")
def category_save(category_id: int | None = None):
    category = db.session.get(Category, category_id) if category_id else None
    if category_id and category is None:
        abort(404)
    name = text_field("name", max_length=150)
    if len(name) < 2:
        flash("نام دسته‌بندی را وارد کنید.", "error")
        return redirect(url_for("admin.categories"))

    parent_id = int_field("parent_id") or None
    if parent_id and parent_id == (category.id if category else None):
        flash("دسته‌بندی نمی‌تواند والد خودش باشد.", "error")
        return redirect(url_for("admin.categories"))

    image_path = category.image if category else None
    file = request.files.get("image")
    if file is not None and file.filename:
        try:
            saved = media_service.save_upload(file, "categories", kind="image", max_mb=settings_service.get_int("image_max_mb", 5))
            image_path = saved["path"]
        except FileValidationError as exc:
            flash(exc.message, "error")
            return redirect(url_for("admin.categories"))

    if category is None:
        category = Category(created_at=utcnow(), updated_at=utcnow())
        db.session.add(category)
    category.name = name
    slug_input = text_field("slug")
    category.slug = slug_input or product_service.unique_slug(Category, name, category.id)
    duplicate = (
        db.session.query(Category.id)
        .filter(Category.slug == category.slug, Category.id != (category.id or 0))
        .first()
    )
    if duplicate:
        category.slug = product_service.unique_slug(Category, category.slug, category.id)
    category.parent_id = parent_id
    category.description = text_field("description") or None
    category.image = image_path
    category.is_active = bool_field("is_active")
    category.sort_order = int_field("sort_order")
    category.seo_title = text_field("seo_title", max_length=200) or None
    category.seo_description = text_field("seo_description", max_length=300) or None
    category.updated_at = utcnow()
    db.session.commit()
    log_admin("category.save", "category", category.id, new={"name": category.name, "slug": category.slug})
    flash("دسته‌بندی ذخیره شد.", "success")
    return redirect(url_for("admin.categories"))


@admin_bp.post("/categories/<int:category_id>/delete")
@permission_required("categories.manage")
def category_delete(category_id: int):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)
    if category.products.count() > 0:
        flash("این دسته‌بندی محصول دارد؛ ابتدا محصولات را منتقل یا حذف کنید.", "error")
        return redirect(url_for("admin.categories"))
    if category.children:
        flash("ابتدا زیردسته‌ها را حذف یا جابه‌جا کنید.", "error")
        return redirect(url_for("admin.categories"))
    if category.image:
        media_service.delete_public(category.image)
    db.session.delete(category)
    db.session.commit()
    log_admin("category.delete", "category", category_id)
    flash("دسته‌بندی حذف شد.", "success")
    return redirect(url_for("admin.categories"))


# ---------------------------------------------------------------------------
# انبار
# ---------------------------------------------------------------------------


@admin_bp.get("/inventory")
@permission_required("inventory.view")
def inventory():
    q = (request.args.get("q") or "").strip()
    stmt = (
        db.session.query(ProductVariant, Product)
        .join(Product, Product.id == ProductVariant.product_id)
        .filter(Product.deleted_at.is_(None))
    )
    if q:
        like = f"%{q}%"
        stmt = stmt.filter(or_(Product.name.like(like), ProductVariant.sku.like(like), Product.sku.like(like)))
    stock_filter = request.args.get("stock")
    if stock_filter == "low":
        stmt = stmt.filter(ProductVariant.stock_quantity <= ProductVariant.low_stock_threshold, ProductVariant.stock_quantity > 0)
    elif stock_filter == "out":
        stmt = stmt.filter(ProductVariant.stock_quantity <= 0)
    page = paginate(stmt.order_by(ProductVariant.stock_quantity.asc()), per_page=25)
    availability = inventory_service.available_map([row[0].id for row in page.items])
    return render_template(
        "admin/inventory/list.html",
        page=page,
        q=q,
        stock_filter=stock_filter,
        availability=availability,
        movement_types=INVENTORY_MOVEMENT_TYPES,
        value=inventory_service.inventory_value(),
    )


@admin_bp.post("/inventory/adjust")
@permission_required("inventory.manage")
def inventory_adjust():
    variant = db.session.get(ProductVariant, int_field("variant_id"))
    if variant is None:
        abort(404)
    movement_type = request.form.get("movement_type", "in")
    quantity = int_field("quantity")
    absolute = bool_field("absolute")
    reason = text_field("reason")
    unit_cost = int_field("unit_cost") if request.form.get("unit_cost") else None
    try:
        inventory_service.adjust_stock(
            variant,
            movement_type,
            quantity,
            reason,
            admin_id=g.user.id,
            unit_cost=unit_cost,
            absolute=absolute,
        )
        log_admin("inventory.adjust", "variant", variant.id, new={"type": movement_type, "quantity": quantity})
        flash("موجودی انبار به‌روزرسانی شد.", "success")
    except inventory_service.StockError as exc:
        flash(exc.message, "error")
    return redirect(request.referrer or url_for("admin.inventory"))


@admin_bp.get("/inventory/movements")
@permission_required("inventory.view")
def inventory_movements():
    stmt = db.session.query(InventoryMovement).order_by(InventoryMovement.created_at.desc())
    movement_type = request.args.get("type")
    if movement_type:
        stmt = stmt.filter(InventoryMovement.movement_type == movement_type)
    variant_id = request.args.get("variant", type=int)
    if variant_id:
        stmt = stmt.filter(InventoryMovement.variant_id == variant_id)
    page = paginate(stmt, per_page=50)
    return render_template(
        "admin/inventory/movements.html",
        page=page,
        movement_types=INVENTORY_MOVEMENT_TYPES,
        selected_type=movement_type,
        variant_id=variant_id,
    )
