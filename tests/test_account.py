"""تست‌های ناحیه کاربری: آدرس، سفارش، نظر، ویدئو، تیکت، اعلان و پیگیری OTP."""
from __future__ import annotations

import io

import pytest

from app.extensions import db as _db
from app.models import (
    Address,
    CustomerVideo,
    Notification,
    Order,
    Review,
    Ticket,
    User,
)
from app.services import otp_service, sms_service
from tests.conftest import add_to_cart, checkout, start_and_pay


def _purchase(app, client, variant_id, shipping_id, quantity=1):
    add_to_cart(client, variant_id, quantity)
    response = checkout(client, shipping_method_id=shipping_id)
    paid, _ = start_and_pay(client, response)
    assert paid.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        return order.order_number, order.id


def test_address_crud(app, customer):
    created = customer.post("/account/addresses", data={
        "recipient_name": "مشتری آزمون", "mobile": "09121112233", "province": "تهران", "city": "تهران",
        "full_address": "خیابان اول، پلاک ۲، واحد ۳", "postal_code": "1234567890", "is_default": "1",
    })
    assert created.status_code == 302
    with app.app_context():
        address = _db.session.query(Address).filter(Address.recipient_name == "مشتری آزمون").one()
        address_id = address.id
        assert address.is_default is True

    updated = customer.post(f"/account/addresses/{address_id}", data={
        "recipient_name": "مشتری ویرایش", "mobile": "09121112233", "province": "تهران", "city": "تهران",
        "full_address": "خیابان دوم، پلاک ۹، واحد ۱", "postal_code": "1234567890",
    })
    assert updated.status_code == 302
    with app.app_context():
        assert _db.session.get(Address, address_id).recipient_name == "مشتری ویرایش"

    invalid = customer.post("/account/addresses", data={
        "recipient_name": "ن", "mobile": "123", "province": "", "city": "", "full_address": "کوتاه",
    })
    assert invalid.status_code == 302
    with app.app_context():
        assert _db.session.query(Address).count() == 1

    assert customer.post(f"/account/addresses/{address_id}/default").status_code == 302
    assert customer.post(f"/account/addresses/{address_id}/delete").status_code == 302
    with app.app_context():
        assert _db.session.get(Address, address_id) is None
    assert customer.get("/account/addresses").status_code == 200


def test_profile_and_password_change(app, customer):
    response = customer.post("/account/profile", data={"full_name": "نام ویرایش‌شده", "email": "edited@example.com"})
    assert response.status_code == 302
    with app.app_context():
        user = _db.session.query(User).filter(User.mobile == "09121112233").one()
        assert user.full_name == "نام ویرایش‌شده"
        assert user.email == "edited@example.com"

    wrong = customer.post("/account/password", data={
        "current_password": "wrong", "new_password": "NewPass@12345", "password_confirm": "NewPass@12345",
    })
    assert wrong.status_code == 200  # فرم با پیام خطا

    changed = customer.post("/account/password", data={
        "current_password": "Customer@123", "new_password": "NewPass@12345", "password_confirm": "NewPass@12345",
    })
    assert changed.status_code == 302
    customer.post("/auth/logout")
    assert customer.post("/auth/login", data={"mobile": "09121112233", "password": "NewPass@12345"}).status_code == 302


def test_review_requires_purchase(app, customer, product, shipping):
    blocked = customer.post("/account/reviews", data={
        "product_id": product["product_id"], "rating": "5", "body": "بدون خرید نباید ثبت شود",
    })
    assert blocked.status_code == 302
    with app.app_context():
        assert _db.session.query(Review).count() == 0

    order_number, _order_id = _purchase(app, customer, product["variant_id"], shipping)
    allowed = customer.post("/account/reviews", data={
        "product_id": product["product_id"], "rating": "4", "title": "خوب", "body": "کیفیت خوب بود",
    })
    assert allowed.status_code == 302
    with app.app_context():
        review = _db.session.query(Review).one()
        assert review.status == "pending"
        assert review.is_verified_purchase is True


def test_video_upload_requires_purchase_and_approval(app, customer, product, shipping, mp4_bytes):
    no_purchase = customer.post(
        "/account/videos",
        data={"product_id": product["product_id"], "description": "بدون خرید", "video": (io.BytesIO(mp4_bytes), "v.mp4")},
        content_type="multipart/form-data",
    )
    assert no_purchase.status_code == 302
    with app.app_context():
        assert _db.session.query(CustomerVideo).count() == 0

    _purchase(app, customer, product["variant_id"], shipping)
    uploaded = customer.post(
        "/account/videos",
        data={"product_id": product["product_id"], "description": "ویدئوی خریدار", "video": (io.BytesIO(mp4_bytes), "v.mp4")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 302
    with app.app_context():
        video = _db.session.query(CustomerVideo).one()
        assert video.status == "pending"
        video_id = video.id
    # مالک می‌تواند ویدئوی در انتظار تأیید خودش را ببیند ولی دیگران نه
    assert customer.get(f"/video/{video_id}").status_code == 200


def test_ticket_create_reply_close(app, customer):
    created = customer.post("/account/tickets", data={
        "subject": "مشکل در پرداخت", "message": "پرداخت من ناموفق بود، راهنمایی کنید.",
        "department": "order", "priority": "high",
    })
    assert created.status_code == 302
    with app.app_context():
        ticket = _db.session.query(Ticket).one()
        ticket_id = ticket.id
        assert ticket.status == "open"
        assert len(ticket.messages) == 1

    reply = customer.post(f"/account/tickets/{ticket_id}/reply", data={"message": "لطفاً بررسی کنید"})
    assert reply.status_code == 302
    with app.app_context():
        assert len(_db.session.get(Ticket, ticket_id).messages) == 2

    assert customer.get(f"/account/tickets/{ticket_id}").status_code == 200
    closed = customer.post(f"/account/tickets/{ticket_id}/close")
    assert closed.status_code == 302
    with app.app_context():
        assert _db.session.get(Ticket, ticket_id).status == "closed"


def test_notifications_and_wishlist_compare(app, customer, product):
    with app.app_context():
        user = _db.session.query(User).filter(User.mobile == "09121112233").one()
        notification = Notification(
            user_id=user.id, type="welcome", title="خوش آمدید", message="به فروشگاه خوش آمدید",
            is_read=False, created_at=__import__("app.utils.dates", fromlist=["utcnow"]).utcnow(),
        )
        _db.session.add(notification)
        _db.session.commit()
        notification_id = notification.id

    assert customer.get("/account/notifications").status_code == 200
    marked = customer.post("/account/notifications/read", data={"id": str(notification_id)})
    assert marked.status_code == 302
    with app.app_context():
        assert _db.session.get(Notification, notification_id).is_read is True

    toggled = customer.post("/api/wishlist/toggle", json={"product_id": product["product_id"]})
    assert toggled.status_code == 200 and toggled.get_json()["active"] is True
    assert customer.get("/wishlist").status_code == 200
    compared = customer.post("/api/compare/toggle", json={"product_id": product["product_id"]})
    assert compared.status_code == 200 and compared.get_json()["active"] is True
    assert customer.get("/compare").status_code == 200


def test_order_cancel_unpaid(app, customer, product, shipping):
    add_to_cart(customer, product["variant_id"], 1)
    response = checkout(customer, shipping_method_id=shipping)
    assert response.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        order_number = order.order_number

    cancelled = customer.post(f"/account/orders/{order_number}/cancel")
    assert cancelled.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).filter(Order.order_number == order_number).one()
        assert order.status == "cancelled"
        assert order.payment_status == "cancelled" or order.payment_status == "unpaid"


def test_track_order_with_otp(app, client, product, shipping):
    add_to_cart(client, product["variant_id"], 1)
    response = checkout(client, shipping_method_id=shipping)
    paid, _ = start_and_pay(client, response)
    assert paid.status_code == 302
    with app.app_context():
        order = _db.session.query(Order).order_by(Order.id.desc()).first()
        order_number = order.order_number
        from app.services import settings_service

        settings_service.set_many({"otp_required_for_orders": True})

    guest = app.test_client()
    request_otp = guest.post("/track-order", data={"order_number": order_number, "mobile": "09121112233"})
    assert request_otp.status_code == 200
    with app.app_context():
        code = otp_service._DEBUG_CODES.get("09121112233")
    assert code
    wrong = guest.post("/track-order/verify", data={"code": "000000"})
    assert wrong.status_code == 200
    verified = guest.post("/track-order/verify", data={"code": code})
    assert verified.status_code == 302
    assert guest.get(verified.headers["Location"]).status_code == 200
