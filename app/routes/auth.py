"""احراز هویت: ورود با رمز، ورود/ثبت‌نام با OTP، تأیید موبایل و بازیابی رمز."""
from __future__ import annotations

import time

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.decorators import anonymous_required, login_required
from app.extensions import db
from app.models import User
from app.services import auth_service, otp_service, rate_limit_service, settings_service
from app.utils.http import client_ip, safe_next_url
from app.utils.security import is_valid_mobile, normalize_mobile

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

ALLOWED_PURPOSES = {"login", "reset_password", "verify_mobile"}


def _post_login_redirect(user: User, next_url: str | None = None) -> str:
    if next_url:
        return next_url
    if user.is_admin:
        return url_for("admin.dashboard")
    return url_for("account.dashboard")


def _otp_context() -> dict:
    return session.get("otp_context") or {}


def _clear_otp_context() -> None:
    session.pop("otp_context", None)


@auth_bp.route("/login", methods=["GET", "POST"])
@anonymous_required
def login():
    next_url = safe_next_url(default="")
    if request.method == "POST":
        mobile = request.form.get("mobile", "")
        password = request.form.get("password", "")
        user, error = auth_service.authenticate(mobile, password, client_ip())
        if user is not None:
            if user.must_change_password:
                flash("برای امنیت حساب، رمز عبور خود را تغییر دهید.", "warning")
                return redirect(url_for("account.password"))
            flash(f"خوش آمدید {user.display_name}.", "success")
            return redirect(_post_login_redirect(user, next_url))
        flash(error or "ورود انجام نشد.", "error")
        return render_template("auth/login.html", next=next_url, mobile=normalize_mobile(mobile))
    return render_template("auth/login.html", next=next_url, mobile="")


@auth_bp.route("/register", methods=["GET", "POST"])
@anonymous_required
def register():
    if not settings_service.get_bool("allow_registration", True):
        flash("ثبت‌نام در حال حاضر غیرفعال است.", "warning")
        return redirect(url_for("auth.login"))
    next_url = safe_next_url()
    if request.method == "POST":
        user, errors = auth_service.register_user(
            full_name=request.form.get("full_name", ""),
            mobile=request.form.get("mobile", ""),
            password=request.form.get("password", ""),
            password_confirm=request.form.get("password_confirm", ""),
            email=request.form.get("email", ""),
        )
        if user is not None:
            auth_service.login_user(user, client_ip())
            try:
                otp_service.send_code(user.mobile, "verify_mobile", client_ip())
                flash("حساب شما ساخته شد. کد تأیید به موبایل شما ارسال شد.", "success")
            except otp_service.OtpError as exc:
                flash(f"حساب شما ساخته شد، اما ارسال کد تأیید انجام نشد: {exc.message}", "warning")
            return redirect(url_for("auth.verify_mobile", next=next_url) if next_url else url_for("auth.verify_mobile"))
        for message in errors:
            flash(message, "error")
        return render_template(
            "auth/register.html",
            next=next_url,
            form=request.form,
        )
    return render_template("auth/register.html", next=next_url, form={})


@auth_bp.post("/logout")
def logout():
    if getattr(g, "user", None) is not None:
        auth_service.logout_user()
        flash("با موفقیت خارج شدید.", "success")
    return redirect(url_for("storefront.home"))


# --- ورود با کد یکبارمصرف ---

@auth_bp.get("/otp")
def otp_request_page():
    purpose = request.args.get("purpose", "login")
    if purpose not in ALLOWED_PURPOSES:
        purpose = "login"
    if getattr(g, "user", None) is not None and purpose == "login":
        return redirect(url_for("account.dashboard"))
    return render_template("auth/otp.html", purpose=purpose, next=safe_next_url())


@auth_bp.post("/otp/request")
def otp_request():
    purpose = request.form.get("purpose", "login")
    if purpose not in ALLOWED_PURPOSES:
        purpose = "login"
    mobile = normalize_mobile(request.form.get("mobile", ""))
    if purpose == "verify_mobile" and getattr(g, "user", None) is not None:
        mobile = g.user.mobile
    if not is_valid_mobile(mobile):
        flash("شماره موبایل معتبر وارد کنید (مثال: 09121234567).", "error")
        return redirect(url_for("auth.otp_request_page", purpose=purpose))

    if purpose == "reset_password":
        exists = db.session.query(User.id).filter(User.mobile == mobile).first()
        if exists is None:
            # پیام یکسان برای جلوگیری از افشای وجود حساب
            flash("اگر این شماره در سیستم ثبت شده باشد، کد بازیابی ارسال می‌شود.", "info")
            session["reset_mobile"] = mobile
            return redirect(url_for("auth.reset_password"))

    try:
        result = otp_service.send_code(mobile, purpose, client_ip())
    except otp_service.OtpError as exc:
        flash(exc.message, "error")
        return redirect(url_for("auth.otp_request_page", purpose=purpose))

    session["otp_context"] = {
        "mobile": mobile,
        "purpose": purpose,
        "next": safe_next_url(),
        "sent_at": int(time.time()),
    }
    message = "کد تأیید به شماره شما ارسال شد."
    if result.get("debug_code"):
        message += f" (کد آزمایشی: {result['debug_code']})"
    flash(message, "success")
    return redirect(url_for("auth.otp_code_page"))


@auth_bp.get("/otp/code")
def otp_code_page():
    context = _otp_context()
    if not context.get("mobile"):
        flash("لطفاً ابتدا شماره موبایل خود را وارد کنید.", "warning")
        return redirect(url_for("auth.otp_request_page"))
    return render_template(
        "auth/otp_code.html",
        mobile=context["mobile"],
        purpose=context.get("purpose", "login"),
        resend_seconds=settings_service.get_int("otp_resend_seconds", 90),
    )


@auth_bp.post("/otp/verify")
def otp_verify():
    context = _otp_context()
    mobile = context.get("mobile")
    purpose = context.get("purpose", "login")
    if not mobile:
        flash("نشست تأیید منقضی شده است. دوباره تلاش کنید.", "warning")
        return redirect(url_for("auth.otp_request_page"))

    code = request.form.get("code", "")
    throttle_key = f"otp_verify:{mobile}"
    allowed, _, retry_after = rate_limit_service.consume(throttle_key, 12, 600)
    if not allowed:
        flash(f"تلاش‌های ناموفق زیاد است. {max(1, retry_after // 60)} دقیقه دیگر تلاش کنید.", "error")
        return redirect(url_for("auth.otp_code_page"))

    ok, message = otp_service.verify_code(mobile, code, purpose)
    if not ok:
        flash(message, "error")
        return redirect(url_for("auth.otp_code_page"))

    _clear_otp_context()

    if purpose == "reset_password":
        session["reset_mobile"] = mobile
        flash("کد تأیید شد. رمز عبور جدید را وارد کنید.", "success")
        return redirect(url_for("auth.reset_password"))

    if purpose == "verify_mobile":
        if getattr(g, "user", None) is None:
            flash("برای تأیید شماره موبایل ابتدا وارد شوید.", "warning")
            return redirect(url_for("auth.login"))
        auth_service.mark_verified(g.user)
        flash("شماره موبایل شما با موفقیت تأیید شد.", "success")
        return redirect(context.get("next") or url_for("account.dashboard"))

    # ورود / ثبت‌نام با OTP
    user, error = auth_service.get_or_create_user_by_mobile(mobile)
    if user is None:
        flash(error or "ورود انجام نشد.", "error")
        return redirect(url_for("auth.login"))
    auth_service.login_user(user, client_ip())
    flash(f"خوش آمدید {user.display_name}.", "success")
    return redirect(_post_login_redirect(user, context.get("next")))


@auth_bp.post("/otp/resend")
def otp_resend():
    context = _otp_context()
    if not context.get("mobile"):
        return redirect(url_for("auth.otp_request_page"))
    try:
        result = otp_service.send_code(context["mobile"], context.get("purpose", "login"), client_ip())
        message = "کد جدید ارسال شد."
        if result.get("debug_code"):
            message += f" (کد آزمایشی: {result['debug_code']})"
        flash(message, "success")
    except otp_service.OtpError as exc:
        flash(exc.message, "error")
    return redirect(url_for("auth.otp_code_page"))


@auth_bp.get("/verify-mobile")
@login_required
def verify_mobile():
    if g.user.is_verified:
        flash("شماره موبایل شما قبلاً تأیید شده است.", "info")
        return redirect(url_for("account.dashboard"))
    return render_template("auth/verify_mobile.html", next=safe_next_url())


# --- بازیابی رمز عبور ---

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@anonymous_required
def forgot_password():
    if request.method == "POST":
        mobile = normalize_mobile(request.form.get("mobile", ""))
        if not is_valid_mobile(mobile):
            flash("شماره موبایل معتبر وارد کنید.", "error")
            return render_template("auth/forgot_password.html", mobile=mobile)
        exists = db.session.query(User.id).filter(User.mobile == mobile).first()
        if exists is not None:
            try:
                result = otp_service.send_code(mobile, "reset_password", client_ip())
                session["reset_mobile"] = mobile
                message = "کد بازیابی برای شما ارسال شد."
                if result.get("debug_code"):
                    message += f" (کد آزمایشی: {result['debug_code']})"
                flash(message, "success")
                return redirect(url_for("auth.reset_password"))
            except otp_service.OtpError as exc:
                flash(exc.message, "error")
                return render_template("auth/forgot_password.html", mobile=mobile)
        flash("اگر این شماره در سیستم ثبت شده باشد، کد بازیابی ارسال می‌شود.", "info")
        return redirect(url_for("auth.reset_password"))
    return render_template("auth/forgot_password.html", mobile="")


@auth_bp.route("/reset-password", methods=["GET", "POST"])
@anonymous_required
def reset_password():
    mobile = session.get("reset_mobile")
    if not mobile:
        flash("لطفاً ابتدا شماره موبایل خود را وارد کنید.", "warning")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        code = request.form.get("code", "")
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")
        ok, message = otp_service.verify_code(mobile, code, "reset_password")
        if not ok:
            flash(message, "error")
            return render_template("auth/reset_password.html", mobile=mobile)
        user = db.session.query(User).filter(User.mobile == mobile).first()
        if user is None:
            flash("حساب کاربری یافت نشد.", "error")
            return redirect(url_for("auth.forgot_password"))
        from app.utils.security import validate_password_strength

        errors = validate_password_strength(
            password, max(6, settings_service.get_int("password_min_length", 8))
        )
        if password != confirm:
            errors.append("تکرار رمز عبور مطابقت ندارد.")
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("auth/reset_password.html", mobile=mobile)
        auth_service.set_password(user, password)
        session.pop("reset_mobile", None)
        flash("رمز عبور شما تغییر کرد. اکنون وارد شوید.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", mobile=mobile)
