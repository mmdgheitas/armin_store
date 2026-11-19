"""تست‌های مدیریت کاتالوگ، دسته‌بندی، انبار، رسانه و سئو."""
from __future__ import annotations

import io

import pytest

from app.extensions import db as _db
from app.models import (
    Category,
    DigitalAsset,
    InventoryMovement,
    Product,
    ProductMedia,
    ProductVariant,
)
from app.utils.dates import utcnow


PRODUCT_FORM = {
    "name": "هدفون بی‌سیم",
    "sku": "ADMIN-HP-1",
    "product_type": "physical",
    "short_description": "توضیح کوتاه",
    "description": "<p>توضیح کامل محصول</p>",
    "base_price": "500000",
    "base_cost": "300000",
    "is_active": "1",
    "is_featured": "1",
    "specifications": "وزن: 250 گرم\nباتری: 20 ساعت",
    "weight": "250",
    "variant_id[]": [""],
    "variant_sku[]": ["ADMIN-HP-1-BLK"],
    "variant_attrs[]": ["مشکی"],
    "variant_price[]": ["500000"],
    "variant_compare[]": ["650000"],
    "variant_cost[]": ["300000"],
    "variant_stock[]": ["7"],
    "variant_threshold[]": ["2"],
    "variant_default[]": ["1"],
}


def _product_form(**overrides):
    form = {key: (list(value) if isinstance(value, list) else value) for key, value in PRODUCT_FORM.items()}
    form.update(overrides)
    return form


def test_product_create_edit_and_stock(app, admin_client, category):
    form = _product_form(category_id=str(category))
    response = admin_client.post("/admin/products/create", data=form)
    assert response.status_code == 302
    with app.app_context():
        product = _db.session.query(Product).filter(Product.sku == "ADMIN-HP-1").one()
        assert product.slug and product.is_active and product.is_featured
        assert product.specifications == [{"name": "وزن", "value": "250 گرم"}, {"name": "باتری", "value": "20 ساعت"}]
        variant = product.variants[0]
        assert variant.stock_quantity == 7
        assert variant.effective_price == 500000
        product_id, variant_id = product.id, variant.id

    edit = _product_form(category_id=str(category), name="هدفون ویرایش‌شده", base_price="450000",
                         **{"variant_id[]": [str(variant_id)], "variant_price[]": ["450000"], "variant_stock[]": ["9"]})
    response = admin_client.post(f"/admin/products/{product_id}/edit", data=edit)
    assert response.status_code == 302
    with app.app_context():
        product = _db.session.get(Product, product_id)
        assert product.name == "هدفون ویرایش‌شده"
        assert product.base_price == 450000
        assert product.variants[0].stock_quantity == 9

    admin_client.post(f"/admin/products/{product_id}/toggle")
    admin_client.post(f"/admin/products/{product_id}/featured")
    with app.app_context():
        product = _db.session.get(Product, product_id)
        assert product.is_active is False
        assert product.is_featured is False

    detail = admin_client.get(f"/admin/products/{product_id}/edit")
    assert detail.status_code == 200


def test_inventory_adjust_creates_movements(app, admin_client, product):
    variant_id = product["variant_id"]
    response = admin_client.post(
        "/admin/inventory/adjust",
        data={"variant_id": str(variant_id), "movement_type": "in", "quantity": "5", "reason": "خرید انبار"},
    )
    assert response.status_code == 302
    response = admin_client.post(
        "/admin/inventory/adjust",
        data={"variant_id": str(variant_id), "movement_type": "adjust", "quantity": "3", "absolute": "1", "reason": "اصلاح شمارش"},
    )
    assert response.status_code == 302
    blocked = admin_client.post(
        "/admin/inventory/adjust",
        data={"variant_id": str(variant_id), "movement_type": "out", "quantity": "999", "reason": "خروج نامعتبر"},
    )
    assert blocked.status_code == 302
    with app.app_context():
        variant = _db.session.get(ProductVariant, variant_id)
        assert variant.stock_quantity == 3
        movements = _db.session.query(InventoryMovement).filter(InventoryMovement.variant_id == variant_id).all()
        assert len(movements) == 2
        assert {m.movement_type for m in movements} == {"in", "adjust"}
        assert any(m.reason == "اصلاح شمارش" for m in movements)
    assert admin_client.get("/admin/inventory").status_code == 200
    assert admin_client.get("/admin/inventory/movements").status_code == 200


def test_media_and_digital_asset_crud(app, admin_client, product, png_bytes):
    upload = admin_client.post(
        f"/admin/products/{product['product_id']}/media",
        data={"file": (io.BytesIO(png_bytes), "main.png"), "media_type": "image", "alt_text": "تصویر", "sort_order": "0"},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 302
    with app.app_context():
        media = _db.session.query(ProductMedia).filter(ProductMedia.product_id == product["product_id"]).one()
        media_id = media.id
        stored = media.file_path
    assert admin_client.get(f"/media/{stored}").status_code == 200
    admin_client.post(f"/admin/products/{product['product_id']}/media/{media_id}/delete")
    with app.app_context():
        assert _db.session.query(ProductMedia).filter(ProductMedia.product_id == product["product_id"]).count() == 0

    asset = admin_client.post(
        f"/admin/products/{product['product_id']}/assets",
        data={
            "delivery_type": "link",
            "external_url": "https://example.com/download/file.zip",
            "download_limit": "3",
            "download_expiry_days": "1",
            "title": "فایل تحویل",
        },
    )
    assert asset.status_code == 302
    with app.app_context():
        row = _db.session.query(DigitalAsset).filter(DigitalAsset.product_id == product["product_id"]).one()
        assert row.delivery_type == "link" and row.download_limit == 3
        asset_id = row.id
    admin_client.post(f"/admin/products/{product['product_id']}/assets/{asset_id}/delete")
    with app.app_context():
        assert _db.session.query(DigitalAsset).filter(DigitalAsset.product_id == product["product_id"]).count() == 0


def test_category_crud_and_delete_guard(app, admin_client, product):
    created = admin_client.post(
        "/admin/categories/create",
        data={"name": "دسته جدید", "slug": "new-cat", "parent_id": "", "sort_order": "2", "is_active": "1"},
    )
    assert created.status_code == 302
    with app.app_context():
        category = _db.session.query(Category).filter(Category.slug == "new-cat").one()
        category_id = category.id

    updated = admin_client.post(
        f"/admin/categories/{category_id}/edit",
        data={"name": "دسته ویرایش", "slug": "new-cat", "sort_order": "4", "is_active": "1"},
    )
    assert updated.status_code == 302
    with app.app_context():
        assert _db.session.get(Category, category_id).name == "دسته ویرایش"

    # دسته دارای محصول نباید حذف شود
    with app.app_context():
        product_row = _db.session.get(Product, product["product_id"])
        product_row.category_id = category_id
        _db.session.commit()
    blocked = admin_client.post(f"/admin/categories/{category_id}/delete")
    assert blocked.status_code == 302
    with app.app_context():
        assert _db.session.get(Category, category_id) is not None

    empty = admin_client.post(
        "/admin/categories/create", data={"name": "دسته خالی", "slug": "empty-cat", "sort_order": "0", "is_active": "1"}
    )
    with app.app_context():
        empty_id = _db.session.query(Category).filter(Category.slug == "empty-cat").one().id
    admin_client.post(f"/admin/categories/{empty_id}/delete")
    with app.app_context():
        assert _db.session.get(Category, empty_id) is None


def test_seo_outputs(app, client, product):
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert product["slug"] in sitemap.get_data(as_text=True)
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap" in robots.get_data(as_text=True)

    detail = client.get(f"/product/{product['slug']}")
    html = detail.get_data(as_text=True)
    assert 'property="og:title"' in html
    assert 'application/ld+json' in html
