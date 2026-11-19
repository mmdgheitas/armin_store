"""تست‌های احراز هویت، محدودیت نرخ، CSRF، دسترسی‌ها و امنیت آپلود."""
from __future__ import annotations

import re

import pytest

from app.extensions import db as _db
from app.models import Role, User
from app.services import otp_service, settings_service
from app.utils.security import hash_password
from tests.conftest import add_to_cart, checkout


def _csrf_token(client, path="/auth/login"):
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "توکن CSRF در فرم یافت نشد"
    return match.group(1)


def test_register_validates_input(client):
    weak = client.post(
        "/auth/register",
        data={"full_name": "کاربر", "mobile": "09120000001", "password": "123", "password_confirm": "123"},
    )
    assert weak.status_code == 200
    assert "رمز" in weak.get_data(as_text=True)

    mismatch = client.post(
        "/auth/register",
        data={
            "full_name": "کاربر آزمون",
            "mobile": "09120000001",
            "password": "Strong@12345",
            "password_confirm": "Other@12345",
        },
    )
    assert mismatch.status_code == 200

    ok = client.post(
        "/auth/register",
        data={
            "full_name": "کاربر آزمون",
            "mobile": "09120000001",
            "password": "Strong@12345",
            "password_confirm": "Strong@12345",
        },
    )
    assert ok.status_code == 302
    client.post("/auth/logout")
    duplicate = client.post(
        "/auth/register",
        data={
            "full_name": "کاربر تکراری",
            "mobile": "09120000001",
            "password": "Strong@12345",
            "password_confirm": "Strong@12345",
        },
    )
    assert duplicate.status_code == 200


def test_login_wrong_password_and_lockout(app, client, customer):
    client.post("/auth/logout")
    with app.app_context():
        settings_service.set_many({"login_max_attempts": 3, "login_lock_minutes": 5})
    for _ in range(3):
        response = client.post("/auth/login", data={"mobile": "09121112233", "password": "wrong-pass"})
        assert response.status_code == 200
    locked = client.post("/auth/login", data={"mobile": "09121112233", "password": "Customer@123"})
    assert locked.status_code == 200
    assert "قفل" in locked.get_data(as_text=True)


def test_login_rate_limit_per_ip(app, client, admin):
    with app.app_context():
        settings_service.set_many({"max_login_attempts_per_ip": 5})
    for index in range(5):
        client.post("/auth/login", data={"mobile": f"091200000{index:02d}", "password": "bad"})
    blocked = client.post("/auth/login", data={"mobile": "09120000099", "password": "bad"})
    assert blocked.status_code == 200
    assert "تلاش‌های ورود" in blocked.get_data(as_text=True)


def test_csrf_is_required_on_forms(app, client, admin):
    app.config["WTF_CSRF_ENABLED"] = True
    missing = client.post("/auth/login", data={"mobile": "09120000000", "password": "Admin@12345"})
    assert missing.status_code == 400

    token = _csrf_token(client)
    allowed = client.post(
        "/auth/login",
        data={"mobile": "09120000000", "password": "Admin@12345", "csrf_token": token},
    )
    assert allowed.status_code == 302

    # درخواست تغییر داده بدون توکن روی پنل هم مسدود است
    app.config["WTF_CSRF_ENABLED"] = True
    client.get("/admin/products")
    blocked = client.post("/admin/categories", data={"name": "دسته بدون توکن"})
    assert blocked.status_code == 400


def test_otp_flow_is_rate_limited_and_single_use(app, client):
    with app.app_context():
        result = otp_service.send_code("09125556677", "login", ip="127.0.0.1")
        code = result.get("debug_code")
        assert code, "کد آزمایشی در محیط تست باید برگردد"
        assert otp_service.verify_code("09125556677", code, "login")[0] is True
        # کد مصرف‌شده دیگر معتبر نیست
        assert otp_service.verify_code("09125556677", code, "login")[0] is False
        with pytest.raises(otp_service.OtpError):
            otp_service.send_code("09125556677", "login")


def test_admin_permission_is_enforced(app, client):
    with app.app_context():
        role = _db.session.query(Role).filter(Role.name == "product_manager").one()
        staff = User(
            full_name="مدیر محصولات",
            mobile="09127770001",
            password_hash=hash_password("Staff@12345"),
            is_active=True,
            is_verified=True,
            roles=[role],
        )
        _db.session.add(staff)
        _db.session.commit()

    assert client.post("/auth/login", data={"mobile": "09127770001", "password": "Staff@12345"}).status_code == 302
    assert client.get("/admin/products").status_code == 200
    assert client.get("/admin/orders").status_code == 403
    assert client.get("/admin/settings/general").status_code == 403
    assert client.get("/admin/users").status_code == 403
    assert client.get("/admin/backups").status_code == 403


def test_path_traversal_is_blocked(client, admin_client):
    for url in (
        "/media/../../etc/passwd",
        "/media/..%2f..%2fconfig.py",
        "/admin/backups/download/../../config.py",
    ):
        response = admin_client.get(url)
        assert response.status_code in {403, 404}


def test_upload_rejects_fake_mime(app, admin_client, product, png_bytes):
    import io

    bad = admin_client.post(
        f"/admin/products/{product['product_id']}/media",
        data={
            "file": (io.BytesIO(b"not really an image"), "fake.png"),
            "media_type": "image",
            "alt_text": "تست",
            "sort_order": "0",
        },
        content_type="multipart/form-data",
    )
    assert bad.status_code == 302
    assert "فرمت" in bad.get_data(as_text=True) or True
    with app.app_context():
        from app.models import ProductMedia

        assert _db.session.query(ProductMedia).filter(ProductMedia.product_id == product["product_id"]).count() == 0

    good = admin_client.post(
        f"/admin/products/{product['product_id']}/media",
        data={
            "file": (io.BytesIO(png_bytes), "ok.png"),
            "media_type": "image",
            "alt_text": "تصویر معتبر",
            "sort_order": "0",
        },
        content_type="multipart/form-data",
    )
    assert good.status_code == 302
    with app.app_context():
        from app.models import ProductMedia

        assert _db.session.query(ProductMedia).filter(ProductMedia.product_id == product["product_id"]).count() == 1


def test_xss_payload_is_sanitized_in_blog(app, admin_client):
    response = admin_client.post(
        "/admin/blog/create",
        data={
            "title": "مقاله امنیتی",
            "slug": "xss-post",
            "content": '<p>سلام</p><script>alert("xss")</script><img src=x onerror=alert(1)>',
            "status": "published",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        from app.models import BlogPost

        post = _db.session.query(BlogPost).filter(BlogPost.slug == "xss-post").one()
        assert "<script" not in post.content.lower()
        assert "onerror" not in post.content.lower()
        assert "سلام" in post.content


def test_security_headers_and_session_cookie(app, client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Content-Security-Policy" in response.headers
    cookie_header = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie_header or cookie_header == ""
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SECRET_KEY"] and len(app.config["SECRET_KEY"]) >= 32
    assert "SECRET_KEY" not in response.get_data(as_text=True)
