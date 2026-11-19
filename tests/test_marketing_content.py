"""تست‌های بازاریابی، محتوا، پشتیبانی، کاربران، تنظیمات، بکاپ و لاگ."""
from __future__ import annotations

import io

import pytest

from app.extensions import db as _db
from app.models import (
    Advertisement,
    AuditLog,
    BlogPost,
    Coupon,
    CustomerVideo,
    Notification,
    Page,
    Review,
    Role,
    ShippingMethod,
    SmsLog,
    Ticket,
    TicketMessage,
    User,
)
from app.services import backup_service, sms_service
from app.utils.dates import utcnow


def test_coupon_crud_and_validation(app, admin_client):
    created = admin_client.post(
        "/admin/coupons/create",
        data={
            "code": "OFF20", "discount_type": "percent", "discount_value": "20",
            "minimum_order": "100000", "maximum_discount": "50000", "usage_limit": "5",
            "per_user_limit": "1", "is_active": "1", "applies_to": "all",
        },
    )
    assert created.status_code == 302
    with app.app_context():
        coupon = _db.session.query(Coupon).filter(Coupon.code == "OFF20").one()
        coupon_id = coupon.id
        assert coupon.discount_value == 20

    duplicate = admin_client.post(
        "/admin/coupons/create",
        data={"code": "OFF20", "discount_type": "fixed", "discount_value": "1000", "applies_to": "all"},
    )
    assert duplicate.status_code == 200  # فرم با خطا دوباره نمایش داده می‌شود

    invalid = admin_client.post(
        "/admin/coupons/create",
        data={"code": "BAD", "discount_type": "percent", "discount_value": "150", "applies_to": "all"},
    )
    assert invalid.status_code == 200

    assert admin_client.get(f"/admin/coupons/{coupon_id}/edit").status_code == 200
    assert admin_client.get("/admin/coupons").status_code == 200
    admin_client.post(f"/admin/coupons/{coupon_id}/toggle")
    with app.app_context():
        assert _db.session.get(Coupon, coupon_id).is_active is False
    admin_client.post(f"/admin/coupons/{coupon_id}/delete")
    with app.app_context():
        assert _db.session.get(Coupon, coupon_id) is None


def test_advertisement_lifecycle(app, admin_client, client):
    created = admin_client.post(
        "/admin/ads/create",
        data={"text": "ارسال رایگان امروز", "link": "/products", "background_color": "#111827",
              "text_color": "#ffffff", "is_active": "1", "sort_order": "0"},
    )
    assert created.status_code == 302
    with app.app_context():
        ad = _db.session.query(Advertisement).one()
        ad_id = ad.id
    assert "ارسال رایگان امروز" in client.get("/").get_data(as_text=True)
    admin_client.post(f"/admin/ads/{ad_id}/toggle")
    with app.app_context():
        assert _db.session.get(Advertisement, ad_id).is_active is False
    admin_client.post(f"/admin/ads/{ad_id}/delete")
    with app.app_context():
        assert _db.session.get(Advertisement, ad_id) is None


def test_shipping_methods_and_cost_calculation(app, admin_client):
    created = admin_client.post(
        "/admin/shipping/create",
        data={
            "name": "ارسال استانی", "shipping_type": "province", "base_cost": "60000",
            "free_threshold": "0", "estimated_days": "۳ روز", "sort_order": "0",
            "configuration_json": '{"province_costs": {"تهران": 45000}, "city_costs": {"تهران|کرج": 30000}}',
            "is_active": "1",
        },
    )
    assert created.status_code == 302
    with app.app_context():
        method = _db.session.query(ShippingMethod).filter(ShippingMethod.name == "ارسال استانی").one()
        assert method.cost_for("تهران", "کرج", 100000) == 45000
        assert method.cost_for("اصفهان", "کاشان", 100000) == 60000
        method_id = method.id

    city_method = ShippingMethod(
        name="ارسال شهری", shipping_type="city", base_cost=80000,
        configuration_json={"city_costs": {"تهران|کرج": 30000}}, is_active=True,
        created_at=utcnow(), updated_at=utcnow(),
    )
    with app.app_context():
        _db.session.add(city_method)
        _db.session.commit()
        assert city_method.cost_for("تهران", "کرج", 100000) == 30000
        assert city_method.cost_for("تهران", "تهران", 100000) == 80000

    bad_json = admin_client.post(
        "/admin/shipping/create",
        data={"name": "نامعتبر", "shipping_type": "province", "configuration_json": "{not-json", "is_active": "1"},
    )
    assert bad_json.status_code == 302
    admin_client.post(f"/admin/shipping/{method_id}/toggle")
    with app.app_context():
        assert _db.session.get(ShippingMethod, method_id).is_active is False
    admin_client.post(f"/admin/shipping/{method_id}/delete")
    with app.app_context():
        assert _db.session.get(ShippingMethod, method_id) is None


def test_blog_and_pages_admin_and_public(app, admin_client, client):
    response = admin_client.post(
        "/admin/blog/create",
        data={"title": "راهنمای خرید", "slug": "buying-guide", "excerpt": "خلاصه",
              "content": "<p>" + "متن کامل مقاله " * 5 + "</p>", "status": "published"},
    )
    assert response.status_code == 302
    with app.app_context():
        post = _db.session.query(BlogPost).filter(BlogPost.slug == "buying-guide").one()
        post_id = post.id
        assert post.status == "published" and post.published_at is not None
    assert client.get("/blog").status_code == 200
    assert client.get("/blog/buying-guide").status_code == 200
    assert admin_client.get(f"/admin/blog/{post_id}/edit").status_code == 200

    page_response = admin_client.post(
        "/admin/pages/create",
        data={"title": "قوانین خرید", "slug": "terms", "content": "<p>قوانین فروشگاه</p>",
              "is_published": "1", "show_in_footer": "1", "sort_order": "1"},
    )
    assert page_response.status_code == 302
    with app.app_context():
        page = _db.session.query(Page).filter(Page.slug == "terms").one()
        page_id = page.id
    assert client.get("/page/terms").status_code == 200
    assert "قوانین خرید" in client.get("/").get_data(as_text=True)
    admin_client.post(f"/admin/pages/{page_id}/toggle")
    with app.app_context():
        assert _db.session.get(Page, page_id).is_published is False
    assert client.get("/page/terms").status_code == 404

    admin_client.post(f"/admin/pages/{page_id}/delete")
    admin_client.post(f"/admin/blog/{post_id}/delete")
    with app.app_context():
        assert _db.session.get(Page, page_id) is None
        assert _db.session.get(BlogPost, post_id) is None


def test_support_tickets_admin_flow(app, admin_client, customer):
    with app.app_context():
        user = _db.session.query(User).filter(User.mobile == "09121112233").one()
        ticket = Ticket(
            ticket_number="T-ADMIN-1", user_id=user.id, subject="سوال پرداخت",
            department="order", priority="high", status="open",
            created_at=utcnow(), updated_at=utcnow(),
        )
        _db.session.add(ticket)
        _db.session.flush()
        _db.session.add(TicketMessage(ticket_id=ticket.id, is_admin_reply=False, message="سوال اولیه", created_at=utcnow()))
        _db.session.commit()
        ticket_id = ticket.id

    assert admin_client.get("/admin/tickets").status_code == 200
    detail = admin_client.get(f"/admin/tickets/{ticket_id}")
    assert detail.status_code == 200

    reply = admin_client.post(
        f"/admin/tickets/{ticket_id}/reply",
        data={"message": "پاسخ پشتیبانی فروشگاه"},
    )
    assert reply.status_code == 302
    status_response = admin_client.post(f"/admin/tickets/{ticket_id}/status", data={"status": "answered"})
    assert status_response.status_code == 302
    with app.app_context():
        ticket = _db.session.get(Ticket, ticket_id)
        assert ticket.status == "answered"
        assert len(ticket.messages) == 2
        assert ticket.messages[-1].is_admin_reply is True
    assert customer.get(f"/account/tickets/{ticket_id}").status_code == 200


def test_reviews_and_videos_moderation(app, admin_client, product):
    with app.app_context():
        review = Review(product_id=product["product_id"], rating=5, body="نظر عالی", status="pending",
                        is_verified_purchase=True, created_at=utcnow(), updated_at=utcnow())
        video = CustomerVideo(product_id=product["product_id"], file_path="videos/x.mp4", status="pending",
                              description="ویدئوی مشتری", created_at=utcnow(), updated_at=utcnow())
        _db.session.add_all([review, video])
        _db.session.commit()
        review_id, video_id = review.id, video.id

    assert admin_client.get("/admin/reviews").status_code == 200
    assert admin_client.get("/admin/videos").status_code == 200
    admin_client.post(f"/admin/reviews/{review_id}/moderate", data={"action": "approve"})
    admin_client.post(f"/admin/videos/{video_id}/moderate", data={"action": "approve"})
    with app.app_context():
        assert _db.session.get(Review, review_id).status == "approved"
        assert _db.session.get(CustomerVideo, video_id).status == "approved"
    admin_client.post(f"/admin/reviews/{review_id}/delete")
    admin_client.post(f"/admin/videos/{video_id}/delete")
    with app.app_context():
        assert _db.session.get(Review, review_id) is None
        assert _db.session.get(CustomerVideo, video_id) is None


def test_roles_and_staff_users(app, admin_client, admin):
    created_role = admin_client.post(
        "/admin/roles/create",
        data={"name": "content_editor", "title": "ویرایشگر محتوا",
              "permissions[]": ["dashboard.view", "blog.manage", "pages.manage"]},
    )
    assert created_role.status_code == 302
    with app.app_context():
        role = _db.session.query(Role).filter(Role.name == "content_editor").one()
        role_id = role.id

    user_response = admin_client.post(
        "/admin/users/create",
        data={"full_name": "کارمند محتوا", "mobile": "09127778811", "password": "Staff@123456",
              "role_ids[]": [str(role_id)], "is_active": "1"},
    )
    assert user_response.status_code == 302
    with app.app_context():
        staff = _db.session.query(User).filter(User.mobile == "09127778811").one()
        assert staff.roles[0].name == "content_editor"
        staff_id = staff.id

    assert admin_client.get("/admin/users").status_code == 200
    assert admin_client.get(f"/admin/users/{staff_id}/edit").status_code == 200
    assert admin_client.get("/admin/roles").status_code == 200

    # حذف نقش دارای کاربر باید مسدود شود
    blocked = admin_client.post(f"/admin/roles/{role_id}/delete")
    assert blocked.status_code == 302
    with app.app_context():
        assert _db.session.get(Role, role_id) is not None

    # نمی‌توان آخرین مدیر کل را غیرفعال کرد
    admin_client.post(f"/admin/users/{admin}/toggle")
    with app.app_context():
        assert _db.session.get(User, admin).is_active is True


def test_settings_payment_and_sms_pages(app, admin_client, monkeypatch):
    response = admin_client.post(
        "/admin/settings/general",
        data={"store_name": "فروشگاه تغییر‌یافته", "store_slogan": "شعار جدید", "store_description": "توضیح",
              "contact_email": "new@example.com", "contact_phone": "02100000000"},
    )
    assert response.status_code == 302
    with app.app_context():
        from app.services import settings_service
        assert settings_service.get_str("store_name") == "فروشگاه تغییر‌یافته"

    payment = admin_client.post(
        "/admin/integrations/payment",
        data={"payment_online_enabled": "1", "bitpay_enabled": "1", "bitpay_test_mode": "1",
              "bitpay_amount_multiplier": "10", "payment_timeout_minutes": "20",
              "bitpay_base_url": "https://bitpay.example/api", "bitpay_token_path": "/token",
              "bitpay_verify_path": "/verify", "bitpay_gateway_url": "https://bitpay.example/pay"},
    )
    assert payment.status_code == 302
    assert admin_client.get("/admin/integrations/payment").status_code == 200
    checked = admin_client.post("/admin/integrations/payment/check")
    assert checked.status_code == 302

    sms = admin_client.post(
        "/admin/integrations/sms",
        data={"_sms_form": "1", "sms_enabled": "1", "sms_api_key": "test-key",
              "sms_base_url": "https://api.sms.ir", "sms_verify_path": "/v1/send/verify",
              "sms_bulk_path": "/v1/send/bulk", "sms_line_number": "3000", "sms_pattern_otp": "1234",
              "sms_notify_order_created": "1"},
    )
    assert sms.status_code == 302
    assert admin_client.get("/admin/integrations/sms").status_code == 200

    sent = []
    monkeypatch.setattr(sms_service, "send_otp", lambda mobile, code: (sent.append(mobile) or sms_service.SmsResult(True, "ارسال شد", "sent")))
    test_send = admin_client.post("/admin/integrations/sms/test", data={"mobile": "09121234567"})
    assert test_send.status_code == 302
    assert sent == ["09121234567"]

    assert admin_client.get("/admin/system/status").status_code == 200
    assert admin_client.get("/admin/settings/general").status_code == 200


def test_backup_create_download_delete_and_upload(app, admin_client):
    response = admin_client.post("/admin/backups/create", data={"note": "تست بکاپ"})
    assert response.status_code == 302
    with app.app_context():
        files = backup_service.list_backups()
    assert len(files) == 1
    filename = files[0]["filename"]
    assert admin_client.get(f"/admin/backups/download/{filename}").status_code == 200

    uploaded = admin_client.post(
        "/admin/backups/upload",
        data={"file": (io.BytesIO(b"not a database"), "bad.db")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 302

    admin_client.post("/admin/backups/delete", data={"filename": filename})
    with app.app_context():
        assert backup_service.list_backups() == []

    assert admin_client.get("/admin/backups").status_code == 200


def test_audit_log_records_admin_actions(app, admin_client, category):
    admin_client.post(
        "/admin/categories/create",
        data={"name": "دسته لاگ", "slug": "log-cat", "sort_order": "0", "is_active": "1"},
    )
    with app.app_context():
        log = _db.session.query(AuditLog).filter(AuditLog.action == "category.save").first()
        assert log is not None
        assert log.admin_id is not None
        assert log.entity_type == "category"
    assert admin_client.get("/admin/audit").status_code == 200
    assert admin_client.get("/admin/audit?q=category").status_code == 200
