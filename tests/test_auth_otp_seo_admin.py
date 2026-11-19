from __future__ import annotations

import datetime as dt

from app.db import create_connection
from app.services.otp import request_otp, verify_otp
from app.utils.persian import normalize_iran_mobile, normalize_persian
from tests.conftest import extract_csrf


def test_persian_normalization_and_otp_rules(app):
    assert normalize_iran_mobile("+98 912 345 6789") == "09123456789"
    assert normalize_iran_mobile("00989123456789") == "09123456789"
    assert normalize_iran_mobile("۰۹۱۲۳۴۵۶۷۸۹") == "09123456789"
    assert normalize_iran_mobile("12345") is None

    assert normalize_persian("برنج‌طارم ي ك") == "برنج طارم ی ک"

    conn = create_connection(app.config["DATABASE_PATH"])
    with app.app_context():
        res = request_otp(conn, raw_mobile="+989127778899", purpose="login", ip_address="127.0.0.1")
        assert res.ok is True
        assert len(res.dev_code) == 6

        # Stored only as hash, never plaintext
        row = conn.execute("SELECT code_hash FROM otp_codes ORDER BY id DESC LIMIT 1").fetchone()
        assert row["code_hash"] != res.dev_code

        # Resend cooldown blocks immediate resend
        res_cool = request_otp(conn, raw_mobile="09127778899", purpose="login", ip_address="127.0.0.1")
        assert res_cool.ok is False
        assert res_cool.cooldown_remaining > 0

        # Max 5 wrong attempts
        for _ in range(5):
            bad = verify_otp(conn, raw_mobile="09127778899", raw_code="000000", purpose="login")
            assert bad.ok is False
        locked = verify_otp(conn, raw_mobile="09127778899", raw_code=res.dev_code, purpose="login")
        assert locked.ok is False

        # Expired OTP rejected
        conn.execute("DELETE FROM otp_codes")
        res2 = request_otp(conn, raw_mobile="09127778890", purpose="login")
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        conn.execute("UPDATE otp_codes SET expires_at = ?", (past,))
        exp_res = verify_otp(conn, raw_mobile="09127778890", raw_code=res2.dev_code, purpose="login")
        assert exp_res.ok is False
    conn.close()


def test_seo_endpoints_and_persian_search(client):
    r_home = client.get("/")
    assert r_home.status_code == 200
    assert 'dir="rtl"' in r_home.text
    assert 'application/ld+json' in r_home.text

    r_sm = client.get("/sitemap.xml")
    assert r_sm.status_code == 200
    assert "<urlset" in r_sm.text

    r_rb = client.get("/robots.txt")
    assert r_rb.status_code == 200
    assert "Disallow: /admin/" in r_rb.text

    # Arabic yeh/kaf search finds Persian product
    r_search = client.get("/search?q=برنج+طارم+هاشمي")
    assert r_search.status_code == 200
    assert "فریدونکنار" in r_search.text

    # Out of stock product returns HTTP 200 (indexable) with ناموجود
    r_oos = client.get("/product/چای-سبز-قلم-لاهیجان")
    assert r_oos.status_code == 200
    assert "ناموجود" in r_oos.text


def test_admin_slug_redirect_rbac_and_end_to_end_checkout(app, client):
    # Admin unauthenticated redirects to login
    r_unauth = client.get("/admin/", follow_redirects=False)
    assert r_unauth.status_code == 302
    assert "/admin/login" in r_unauth.headers["Location"]

    # Login as super_admin
    r_login_page = client.get("/admin/login")
    csrf_tok = extract_csrf(r_login_page.data)
    r_do_login = client.post(
        "/admin/login",
        data={"csrf_token": csrf_tok, "action": "credentials", "identifier": "admin", "password": "ChangeMe123!@#"},
        follow_redirects=False,
    )
    assert r_do_login.status_code == 302

    # Edit product slug and verify automatic 301 redirect
    conn = create_connection(app.config["DATABASE_PATH"])
    prod = conn.execute("SELECT * FROM products WHERE slug = 'زردچوبه-قلمی-آسیاب-تازه'").fetchone()
    pid = int(prod["id"])
    var = conn.execute("SELECT * FROM product_variants WHERE product_id = ? LIMIT 1", (pid,)).fetchone()
    r_edit_pg = client.get(f"/admin/products/{pid}/edit")
    csrf2 = extract_csrf(r_edit_pg.data)
    r_save = client.post(
        f"/admin/products/{pid}/edit",
        data={
            "csrf_token": csrf2,
            "title": prod["title"],
            "slug": "زردچوبه-تازه-ارگانیک",
            "category_id": str(prod["category_id"]),
            "status": "published",
            "variant_id[]": [str(var["id"])],
            "variant_label[]": [var["label"]],
            "variant_sku[]": [var["sku"]],
            "variant_price_toman[]": [str(var["price_rial"] // 10)],
            "variant_stock[]": [str(var["stock_quantity"])],
            "variant_active[]": ["1"],
        },
        follow_redirects=False,
    )
    assert r_save.status_code == 302
    r_redir = client.get("/product/زردچوبه-قلمی-آسیاب-تازه", follow_redirects=False)
    assert r_redir.status_code == 301
    assert "/product/" in r_redir.headers["Location"]

    # Logout admin and test customer cart -> checkout -> mock payment
    client.post("/admin/logout", data={"csrf_token": csrf2})

    # Add item to guest cart
    v_id = conn.execute("SELECT id FROM product_variants WHERE sku = 'ARM-RICE-TH-1K'").fetchone()["id"]
    r_cart = client.get("/cart/")
    c_csrf = extract_csrf(r_cart.data)
    r_add = client.post(
        "/cart/add",
        json={"variant_id": int(v_id), "quantity": 2},
        headers={"X-CSRFToken": c_csrf, "X-Requested-With": "XMLHttpRequest"},
    )
    assert r_add.status_code == 200
    assert r_add.get_json()["ok"] is True

    # Request guest checkout OTP
    r_otp = client.post(
        "/auth/otp/request",
        json={"mobile": "09128889900", "purpose": "checkout"},
        headers={"X-CSRFToken": c_csrf},
    )
    dev_code = r_otp.get_json()["dev_code"]

    prov_id = conn.execute("SELECT id FROM provinces WHERE name = 'تهران'").fetchone()["id"]
    city_id = conn.execute("SELECT id FROM cities WHERE province_id = ? LIMIT 1", (prov_id,)).fetchone()["id"]
    conn.close()

    r_chk_pg = client.get("/checkout/")
    chk_csrf = extract_csrf(r_chk_pg.data)
    r_chk = client.post(
        "/checkout/",
        data={
            "csrf_token": chk_csrf,
            "address_id": "new",
            "customer_name": "زهرا حسینی",
            "customer_mobile": "09128889900",
            "otp_code": dev_code,
            "province_id": str(prov_id),
            "city_id": str(city_id),
            "address_line": "تهران خیابان شریعتی پلاک ۱۵۰",
            "postal_code": "1541111111",
            "payment_method": "bitpay",
            "accept_terms": "1",
        },
        follow_redirects=False,
    )
    assert r_chk.status_code == 302
    assert "/payment/mock-gateway/" in r_chk.headers["Location"]

    # Complete mock payment
    mock_url = r_chk.headers["Location"]
    r_mock_pg = client.get(mock_url)
    m_csrf = extract_csrf(r_mock_pg.data)
    r_pay = client.post(
        mock_url,
        data={"csrf_token": m_csrf, "outcome": "success"},
        follow_redirects=True,
    )
    assert r_pay.status_code == 200
    assert "با موفقیت ثبت شد" in r_pay.text


def test_all_admin_routes_and_rbac(app, client):
    """Verify every admin template renders 200 for super_admin and RBAC blocks unauthorized roles."""
    # Login as super_admin
    r_login = client.get("/admin/login")
    csrf = extract_csrf(r_login.data)
    client.post(
        "/admin/login",
        data={"csrf_token": csrf, "action": "credentials", "identifier": "admin", "password": "ChangeMe123!@#"},
    )

    admin_routes = [
        "/admin/",
        "/admin/products",
        "/admin/products/new",
        "/admin/categories",
        "/admin/inventory",
        "/admin/back-in-stock",
        "/admin/orders",
        "/admin/orders/manual",
        "/admin/customers",
        "/admin/reviews",
        "/admin/coupons",
        "/admin/homepage",
        "/admin/pages",
        "/admin/redirects",
        "/admin/payments",
        "/admin/sms",
        "/admin/reports",
        "/admin/settings",
        "/admin/users",
        "/admin/audit-logs",
    ]
    for path in admin_routes:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    # Create an order_operator and verify RBAC blocks /admin/settings and /admin/users
    r_users = client.get("/admin/users")
    csrf_u = extract_csrf(r_users.data)
    client.post(
        "/admin/users",
        data={
            "csrf_token": csrf_u,
            "action": "create",
            "full_name": "اپراتور سفارش",
            "username": "operator1",
            "email": "op@arminstore.ir",
            "role": "order_operator",
            "password": "OperatorPass123!",
        },
    )
    client.post("/admin/logout", data={"csrf_token": csrf_u})

    r_l2 = client.get("/admin/login")
    csrf_l2 = extract_csrf(r_l2.data)
    client.post(
        "/admin/login",
        data={"csrf_token": csrf_l2, "action": "credentials", "identifier": "operator1", "password": "OperatorPass123!"},
    )
    assert client.get("/admin/orders").status_code == 200
    assert client.get("/admin/settings").status_code == 403
    assert client.get("/admin/users").status_code == 403

