from __future__ import annotations

from urllib.parse import urlparse

from flask import (
    Blueprint,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from app import limiter
from app.db import get_db
from app.repositories.users import (
    create_user,
    get_user_by_email,
    get_user_by_mobile,
)
from app.services.auth import (
    ADMIN_ROLES,
    hash_password,
    login_user_session,
    logout_user_session,
    validate_password_policy,
    verify_password,
)
from app.services.cart import merge_guest_cart_into_user
from app.services.otp import request_otp, verify_otp
from app.utils.persian import normalize_iran_mobile

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next_url(target: str | None) -> str:
    if not target:
        return url_for("account.dashboard")
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme:
        return url_for("account.dashboard")
    if not target.startswith("/"):
        return url_for("account.dashboard")
    return target


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("30/minute")
def login():
    if g.current_user:
        return redirect(url_for("account.dashboard"))

    conn = get_db()
    next_url = _safe_next_url(request.args.get("next") or request.form.get("next"))

    if request.method == "POST":
        mode = request.form.get("auth_mode", "password")
        if mode == "otp_verify":
            mobile = normalize_iran_mobile(request.form.get("mobile"))
            code = request.form.get("code", "")
            full_name = (request.form.get("full_name") or "").strip()
            vres = verify_otp(conn, raw_mobile=mobile or "", raw_code=code, purpose="login")
            if not vres.ok:
                flash(vres.message, "danger")
                return render_template("auth/login.html", next_url=next_url, active_tab="otp", prefill_mobile=mobile or "")

            user = get_user_by_mobile(conn, mobile)
            if not user:
                uid = create_user(
                    conn,
                    mobile=mobile,
                    full_name=full_name or "کاربر گرامی",
                    role="customer",
                    mobile_verified=1,
                )
                user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            elif user["is_blocked"] or not user["is_active"]:
                flash("حساب کاربری شما مسدود یا غیرفعال شده است.", "danger")
                return render_template("auth/login.html", next_url=next_url, active_tab="otp")
            else:
                conn.execute("UPDATE users SET mobile_verified = 1 WHERE id = ?", (user["id"],))

            login_user_session(conn, user, is_admin_session=(user["role"] in ADMIN_ROLES))
            merge_guest_cart_into_user(conn, int(user["id"]))
            flash("خوش آمدید!", "success")
            return redirect(next_url)

        # Email + password login
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = get_user_by_email(conn, email) if email else None
        if not user or not verify_password(user["password_hash"], password):
            flash("ایمیل یا رمز عبور اشتباه است.", "danger")
            return render_template("auth/login.html", next_url=next_url, active_tab="password", prefill_email=email)
        if user["is_blocked"] or not user["is_active"]:
            flash("حساب کاربری شما مسدود یا غیرفعال است.", "danger")
            return render_template("auth/login.html", next_url=next_url, active_tab="password")

        login_user_session(conn, user, is_admin_session=(user["role"] in ADMIN_ROLES))
        merge_guest_cart_into_user(conn, int(user["id"]))
        flash("ورود با موفقیت انجام شد.", "success")
        return redirect(next_url)

    return render_template(
        "auth/login.html",
        next_url=next_url,
        active_tab=request.args.get("tab", "otp"),
        noindex=True,
    )


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("20/hour")
def register():
    if g.current_user:
        return redirect(url_for("account.dashboard"))

    conn = get_db()
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        mobile_raw = (request.form.get("mobile") or "").strip()
        password = request.form.get("password") or ""
        password_confirm = request.form.get("password_confirm") or ""

        if len(full_name) < 2:
            flash("نام و نام خانوادگی را به درستی وارد کنید.", "danger")
            return render_template("auth/register.html", form_data=request.form, noindex=True)

        if not email or "@" not in email:
            flash("ایمیل معتبر وارد کنید.", "danger")
            return render_template("auth/register.html", form_data=request.form, noindex=True)

        if get_user_by_email(conn, email):
            flash("این ایمیل قبلاً ثبت شده است.", "danger")
            return render_template("auth/register.html", form_data=request.form, noindex=True)

        mobile = None
        if mobile_raw:
            mobile = normalize_iran_mobile(mobile_raw)
            if not mobile:
                flash("شماره موبایل وارد شده معتبر نیست.", "danger")
                return render_template("auth/register.html", form_data=request.form, noindex=True)
            if get_user_by_mobile(conn, mobile):
                flash("این شماره موبایل قبلاً در سیستم ثبت شده است.", "danger")
                return render_template("auth/register.html", form_data=request.form, noindex=True)

        ok_pw, pw_msg = validate_password_policy(password)
        if not ok_pw:
            flash(pw_msg, "danger")
            return render_template("auth/register.html", form_data=request.form, noindex=True)
        if password != password_confirm:
            flash("تکرار رمز عبور مطابقت ندارد.", "danger")
            return render_template("auth/register.html", form_data=request.form, noindex=True)

        uid = create_user(
            conn,
            mobile=mobile,
            email=email,
            password_hash=hash_password(password),
            full_name=full_name,
            role="customer",
        )
        user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        login_user_session(conn, user)
        merge_guest_cart_into_user(conn, uid)
        flash("حساب کاربری شما با موفقیت ایجاد شد.", "success")
        return redirect(url_for("account.dashboard"))

    return render_template("auth/register.html", form_data={}, noindex=True)


@auth_bp.route("/otp/request", methods=["POST"])
@limiter.limit("6/minute;25/hour")
def otp_request():
    conn = get_db()
    mobile = request.form.get("mobile") or (request.json.get("mobile") if request.is_json else "")
    purpose = request.form.get("purpose") or (request.json.get("purpose") if request.is_json else "login")
    if purpose not in {"login", "checkout", "mobile_change"}:
        purpose = "login"
    res = request_otp(
        conn,
        raw_mobile=mobile or "",
        purpose=purpose,
        ip_address=request.remote_addr or "",
    )
    status_code = 200 if res.ok else 400
    payload = {
        "ok": res.ok,
        "message": res.message,
        "cooldown": res.cooldown_remaining,
    }
    if res.dev_code:
        payload["dev_code"] = res.dev_code
    return jsonify(payload), status_code


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user_session()
    flash("با موفقیت از حساب کاربری خارج شدید.", "info")
    return redirect(url_for("shop.home"))
